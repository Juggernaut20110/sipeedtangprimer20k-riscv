ROOT ?= $(abspath ..)
BUILD_DIR ?= $(ROOT)/build/minimal
OUTPUT_DIR ?= $(BUILD_DIR)/firmware
PROFILE ?= minimal

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

OBJECTS = crt0.o main.o app_logic.o
COMMA = ,
REGULAR_LIBS = $(filter-out $(ALWAYS_LINK_LIBS),$(LIBS))
ALWAYS_LINK_LIBFLAGS = $(if $(ALWAYS_LINK_LIBS),-Wl$(COMMA)--whole-archive $(ALWAYS_LINK_LIBS:lib%=-l%) -Wl$(COMMA)--no-whole-archive)
CFLAGS += -I$(ROOT)/firmware -I$(OUTPUT_DIR)

all: $(OUTPUT_DIR)/demo.bin

$(OUTPUT_DIR)/demo.elf: $(OBJECTS) $(ROOT)/firmware/linker.ld
	$(CC) $(LDFLAGS) -T $(ROOT)/firmware/linker.ld -N -o $@ $(OBJECTS) \
		$(PACKAGES:%=-L$(BUILD_DIR)/software/%) -Wl,--start-group \
		$(REGULAR_LIBS:lib%=-l%) $(ALWAYS_LINK_LIBFLAGS) -Wl,--end-group \
		-Wl,--gc-sections -Wl,-Map,$@.map

$(OUTPUT_DIR)/demo.bin: $(OUTPUT_DIR)/demo.elf
	$(OBJCOPY) -O binary $< $@
	chmod -x $@

crt0.o: $(CPU_DIRECTORY)/crt0.S
	$(assemble)

main.o: $(ROOT)/firmware/main.c $(OUTPUT_DIR)/profile.h
	$(compile)

app_logic.o: $(ROOT)/firmware/app_logic.c $(ROOT)/firmware/app_logic.h
	$(compile)

clean:
	$(RM) $(OBJECTS) $(OUTPUT_DIR)/demo.elf $(OUTPUT_DIR)/demo.bin $(OUTPUT_DIR)/demo.elf.map

.PHONY: all clean
