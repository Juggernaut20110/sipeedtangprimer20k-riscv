"""Isolated machine-mode DDR3 SoC for bare-metal and RTOS fit work."""

from gateware.ddr_geometry import DDR_SIZE_BYTES
from gateware.peripherals import ETH_SLOT_BYTES
from gateware.soc import ProjectSoC, SYS_CLK_FREQ


class RtosSystemFitSoC(ProjectSoC):
    """Fitted DDR/RMII/native-SD system without diagnostic DDR windows."""

    def __init__(self, *, cpu_variant, cpu_rtl=None, l2_size=2048,
                 working_sram_size=4096, ethernet="rmii",
                 ethernet_rx_slots=1, ethernet_tx_slots=1,
                 native_sd=True, bios_size=16 * 1024):
        if cpu_variant not in ("lite", "minimal"):
            raise ValueError("RTOS fit CPU variant must be 'lite' or 'minimal'")
        super().__init__(
            profile=cpu_variant,
            memory="ddr3",
            bios_size=bios_size,
            cpu_rtl=cpu_rtl,
            cpu_variant=cpu_variant,
            sdcard="none",
            ethernet=ethernet,
            l2_size=l2_size,
            working_sram_size=working_sram_size,
            ddr_diagnostics=False,
            ethernet_rx_slots=ethernet_rx_slots,
            ethernet_tx_slots=ethernet_tx_slots,
            ddr_constraint_scope="ddrphy/",
            include_project_gpio=False,
            allow_custom_ddr_bios_size=True,
        )
        for name in ("SDCARD_BOOT_DISABLE", "NET_BOOT_DISABLE", "BIOS_NO_ETHERNET_INIT"):
            if name not in self.constants:
                self.add_constant(name)
        self.add_constant("PROJECT_RTO_SYSTEM_FIT", 1)
        self.add_constant("PROJECT_SDCARD_NATIVE", int(native_sd))
        if native_sd:
            self.add_sdcard(name="sdcard", mode="read+write")
            self._validate_native_sd()
        self._validate_fit_memory()

    def _validate_fit_memory(self):
        main_ram = self.bus.regions.get("main_ram")
        if self.sys_clk_freq != SYS_CLK_FREQ:
            raise RuntimeError(f"system clock is {self.sys_clk_freq} Hz, expected {SYS_CLK_FREQ} Hz")
        if self.ddrphy.settings.nphases != 2 or self.ddrphy.settings.databits != 16:
            raise RuntimeError("DDR PHY does not match the fitted x16, 1:2 DDR interface")
        if not self.ddrphy.settings.dll_off or (self.ddrphy.settings.cl, self.ddrphy.settings.cwl) != (6, 6):
            raise RuntimeError("DDR PHY lost the existing 96 MHz DLL-off CL6/CWL6 configuration")
        if self.sdram.controller.settings.geom.bankbits != 3:
            raise RuntimeError("DDR controller does not expose the expected eight banks")
        if main_ram is None or main_ram.size != DDR_SIZE_BYTES or not main_ram.cached:
            raise RuntimeError("128 MiB cached main DDR region is missing")
        if "ddr_diagnostic_ram" in self.bus.regions or "ddr_uncached" in self.bus.regions:
            raise RuntimeError("RTOS fit target unexpectedly contains DDR diagnostic memory")

    def _validate_native_sd(self):
        required_masters = {"sdcard_block2mem", "sdcard_mem2block"}
        missing = required_masters - set(getattr(self.bus, "masters", {}))
        if missing:
            raise RuntimeError(f"native SD DMA masters are missing: {sorted(missing)}")
        if not hasattr(self, "sdcard") or not hasattr(self.sdcard, "ev"):
            raise RuntimeError("native SD core or interrupt event manager is missing")
        for event in ("card_detect", "block2mem_dma", "mem2block_dma"):
            if not hasattr(self.sdcard.ev, event):
                raise RuntimeError(f"native SD interrupt source {event!r} is missing")
        if hasattr(self, "ethmac"):
            if self.ethmac.slot_size.constant != ETH_SLOT_BYTES:
                raise RuntimeError("LiteEth MAC slot size changed")
            if (self.ethmac.rx_slots.constant, self.ethmac.tx_slots.constant) != (1, 1):
                raise RuntimeError("RTOS target requires one RX and one TX Ethernet slot")
