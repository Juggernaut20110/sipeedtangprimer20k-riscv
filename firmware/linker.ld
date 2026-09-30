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
    } > main_ram

    .rodata :
    {
        . = ALIGN(8);
        _frodata = .;
        *(.rodata .rodata.* .gnu.linkonce.r.*)
        *(.rodata1 .srodata .srodata.* .got .got.* .toc .toc.*)
        . = ALIGN(8);
        _erodata = .;
    } > main_ram

    .preinit_array :
    {
        . = ALIGN(8);
        PROVIDE_HIDDEN(__preinit_array_start = .);
        KEEP(*(SORT(.preinit_array.*)))
        KEEP(*(.preinit_array))
        PROVIDE_HIDDEN(__preinit_array_end = .);
    } > main_ram

    .init_array :
    {
        . = ALIGN(8);
        PROVIDE_HIDDEN(__init_array_start = .);
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array))
        KEEP(*(SORT(.ctors.*)))
        KEEP(*(.ctors))
        PROVIDE_HIDDEN(__init_array_end = .);
    } > main_ram

    .fini_array :
    {
        . = ALIGN(8);
        PROVIDE_HIDDEN(__fini_array_start = .);
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array))
        KEEP(*(SORT(.dtors.*)))
        KEEP(*(.dtors))
        PROVIDE_HIDDEN(__fini_array_end = .);
    } > main_ram

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
    } > sram AT > main_ram

    .bss (NOLOAD) :
    {
        . = ALIGN(8);
        _fbss = .;
        *(.dynsbss .sbss .sbss.* .gnu.linkonce.sb.* .scommon)
        *(.dynbss .bss .bss.* .gnu.linkonce.b.* COMMON)
        . = ALIGN(8);
        _ebss = .;
        _end = .;
    } > sram

    .stack ORIGIN(sram) + LENGTH(sram) - 2048 (NOLOAD) :
    {
        _stack_bottom = .;
        . = . + 2048;
        _stack_top = .;
    } > sram
}

PROVIDE(_fstack = ORIGIN(sram) + LENGTH(sram));
PROVIDE(_fdata_rom = LOADADDR(.data));
PROVIDE(_edata_rom = LOADADDR(.data) + SIZEOF(.data));

ASSERT(_end <= _stack_bottom, "firmware data/BSS overlaps the reserved 2 KiB stack")
ASSERT(LOADADDR(.data) + SIZEOF(.data) <= ORIGIN(main_ram) + LENGTH(main_ram), "firmware load image exceeds main RAM")
ASSERT(SIZEOF(.text) + SIZEOF(.rodata) + SIZEOF(.data) <= LENGTH(main_ram), "firmware image exceeds main RAM")
