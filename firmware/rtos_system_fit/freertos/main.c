#include "FreeRTOS.h"
#include "task.h"

#include <generated/csr.h>
#include <generated/soc.h>
#include <irq.h>

#include <stdint.h>
#include <stdio.h>

#define APP_TASK_STACK_WORDS 768u /* 3072 bytes per application task */

extern void freertos_risc_v_trap_handler(void);

void vApplicationGetIdleTaskMemory(StaticTask_t **tcb, StackType_t **stack,
				   uint32_t *stack_depth);
void vApplicationStackOverflowHook(TaskHandle_t task, char *name);

static StaticTask_t counter_tcb;
static StaticTask_t status_tcb;
static StackType_t counter_stack[APP_TASK_STACK_WORDS];
static StackType_t status_stack[APP_TASK_STACK_WORDS];
static StaticTask_t idle_tcb;
static StackType_t idle_stack[configMINIMAL_STACK_SIZE];
static volatile uint32_t app_ticks;

void vApplicationAssert(const char *file, unsigned long line)
{
	printf("FreeRTOS assertion at %s:%lu\n", file, line);
	for (;;) {
		__asm__ volatile ("wfi");
	}
}

void vApplicationGetIdleTaskMemory(StaticTask_t **tcb, StackType_t **stack,
				   uint32_t *stack_depth)
{
	*tcb = &idle_tcb;
	*stack = idle_stack;
	*stack_depth = configMINIMAL_STACK_SIZE;
}

void vApplicationStackOverflowHook(TaskHandle_t task, char *name)
{
	(void)task;
	printf("FreeRTOS stack overflow: %s\n", name);
	for (;;) {
		__asm__ volatile ("wfi");
	}
}

void vPortSetupTimerInterrupt(void)
{
	const uint32_t reload = configCPU_CLOCK_HZ / configTICK_RATE_HZ;

	irq_setmask(1u << TIMER0_INTERRUPT);
	timer0_en_write(0);
	timer0_reload_write(reload);
	timer0_load_write(reload);
	timer0_ev_pending_write(1);
	timer0_ev_enable_write(1);
	timer0_en_write(1);
	__asm__ volatile ("csrw mie, %0" :: "r"(1u << 11) : "memory");
}

void freertos_risc_v_application_interrupt_handler(void)
{
	uint32_t cause;
	uint32_t pending;

	__asm__ volatile ("csrr %0, mcause" : "=r"(cause));
	if ((cause & 0x7fffffffu) != 11u) {
		vApplicationAssert("unexpected machine interrupt", cause);
		return;
	}

	pending = irq_pending() & irq_getmask();
	if ((pending & (1u << TIMER0_INTERRUPT)) != 0u) {
		timer0_ev_pending_write(1);
		app_ticks++;
		if (xTaskIncrementTick() != pdFALSE) {
			vTaskSwitchContext();
		}
	}
}

static void counter_task(void *argument)
{
	uint32_t count = 0;
	(void)argument;
	for (;;) {
		printf("FreeRTOS counter=%lu tick=%lu\n",
		       (unsigned long)++count, (unsigned long)app_ticks);
		vTaskDelay(pdMS_TO_TICKS(1000));
	}
}

static void status_task(void *argument)
{
	(void)argument;
	for (;;) {
		printf("FreeRTOS second task alive\n");
		vTaskDelay(pdMS_TO_TICKS(2000));
	}
}

int main(void)
{
	TaskHandle_t counter_handle;
	TaskHandle_t status_handle;
	uintptr_t trap = (uintptr_t)freertos_risc_v_trap_handler;

	__asm__ volatile ("csrw mtvec, %0" :: "r"(trap) : "memory");
	__asm__ volatile ("csrc mstatus, %0" :: "r"(8u) : "memory");
	printf("FreeRTOS LiteX RV32IM, 1 kHz tick, DDR main RAM\n");

	counter_handle = xTaskCreateStatic(counter_task, "counter", APP_TASK_STACK_WORDS,
					   NULL, 2, counter_stack, &counter_tcb);
	status_handle = xTaskCreateStatic(status_task, "status", APP_TASK_STACK_WORDS,
					  NULL, 1, status_stack, &status_tcb);
	configASSERT(counter_handle != NULL);
	configASSERT(status_handle != NULL);
	vTaskStartScheduler();

	printf("FreeRTOS scheduler returned unexpectedly\n");
	for (;;) {
		__asm__ volatile ("wfi");
	}
}
