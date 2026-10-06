#include <stdint.h>
#include <stdio.h>

#include <generated/mem.h>
#include <libbase/uart.h>
#include <liblitedram/sdram.h>

#include "boot.h"

/* boot.c consults this before accepting DDR loads or handing off an image. */
int rtos_bios_ddr_ready;

static int ddr_smoke_check(void)
{
	static const uint32_t patterns[] = {
		0x13579bdf, 0x2468ace0, 0x55aa33cc, 0xa55ac33c,
	};
	volatile uint32_t *memory = (volatile uint32_t *)MAIN_RAM_BASE;
	unsigned int i;

	for (i = 0; i < sizeof(patterns) / sizeof(patterns[0]); i++)
		memory[i] = patterns[i];
	for (i = 0; i < sizeof(patterns) / sizeof(patterns[0]); i++) {
		if (memory[i] != patterns[i]) {
			printf("DDR smoke check failed at 0x%08lx: read 0x%08lx, expected 0x%08lx\n",
			       (unsigned long)&memory[i], (unsigned long)memory[i],
			       (unsigned long)patterns[i]);
			return 0;
		}
	}
	return 1;
}

int main(void)
{
	int ddr_ok;

	uart_init();
	printf("RTOS fit BIOS: initializing DDR3\n");
	ddr_ok = sdram_init() == 1;
	if (ddr_ok)
		ddr_ok = ddr_smoke_check();
	if (!ddr_ok) {
		printf("DDR initialization or smoke check failed; staying in serial loader\n");
		rtos_bios_ddr_ready = 0;
	} else {
		printf("DDR initialization and bounded smoke check passed\n");
		rtos_bios_ddr_ready = 1;
	}

	for (;;) {
		(void)serialboot();
	}
}
