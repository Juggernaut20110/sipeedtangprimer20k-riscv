# FatFs application copy

This application-specific copy is based on the FatFs R0.14 source bundled with
the pinned LiteX dependency. The upstream copyright and redistribution notice
is retained in `ff.c` and `ff.h` (Copyright (C) 2019, ChaN). Project-specific
write support is selected in `ffconf.h`; the BIOS FatFs configuration remains
read-only. Formatting and exFAT are disabled in this application.
