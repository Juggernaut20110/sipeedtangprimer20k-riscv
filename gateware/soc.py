from pathlib import Path

from migen import Cat, Signal
from migen.fhdl.specials import READ_FIRST, WRITE_FIRST
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.cores.gpio import GPIOIn, GPIOOut
from litex.soc.integration.soc import SoCCore, SoCRegion
from litex_boards.targets import sipeed_tang_primer_20k
from gateware.profile_selection import accepted_maxperf_selection


PROFILES = {
    "minimal": "minimal",
    "lite": "lite",
    "standard": "standard",
    # Project-owned profile alias: BaseSoC is constructed with its supported
    # standard shell, then the generated dynamic_target RTL is installed below.
    "performance": "standard",
    # LiteX's pinned linux variant ships VexRiscv_Linux.v with MMU,
    # supervisor and LR/SC support.  The project uses it for bare-metal
    # GPIO/diagnostic/benchmark firmware; this does not imply an OS boot.
    "linux": "linux",
}
if accepted_maxperf_selection() is not None:
    PROFILES["maxperf"] = "standard"
SYS_CLK_FREQ = 48_000_000
DDR_CK_FREQ = 96_000_000
MEMORY_MODES = ("onchip", "ddr3")
DDR_SIZE_BYTES = 256 * 1024 * 1024
DDR_MAIN_RAM_BASE = 0x4000_0000
DDR_UNCACHED_BASE = 0xC000_0000
DDR_DIAGNOSTIC_BASE = 0x2000_0000
DDR_DIAGNOSTIC_SIZE = 16 * 1024
DDR_L2_SIZE = 8 * 1024
DDR_BIOS_SIZES = (32 * 1024, 40 * 1024, 48 * 1024)
UART_BAUDRATE = 115_200
LED_RESOURCES = tuple(range(6))
LED_LOGICAL_RESOURCE_ORDER = tuple(reversed(LED_RESOURCES))
# The Dock's C7/S4 switch is a dedicated FPGA reset/configuration input on the
# attached board revision. It resets the device when pressed, so only S0-S3 are
# usable as fabric GPIO even though the pinned platform lists btn_n4.
BUTTON_RESOURCES = tuple(range(4))


class ProjectSoC(sipeed_tang_primer_20k.BaseSoC):
    """Upstream board SoC with project-owned, ordered Dock GPIO CSRs."""

    def __init__(self, profile="minimal", memory="onchip", bios_size=None, cpu_rtl=None,
                 cpu_variant=None, **kwargs):
        provisional_maxperf = (profile == "maxperf" and profile not in PROFILES
                               and cpu_rtl is not None
                               and cpu_variant in ("projectim", "projectimc"))
        if profile not in PROFILES and not provisional_maxperf:
            raise ValueError(f"unknown CPU profile {profile!r}; choose from {', '.join(PROFILES)}")
        if memory not in MEMORY_MODES:
            raise ValueError(f"unknown memory mode {memory!r}; choose from {', '.join(MEMORY_MODES)}")

        ddr3 = memory == "ddr3"
        if profile == "performance" and cpu_rtl is None:
            raise ValueError("the performance profile requires its selected generated VexRiscv RTL")
        if bios_size is None:
            bios_size = 24 * 1024 if profile in ("performance", "linux") and not ddr3 else 32 * 1024
        if ddr3 and bios_size not in DDR_BIOS_SIZES:
            raise ValueError(f"DDR3 BIOS size must be one of {DDR_BIOS_SIZES}")
        if not ddr3 and bios_size != 32 * 1024:
            is_standard_cpu_candidate = (
                profile in ("standard", "maxperf") and cpu_rtl is not None and bios_size == 24 * 1024
            )
            is_performance_profile = profile == "performance" and cpu_rtl is not None and bios_size == 24 * 1024
            is_linux_profile = profile == "linux" and bios_size == 24 * 1024
            if not (is_standard_cpu_candidate or is_performance_profile or is_linux_profile):
                raise ValueError("on-chip public profiles keep their approved BIOS reservation")

        # An integrated main RAM size of zero is the upstream target's documented
        # switch for its board-specific GW2DDRPHY and IMD128M16R39CG8GNF path.
        super().__init__(
            dock="standard",
            sys_clk_freq=SYS_CLK_FREQ,
            cpu_type="vexriscv",
            cpu_variant=cpu_variant or PROFILES[profile],
            integrated_rom_size=bios_size,
            integrated_sram_size=8 * 1024,
            integrated_main_ram_size=0 if ddr3 else 32 * 1024,
            l2_size=DDR_L2_SIZE,
            uart_name="serial",
            uart_baudrate=UART_BAUDRATE,
            with_uart=True,
            with_timer=True,
            timer_uptime=True,
            with_led_chaser=False,
            with_buttons=False,
            with_lcd_backlight=False,
            with_rgb_led=False,
            with_ethernet=False,
            with_etherbone=False,
            with_video_terminal=False,
            with_lcd_terminal=False,
            with_lcd_colorbars=False,
            with_spi_flash=False,
            **kwargs,
        )
        if cpu_rtl is not None:
            rtl_path = str(cpu_rtl)
            if not Path(rtl_path).is_file():
                raise FileNotFoundError(f"project-owned VexRiscv RTL does not exist: {rtl_path}")
            self.cpu.use_external_variant(rtl_path)
            self.project_cpu_rtl = rtl_path
        else:
            self.project_cpu_rtl = None

        if ddr3:
            self._add_ddr3_aliases()
            # The DDR CRG creates sys from CLKDIV and preserves the board-level
            # synchronizer on that derived clock. Its source is the 96 MHz PLL
            # output which also clocks the DDR PHY; init remains on clk27.
            clk27 = self.platform.lookup_request("clk27")
            self.platform.add_generated_clock_constraint(
                self.crg.cd_sys2x_i.clk,
                clk27,
                divide_by=9,
                multiply_by=32,
                name="ddr_ck_96mhz",
                pin="rPLL/CLKOUT",
            )
            self.platform.add_generated_clock_constraint(
                self.crg.cd_sys2x.clk,
                self.crg.pll.clkouts[0].clk,
                name="sys2x_clk",
                pin="DHCEN/CLKOUT",
            )
            self.platform.add_generated_clock_constraint(
                self.crg.cd_sys.clk,
                self.crg.cd_sys2x.clk,
                divide_by=2,
                name="sys_clk",
                pin="CLKDIV/CLKOUT",
            )
            # LiteDRAM's pause flag crosses from clk27 into a two-flop
            # synchronizer in sys. Its stop/reset controls are synchronized in
            # GW2DDRPHYInit into the ungated sys2x_i domain before driving the
            # clock gate and PHY reset pins. Cut only these asynchronous source
            # to destination clock paths; all synchronous DDR and CPU paths
            # remain timed.
            self.platform.add_false_path_constraint(clk27, self.crg.cd_sys2x_i.clk)
            self.platform.add_false_path_constraint(clk27, self.crg.cd_sys.clk)
            # GW2DDRPHYInit deasserts reset_sys2x while DHCEN is holding
            # sys2x stopped, then waits eight clk27 cycles before releasing
            # stop. This controlled reset release cannot race the gated PHY
            # clocks. Limit the setup/recovery exception to the asynchronous
            # RESET pins of the DDR DQS and OSER4 primitives.
            self.platform.toolchain.additional_sdc_commands.append(
                "set_false_path -to [get_pins {DQS/RESET DQS_1/RESET OSER4/RESET OSER4_*/RESET}] -setup"
            )
        else:
            # The on-chip CRG creates sys directly from the PLL and has a
            # duplicate PLL-helper reset synchronizer. Keep the board CRG's
            # synchronizer and remove only the redundant one.
            pll_specials = self.crg.pll._fragment.specials
            pll_syncs = [
                special for special in pll_specials
                if isinstance(special, AsyncResetSynchronizer)
                and special.cd is self.crg.cd_sys
            ]
            if len(pll_syncs) != 1:
                raise RuntimeError(f"expected one redundant PLL reset synchronizer, found {len(pll_syncs)}")
            pll_specials.remove(pll_syncs[0])

        # LiteX creates the writable Wishbone RAMs with write-first ports. Gowin
        # V1.9.12.04 maps those to write-through SP block RAMs with WRE tied high
        # and the byte enables as the only write control. On the GW2A-18C, the
        # first bus read of a word after a byte-enabled write to it then returns
        # wrong data on the written byte lanes until the block reads another
        # address, although Gowin's simulation model reads back correctly. The
        # Wishbone SRAM never reads and writes in the same cycle, so read-first
        # ports are equivalent on the bus and infer read-before-write SP blocks
        # that read back correctly on the board.
        writable_rams = [self.sram]
        if not ddr3:
            writable_rams.append(self.main_ram)
        else:
            writable_rams.append(self.ddr_diagnostic_ram)
        for ram in writable_rams:
            ram_ports = ram.mem.ports
            if len(ram_ports) != 1 or ram_ports[0].mode != WRITE_FIRST:
                raise RuntimeError("expected one write-first port on each integrated RAM")
            ram_ports[0].mode = READ_FIRST

        self.profile = profile
        self.memory = memory
        self.bios_size = bios_size
        self.project_led_resource_order = LED_RESOURCES
        self.project_led_logical_resource_order = LED_LOGICAL_RESOURCE_ORDER
        self.project_button_resource_order = BUTTON_RESOURCES

        # Request each platform resource explicitly, then map the reversed upstream
        # pin order onto the board's printed LED0..LED5 logical bit order. Dock LEDs
        # sink current, so invert outputs while keeping the CSR active-high.
        led_resources = {i: self.platform.request("led", i) for i in LED_RESOURCES}
        led_pads = Cat(*(led_resources[i] for i in LED_LOGICAL_RESOURCE_ORDER))
        led_logical = Signal(len(LED_RESOURCES))
        self.leds = GPIOOut(pads=led_logical, reset=0)
        self.comb += led_pads.eq(~led_logical)

        # btn_n is active-low on the Dock. Inversion normalizes pressed to logical 1;
        # GPIOIn applies LiteX's two-stage MultiReg synchronizer before the CSR.
        button_pads_n = Cat(*(self.platform.request("btn_n", i) for i in BUTTON_RESOURCES))
        button_pressed = Signal(len(BUTTON_RESOURCES))
        self.comb += button_pressed.eq(~button_pads_n)
        self.buttons = GPIOIn(pads=button_pressed)

        self.add_constant("PROJECT_PROFILE", profile)
        self.add_constant("PROJECT_MEMORY_MODE", memory)
        self.add_constant("PROJECT_GPIO_LED_COUNT", len(LED_RESOURCES))
        self.add_constant("PROJECT_GPIO_BUTTON_COUNT", len(BUTTON_RESOURCES))

        if not ddr3:
            # The upstream platform also retains its 27 MHz input-clock constraint.
            self.platform.add_generated_clock_constraint(
                self.crg.cd_sys.clk,
                self.platform.lookup_request("clk27"),
                divide_by=9,
                multiply_by=16,
                name="sys_clk",
                pin="rPLL/CLKOUT",
            )

    def _add_ddr3_aliases(self):
        from litedram.frontend.wishbone import LiteDRAMWishbone2Native
        from litex.soc.interconnect import wishbone

        geom = self.sdram.controller.settings.geom
        rows = 1 << geom.rowbits
        columns = 1 << geom.colbits
        banks = 1 << geom.bankbits
        width_bytes = self.ddrphy.settings.databits // 8
        physical_size = banks * rows * columns * width_bytes
        if physical_size != DDR_SIZE_BYTES:
            raise RuntimeError(f"unexpected DDR3 geometry: {physical_size} bytes, expected {DDR_SIZE_BYTES}")
        # Diagnostic code, data, and stack are loaded here before any destructive
        # memory access. It remains available while the complete DDR range is tested.
        self.add_ram(
            name="ddr_diagnostic_ram",
            origin=DDR_DIAGNOSTIC_BASE,
            size=DDR_DIAGNOSTIC_SIZE,
        )

        # Normal CPU accesses at 0x40000000 pass through LiteDRAM's 8 KiB L2.
        # This second Wishbone/native bridge has its own controller port and maps
        # the same 256 MiB physical range into VexRiscv's uncached IO window.
        alias_bus = wishbone.Interface(data_width=32, address_width=32, addressing="word")
        alias_region = SoCRegion(
            origin=DDR_UNCACHED_BASE,
            size=DDR_SIZE_BYTES,
            mode="rwx",
            cached=False,
        )
        self.bus.add_slave("ddr_uncached", slave=alias_bus, region=alias_region)
        alias_port = self.sdram.crossbar.get_port()
        self.submodules.ddr_uncached_bridge = LiteDRAMWishbone2Native(
            wishbone=alias_bus,
            port=alias_port,
            base_address=DDR_UNCACHED_BASE,
        )
        self.add_constant("PROJECT_DDR_BYTES", DDR_SIZE_BYTES)
        self.add_constant("PROJECT_DDR_UNCACHED_BASE", DDR_UNCACHED_BASE)
        self.add_constant("PROJECT_DDR_DIAGNOSTIC_BASE", DDR_DIAGNOSTIC_BASE)
        self.add_constant("PROJECT_DDR_L2_BYTES", DDR_L2_SIZE)


def make_soc(profile="minimal", memory="onchip"):
    return ProjectSoC(profile=profile, memory=memory)
