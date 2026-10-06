/* Link-only integration probe. The fit study never calls this on hardware. */
#include <generated/soc.h>

#include <libliteeth/udp.h>
#include <liblitesdcard/sdcard.h>

#include <stdint.h>

int rtos_fit_peripheral_link_probe(void)
{
	static const uint8_t local_mac[6] = {0x02, 0x20, 0x20, 0x00, 0x00, 0x01};
	static uint8_t dma_buffer[512] __attribute__((aligned(4)));
	int result = sdcard_init();

	/* Link the real native-DMA APIs; this probe is retained but never called. */
	result += sdcard_read(0, 1, dma_buffer);
	result += sdcard_write(0, 1, dma_buffer);
	udp_start(local_mac, IPTOINT(192, 0, 2, 20));
	udp_service();
	(void)udp_get_tx_buffer();
	result += udp_send(49152, 7, 0);
	return result;
}
