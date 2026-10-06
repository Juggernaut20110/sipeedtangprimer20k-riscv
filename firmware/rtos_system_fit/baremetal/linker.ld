INCLUDE generated/output_format.ld
INCLUDE generated/regions.ld
ENTRY(_start)

__DYNAMIC = 0;

SECTIONS
{
	.text :
	{
		_ftext = .;
		/* Preserve the LiteX reset/trap entry before application code. */
		KEEP(*crt0*(.text))
		*(.text .stub .text.* .gnu.linkonce.t.*)
		KEEP(*(.text.isr))
		_etext = .;
	} > main_ram

	.rodata :
	{
		. = ALIGN(8);
		_frodata = .;
		*(.rodata .rodata.* .gnu.linkonce.r.*)
		*(.srodata .srodata.*)
		*(.got .got.*)
		*(.toc .toc.*)
		FILL(0)
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
		*(.data .data.* .gnu.linkonce.d.*)
		*(.data1)
		_gp = ALIGN(16);
		*(.sdata .sdata.* .gnu.linkonce.s.*)
		. = ALIGN(8);
		_edata = .;
	} > main_ram AT > main_ram

	.bss (NOLOAD) :
	{
		. = ALIGN(8);
		_fbss = .;
		*(.dynsbss)
		*(.sbss .sbss.* .gnu.linkonce.sb.*)
		*(.scommon)
		*(.dynbss)
		*(.bss .bss.* .gnu.linkonce.b.*)
		*(COMMON)
		. = ALIGN(8);
		_ebss = .;
		_end = .;
	} > main_ram

	/DISCARD/ : { *(.eh_frame) *(.comment) }
}

PROVIDE(_fstack = ORIGIN(main_ram) + LENGTH(main_ram));
PROVIDE(_fdata_rom = LOADADDR(.data));
PROVIDE(_edata_rom = LOADADDR(.data) + SIZEOF(.data));
