"""Isolated native-SD Linux fit-study SoC.

The registered project profiles continue to use :class:`ProjectSoC` defaults.
This target reuses the board DDR/RMII integration while omitting the destructive
DDR diagnostic RAM and uncached alias that are useful to hardware tests.
"""

from gateware.soc import DDR_SIZE_BYTES, ProjectSoC, SYS_CLK_FREQ


class SystemFitSoC(ProjectSoC):
    """Linux-capable DDR3 system with optional native SD and RMII Ethernet."""

    def __init__(self, *, cpu_rtl, l2_size, working_sram_size,
                 ethernet="rmii", ethernet_rx_slots=1, ethernet_tx_slots=1,
                 native_sd=True, bios_size=32 * 1024):
        super().__init__(
            profile="linux",
            memory="ddr3",
            bios_size=bios_size,
            cpu_rtl=cpu_rtl,
            cpu_variant="linux",
            sdcard="none",
            ethernet=ethernet,
            l2_size=l2_size,
            working_sram_size=working_sram_size,
            ddr_diagnostics=False,
            ethernet_rx_slots=ethernet_rx_slots,
            ethernet_tx_slots=ethernet_tx_slots,
            # Gowin preserves this fitter's DDR PHY as a named module instead
            # of flattening it into the top level. SDC/CST instance paths must
            # therefore include the real top-level instance name.
            ddr_constraint_scope="ddrphy/",
        )

        for name in ("SDCARD_BOOT_DISABLE", "NET_BOOT_DISABLE", "BIOS_NO_ETHERNET_INIT"):
            if name not in self.constants:
                self.add_constant(name)
        self.add_constant("PROJECT_SDCARD_NATIVE", int(native_sd))

        if native_sd:
            # LiteX add_sdcard requests the board's native four-bit sdcard pads,
            # constructs SDPHY/SDCore and retains both Wishbone DMA masters.
            self.add_sdcard(name="sdcard", mode="read+write")
            self._validate_native_sd()

        self._validate_fit_memory()

    def _validate_fit_memory(self):
        if self.sys_clk_freq != SYS_CLK_FREQ:
            raise RuntimeError(f"system clock is {self.sys_clk_freq} Hz, expected {SYS_CLK_FREQ} Hz")
        if self.ddrphy.settings.nphases != 2 or self.ddrphy.settings.databits != 16:
            raise RuntimeError("DDR PHY does not match the fitted x16, 1:2 DDR interface")
        if not self.ddrphy.settings.dll_off or (self.ddrphy.settings.cl, self.ddrphy.settings.cwl) != (6, 6):
            raise RuntimeError("DDR PHY lost the existing 96 MHz DLL-off CL6/CWL6 configuration")
        if self.sdram.controller.settings.geom.bankbits != 3:
            raise RuntimeError("DDR controller does not expose the expected eight banks")
        main_ram = self.bus.regions.get("main_ram")
        if main_ram is None or main_ram.size != DDR_SIZE_BYTES or not main_ram.cached:
            raise RuntimeError("128 MiB cached main DDR region is missing")
        if "ddr_diagnostic_ram" in self.bus.regions or "ddr_uncached" in self.bus.regions:
            raise RuntimeError("fit-study target unexpectedly contains DDR diagnostic memory")
        if self.ddr_diagnostics:
            raise RuntimeError("fit-study target must not enable DDR diagnostic memory")

    def _validate_native_sd(self):
        required_masters = {"sdcard_block2mem", "sdcard_mem2block"}
        masters = set(getattr(self.bus, "masters", {}))
        missing = required_masters - masters
        if missing:
            raise RuntimeError(f"native SD DMA masters are missing: {sorted(missing)}")
        if not hasattr(self, "sdcard") or not hasattr(self.sdcard, "ev"):
            raise RuntimeError("native SD core or interrupt event manager is missing")
        for event in ("card_detect", "block2mem_dma", "mem2block_dma"):
            if not hasattr(self.sdcard.ev, event):
                raise RuntimeError(f"native SD interrupt source {event!r} is missing")
