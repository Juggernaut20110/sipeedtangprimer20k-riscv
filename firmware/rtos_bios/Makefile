# Study-local BIOS recipe. The pinned LiteX source is copied into each attempt
# and patched with patches/litex-rtos-bios-ddr-guard.patch before this recipe is
# installed there.
include ../include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

OBJECTS = crt0.o boot-helper.o helpers.o boot.o rtos_main.o
LSCRIPT = linker.ld
REGULAR_LIBS = $(filter-out $(ALWAYS_LINK_LIBS),$(LIBS))
COMMA = ,
ALWAYS_LINK_LIBFLAGS = $(if $(ALWAYS_LINK_LIBS),-Wl$(COMMA)--whole-archive $(ALWAYS_LINK_LIBS:lib%=-l%) -Wl$(COMMA)--no-whole-archive)

ifneq ($(BIOS_STACK_MARGIN),)
MEMUSAGE_ARGS += --fail-stack-margin $(BIOS_STACK_MARGIN)
endif
ifneq ($(BIOS_ROM_BUDGET),)
MEMUSAGE_ARGS += --max-rom $(BIOS_ROM_BUDGET)
endif
ifneq ($(BIOS_SRAM_BUDGET),)
MEMUSAGE_ARGS += --max-sram $(BIOS_SRAM_BUDGET)
endif

all: bios.bin
	$(PYTHON) -m litex.soc.software.memusage $(MEMUSAGE_ARGS) bios.elf $(CURDIR)/../include/generated/regions.ld $(TRIPLE)

bios.elf: $(BIOS_DIRECTORY)/$(LSCRIPT) $(OBJECTS) $(LIBS:%=%.a)
	$(CC) $(LDFLAGS) -T $(BIOS_DIRECTORY)/$(LSCRIPT) -N -o $@ \
		$(OBJECTS) $(PACKAGES:%=-L../%) -Wl,--start-group \
		$(REGULAR_LIBS:lib%=-l%) $(ALWAYS_LINK_LIBFLAGS) -Wl,--end-group \
		-Wl,--gc-sections -Wl,-Map,$@.map

vpath %.a $(PACKAGES:%=../%)

bios.bin: bios.elf
	$(OBJCOPY) -O binary $< $@
	chmod -x $@
	$(PYTHON) -m litex.soc.software.crcfbigen $@ --little

crt0.o: $(CPU_DIRECTORY)/crt0.S
	$(assemble)

boot-helper.o: $(CPU_DIRECTORY)/boot-helper.S
	$(assemble)

%.o: %.c
	$(compile)

%.o: %.S
	$(assemble)

VPATH = $(BIOS_DIRECTORY):$(CPU_DIRECTORY)
-include $(OBJECTS:.o=.d)

.PHONY: all
