"""Project-owned constants shared by optional peripheral integrations."""

SYS_CLK_HZ = 48_000_000
SD_SPI_INIT_MAX_HZ = 400_000
SD_SPI_TRANSFER_MAX_HZ = 12_000_000
RMII_REFERENCE_HZ = 50_000_000
ETH_RX_SLOTS = 2
ETH_TX_SLOTS = 2
ETH_SLOT_BYTES = 2048
ETH_STANDARD_FRAME_BYTES = 1518


def spi_divider_at_most(sys_hz, target_hz):
    """Return the smallest legal SPI divider that cannot exceed target_hz."""
    if not isinstance(sys_hz, int) or not isinstance(target_hz, int):
        raise TypeError("clock frequencies must be integers in Hz")
    if sys_hz <= 0 or target_hz <= 0:
        raise ValueError("clock frequencies must be positive")
    return min(65_535, max(2, (sys_hz + target_hz - 1) // target_hz))


def spi_actual_hz(sys_hz, divider):
    if not isinstance(sys_hz, int) or not isinstance(divider, int):
        raise TypeError("clock and divider must be integers")
    if sys_hz <= 0 or not 2 <= divider <= 65_535:
        raise ValueError("SPI requires a positive clock and a divider from 2 through 65535")
    return sys_hz // divider
