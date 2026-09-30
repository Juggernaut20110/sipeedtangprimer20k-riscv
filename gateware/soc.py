from migen import Cat, Signal
from migen.fhdl.specials import READ_FIRST, WRITE_FIRST
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.cores.gpio import GPIOIn, GPIOOut
from litex.soc.integration.soc import SoCCore
from litex_boards.targets import sipeed_tang_primer_20k


PROFILES = {
    "minimal": "minimal",
    "lite": "lite",
    "standard": "standard",
}
SYS_CLK_FREQ = 48_000_000
UART_BAUDRATE = 115_200
LED_RESOURCES = tuple(range(6))
LED_LOGICAL_RESOURCE_ORDER = tuple(reversed(LED_RESOURCES))
# The Dock's C7/S4 switch is a dedicated FPGA reset/configuration input on the
# attached board revision. It resets the device when pressed, so only S0-S3 are
# usable as fabric GPIO even though the pinned platform lists btn_n4.
BUTTON_RESOURCES = tuple(range(4))


class ProjectSoC(sipeed_tang_primer_20k.BaseSoC):
    """Upstream board SoC with project-owned, ordered Dock GPIO CSRs."""

    def __init__(self, profile="minimal", **kwargs):
        if profile not in PROFILES:
            raise ValueError(f"unknown CPU profile {profile!r}; choose from {', '.join(PROFILES)}")

        # These sizes keep the upstream target on its existing clock/reset and Dock
        # platform paths while preventing the optional DDR3 block from being created.
        super().__init__(
            dock="standard",
            sys_clk_freq=SYS_CLK_FREQ,
            cpu_type="vexriscv",
            cpu_variant=PROFILES[profile],
            integrated_rom_size=32 * 1024,
            integrated_sram_size=8 * 1024,
            integrated_main_ram_size=32 * 1024,
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

        # This pinned LiteX-Boards CRG creates the system clock with the PLL's
        # default reset synchronizer, then adds its own reset synchronizer with
        # PLL-lock and SoC-reset handling. Gowin lowers both to drivers of the
        # same cd_sys.rst net. Keep the board CRG synchronizer and drop the
        # redundant PLL-helper one so Gowin sees one reset driver.
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
        for ram in (self.sram, self.main_ram):
            ram_ports = ram.mem.ports
            if len(ram_ports) != 1 or ram_ports[0].mode != WRITE_FIRST:
                raise RuntimeError("expected one write-first port on each integrated RAM")
            ram_ports[0].mode = READ_FIRST

        self.profile = profile
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
        self.add_constant("PROJECT_GPIO_LED_COUNT", len(LED_RESOURCES))
        self.add_constant("PROJECT_GPIO_BUTTON_COUNT", len(BUTTON_RESOURCES))

        # Constrain the PLL output explicitly at the application clock rate. The
        # upstream platform also retains its 27 MHz input-clock constraint.
        self.platform.add_generated_clock_constraint(
            self.crg.cd_sys.clk,
            self.platform.lookup_request("clk27"),
            divide_by=9,
            multiply_by=16,
            name="sys_clk",
            pin="rPLL/CLKOUT",
        )


def make_soc(profile="minimal"):
    return ProjectSoC(profile=profile)
