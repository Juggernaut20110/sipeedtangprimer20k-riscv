"""Board-owned DDR3 module selection, independent of the upstream board default."""

from litedram.modules import IMD128M16R39CG8GNF
from gateware.ddr_geometry import DDR_BANK_BITS, DDR_COLUMN_BITS, DDR_ROW_BITS


class H5TQ1G63EFR(IMD128M16R39CG8GNF):
    # SK hynix H5TQ1G63EFR: eight banks, 8192 rows, 1024 x16 columns = 128 MiB.
    # Keep the existing conservative timing envelope for this geometry-only
    # correction, including 160 ns tRFC. Do not infer a speed suffix from the
    # part prefix or silently change the proven receive/clock configuration.
    nbanks = 1 << DDR_BANK_BITS
    nrows = 1 << DDR_ROW_BITS
    ncols = 1 << DDR_COLUMN_BITS
