#include <generated/csr.h>
#include <generated/soc.h>
#include <irq.h>

#include <stdint.h>
#include <stdio.h>

static volatile uint32_t system_ticks;

static void timer0_isr(void)
{
	timer0_ev_pending_write(1);
	system_ticks++;
}

static void timer_init(void)
{
	irq_attach(TIMER0_INTERRUPT, timer0_isr);
	irq_setmask(1u << TIMER0_INTERRUPT);
	timer0_en_write(0);
	timer0_reload_write(CONFIG_CLOCK_FREQUENCY / 1000u);
	timer0_load_write(CONFIG_CLOCK_FREQUENCY / 1000u);
	timer0_ev_pending_write(1);
	timer0_ev_enable_write(1);
	timer0_en_write(1);
	irq_setie(1);
}

int main(void)
{
	uint32_t last_reported = 0;

	printf("Bare-metal DDR hello: RV32IM, %u Hz\n", CONFIG_CLOCK_FREQUENCY);
	timer_init();
	for (;;) {
		uint32_t now = system_ticks;
		if (now - last_reported >= 1000u) {
			last_reported += 1000u;
			printf("counter=%lu ticks=%lu\n",
			       (unsigned long)(last_reported / 1000u), (unsigned long)now);
		}
	}
}
