ROOT ?= $(abspath ..)
BUILD_DIR ?= $(ROOT)/build/minimal
OUTPUT_DIR ?= $(BUILD_DIR)/firmware
PROFILE ?= minimal
LINKER_SCRIPT ?= $(ROOT)/firmware/linker.ld

include $(BUILD_DIR)/software/include/generated/variables.mak
include $(SOC_DIRECTORY)/software/common.mak

OBJECTS = crt0.o main.o app_logic.o memcmp.o
ifeq ($(PERIPHERAL_DIAGNOSTIC),1)
OBJECTS += peripherals/peripheral_diag.o
else
ifeq ($(SDCARD),spi)
OBJECTS += peripherals/sd_protocol.o peripherals/sd_spi.o peripherals/sdcard_app.o \
	peripherals/fatfs/ff.o peripherals/fatfs/ffunicode.o
CFLAGS += -I$(ROOT)/firmware/peripherals
endif
endif
ifeq ($(ETHERNET),rmii)
ifeq ($(PERIPHERAL_DIAGNOSTIC),1)
else
OBJECTS += peripherals/ethernet_lwip.o peripherals/lwip_sys.o \
	peripherals/lwip/core/init.o peripherals/lwip/core/def.o \
	peripherals/lwip/core/inet_chksum.o peripherals/lwip/core/ip.o \
	peripherals/lwip/core/mem.o peripherals/lwip/core/memp.o \
	peripherals/lwip/core/netif.o peripherals/lwip/core/pbuf.o \
	peripherals/lwip/core/raw.o peripherals/lwip/core/stats.o \
	peripherals/lwip/core/sys.o peripherals/lwip/core/timeouts.o \
	peripherals/lwip/netif/ethernet.o \
	peripherals/lwip/core/udp.o peripherals/lwip/core/tcp.o \
	peripherals/lwip/core/tcp_in.o peripherals/lwip/core/tcp_out.o \
	peripherals/lwip/core/ipv4/acd.o peripherals/lwip/core/ipv4/autoip.o peripherals/lwip/core/ipv4/dhcp.o \
	peripherals/lwip/core/ipv4/etharp.o peripherals/lwip/core/ipv4/icmp.o \
	peripherals/lwip/core/ipv4/igmp.o peripherals/lwip/core/ipv4/ip4.o \
	peripherals/lwip/core/ipv4/ip4_addr.o peripherals/lwip/core/ipv4/ip4_frag.o
CFLAGS += -I$(ROOT)/firmware/peripherals -I$(ROOT)/firmware/peripherals/lwip -I$(ROOT)/.deps/lwip/src/include
endif
endif
COMMA = ,
REGULAR_LIBS = $(filter-out $(ALWAYS_LINK_LIBS),$(LIBS))
ALWAYS_LINK_LIBFLAGS = $(if $(ALWAYS_LINK_LIBS),-Wl$(COMMA)--whole-archive $(ALWAYS_LINK_LIBS:lib%=-l%) -Wl$(COMMA)--no-whole-archive)
CFLAGS += -I$(ROOT)/firmware -I$(OUTPUT_DIR)

all: $(OUTPUT_DIR)/demo.bin

$(OUTPUT_DIR)/demo.elf: $(OBJECTS) $(LINKER_SCRIPT)
	$(CC) $(LDFLAGS) -T $(LINKER_SCRIPT) -N -o $@ $(OBJECTS) \
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

memcmp.o: $(ROOT)/firmware/memcmp.c
	$(compile)

peripherals/sd_protocol.o: $(ROOT)/firmware/peripherals/sd_protocol.c $(ROOT)/firmware/peripherals/sd_protocol.h | $(OUTPUT_DIR)/peripherals
	$(compile)

peripherals/sd_spi.o: $(ROOT)/firmware/peripherals/sd_spi.c $(ROOT)/firmware/peripherals/sd_spi.h $(ROOT)/firmware/peripherals/sd_protocol.h | $(OUTPUT_DIR)/peripherals
	$(compile)

peripherals/sdcard_app.o: $(ROOT)/firmware/peripherals/sdcard_app.c $(ROOT)/firmware/peripherals/sdcard_app.h $(ROOT)/firmware/peripherals/fatfs/ff.h | $(OUTPUT_DIR)/peripherals
	$(compile)

peripherals/peripheral_diag.o: $(ROOT)/firmware/peripherals/peripheral_diag.c | $(OUTPUT_DIR)/peripherals
	$(compile)

peripherals/ethernet_lwip.o: $(ROOT)/firmware/peripherals/ethernet_lwip.c $(ROOT)/firmware/peripherals/ethernet_lwip.h $(ROOT)/firmware/peripherals/lwipopts.h $(OUTPUT_DIR)/profile.h | $(OUTPUT_DIR)/peripherals
	$(compile)

peripherals/lwip_sys.o: $(ROOT)/firmware/peripherals/lwip_sys.c $(ROOT)/firmware/peripherals/lwip_sys.h | $(OUTPUT_DIR)/peripherals
	$(compile)

peripherals/fatfs/%.o: $(ROOT)/firmware/peripherals/fatfs/%.c | $(OUTPUT_DIR)/peripherals/fatfs
	$(compile)

peripherals/lwip/%.o: $(ROOT)/.deps/lwip/src/%.c | $(OUTPUT_DIR)/peripherals/lwip
	mkdir -p $(dir $@)
	$(compile)

$(OUTPUT_DIR)/peripherals $(OUTPUT_DIR)/peripherals/fatfs $(OUTPUT_DIR)/peripherals/lwip:
	mkdir -p $@

clean:
	$(RM) $(OBJECTS) $(OUTPUT_DIR)/demo.elf $(OUTPUT_DIR)/demo.bin $(OUTPUT_DIR)/demo.elf.map
	rm -rf $(OUTPUT_DIR)/peripherals

.PHONY: all clean

# Include compiler-generated header dependencies (including ffconf.h ABI changes).
-include $(OBJECTS:.o=.d)
