#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

#include <stdint.h>

#if defined(CONFIG_ETH_LITEX_LITEETH) && defined(CONFIG_SDHC_LITEX_LITESDCARD)
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/net/net_if.h>

#define SDHC_NODE DT_ALIAS(sdhc0)
BUILD_ASSERT(DT_NODE_EXISTS(SDHC_NODE), "generated LiteX SDHC alias is missing");
#endif

static struct k_timer heartbeat;
static volatile uint32_t beat_count;

static void heartbeat_expiry(struct k_timer *timer)
{
	ARG_UNUSED(timer);
	beat_count++;
}

static void worker(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);
	for (;;) {
		k_sleep(K_SECONDS(2));
		printk("Zephyr worker alive, ticks=%u\n", k_uptime_ticks());
	}
}

K_THREAD_STACK_DEFINE(worker_stack, 2048);
static struct k_thread worker_thread;

int main(void)
{
	k_timer_init(&heartbeat, heartbeat_expiry, NULL);
	k_timer_start(&heartbeat, K_SECONDS(1), K_SECONDS(1));
	k_thread_create(&worker_thread, worker_stack, K_THREAD_STACK_SIZEOF(worker_stack),
			worker, NULL, NULL, NULL, 2, 0, K_NO_WAIT);
	printk("Zephyr LiteX RV32IM hello, 48 MHz system timer\n");

#if defined(CONFIG_ETH_LITEX_LITEETH) && defined(CONFIG_SDHC_LITEX_LITESDCARD)
	const struct device *sdhc = DEVICE_DT_GET(SDHC_NODE);
	printk("SDHC device ready=%u; LiteEth interface=%p\n",
	       device_is_ready(sdhc), (void *)net_if_get_default());
#endif

	for (;;) {
		k_sleep(K_SECONDS(1));
		printk("heartbeat=%u\n", beat_count);
	}
}
