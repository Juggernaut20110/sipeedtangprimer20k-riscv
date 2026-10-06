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
        *(.rodata .rodata.* .gnu.linkonce.r.* .rodata1)
        *(.srodata .srodata.* .got .got.* .toc .toc.*)
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
    } > main_ram AT > main_ram

    .bss (NOLOAD) :
    {
        . = ALIGN(8);
        _fbss = .;
        *(.dynsbss .sbss .sbss.* .gnu.linkonce.sb.* .scommon)
        *(.dynbss .bss .bss.* .gnu.linkonce.b.* COMMON)
        . = ALIGN(8);
        _ebss = .;
        _end = .;
    } > main_ram

    .stack ORIGIN(main_ram) + LENGTH(main_ram) - 16384 (NOLOAD) :
    {
        _stack_bottom = .;
        . = . + 16384;
        _stack_top = .;
    } > main_ram
}

PROVIDE(_fstack = ORIGIN(main_ram) + LENGTH(main_ram));
PROVIDE(_fdata_rom = LOADADDR(.data));
PROVIDE(_edata_rom = LOADADDR(.data) + SIZEOF(.data));
PROVIDE(__heap_start = _end);
PROVIDE(__heap_end = _stack_bottom);

ASSERT(_end <= _stack_bottom, "peripheral application data/BSS overlaps its DDR stack")
ASSERT(LOADADDR(.data) + SIZEOF(.data) <= ORIGIN(main_ram) + LENGTH(main_ram), "peripheral firmware load image exceeds DDR")
ASSERT(SIZEOF(.text) + SIZEOF(.rodata) + SIZEOF(.data) <= LENGTH(main_ram), "peripheral firmware image exceeds DDR")
