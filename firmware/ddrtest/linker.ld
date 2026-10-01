INCLUDE generated/output_format.ld
ENTRY(_start)
__DYNAMIC = 0;
INCLUDE generated/regions.ld

SECTIONS
{
    .text :
    {
        _ftext = .;
        *crt0*(.text)
        KEEP(*crt0*(.text))
        KEEP(*(.text.isr))
        *(.text .stub .text.* .gnu.linkonce.t.*)
        _etext = .;
    } > ddr_diagnostic_ram

    .rodata :
    {
        . = ALIGN(8);
        _frodata = .;
        *(.rodata .rodata.* .gnu.linkonce.r.*)
        *(.rodata1 .srodata .srodata.* .got .got.* .toc .toc.*)
        __preinit_array_start = .;
        KEEP(*(.preinit_array .preinit_array.*))
        __preinit_array_end = .;
        __init_array_start = .;
        KEEP(*(.init_array .init_array.*))
        __init_array_end = .;
        KEEP(*(.fini_array .fini_array.*))
        . = ALIGN(8);
        _erodata = .;
    } > ddr_diagnostic_ram

    .data :
    {
        . = ALIGN(8);
        _fdata = .;
        *(.data .data.* .gnu.linkonce.d.* .data1)
        . = ALIGN(16);
        _gp = .;
        *(.sdata .sdata.* .gnu.linkonce.s.*)
        . = ALIGN(8);
        _edata = .;
    } > ddr_diagnostic_ram AT > ddr_diagnostic_ram

    .bss (NOLOAD) :
    {
        . = ALIGN(8);
        _fbss = .;
        *(.dynsbss .sbss .sbss.* .gnu.linkonce.sb.* .scommon)
        *(.dynbss .bss .bss.* .gnu.linkonce.b.* COMMON)
        . = ALIGN(8);
        _ebss = .;
        _end = .;
    } > ddr_diagnostic_ram

    .stack ORIGIN(ddr_diagnostic_ram) + LENGTH(ddr_diagnostic_ram) - 2048 (NOLOAD) :
    {
        _stack_bottom = .;
        . = . + 2048;
        _stack_top = .;
    } > ddr_diagnostic_ram
}

PROVIDE(_fstack = ORIGIN(ddr_diagnostic_ram) + LENGTH(ddr_diagnostic_ram));
PROVIDE(_fdata_rom = LOADADDR(.data));
PROVIDE(_edata_rom = LOADADDR(.data) + SIZEOF(.data));

ASSERT(_end <= _stack_bottom, "DDR diagnostic BSS overlaps its reserved 2 KiB stack")
ASSERT(LOADADDR(.data) + SIZEOF(.data) <= ORIGIN(ddr_diagnostic_ram) + LENGTH(ddr_diagnostic_ram), "DDR diagnostic load image exceeds its 16 KiB RAM")
ASSERT(SIZEOF(.text) + SIZEOF(.rodata) + SIZEOF(.data) <= LENGTH(ddr_diagnostic_ram), "DDR diagnostic image exceeds its 16 KiB RAM")
