#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#include <stdint.h>
#include "generated/soc.h"

#define configUSE_PREEMPTION                    1
#define configUSE_TIME_SLICING                  1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 1
#define configCPU_CLOCK_HZ                      CONFIG_CLOCK_FREQUENCY
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    5
#define configMINIMAL_STACK_SIZE                128
#define configMAX_TASK_NAME_LEN                  12
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1
#define configSUPPORT_STATIC_ALLOCATION          1
#define configSUPPORT_DYNAMIC_ALLOCATION        0
#define configUSE_MUTEXES                        1
#define configUSE_RECURSIVE_MUTEXES              0
#define configUSE_COUNTING_SEMAPHORES            0
#define configUSE_QUEUE_SETS                     0
#define configUSE_TIMERS                         0
#define configCHECK_FOR_STACK_OVERFLOW           2
#define configUSE_MALLOC_FAILED_HOOK             0
#define configUSE_TICK_HOOK                      0
#define configUSE_IDLE_HOOK                      0
#define configUSE_TRACE_FACILITY                 0
#define configUSE_STATS_FORMATTING_FUNCTIONS     0
#define configUSE_CO_ROUTINES                    0
#define configUSE_APPLICATION_TASK_TAG            0
#define configENABLE_FPU                         0
#define configENABLE_VPU                         0
#define configISR_STACK_SIZE_WORDS               512
#define configMTIME_BASE_ADDRESS                 0
#define configMTIMECMP_BASE_ADDRESS              0
#define configASSERT(x) do { if (!(x)) { vApplicationAssert(__FILE__, __LINE__); } } while (0)

#define INCLUDE_vTaskDelay                       1
#define INCLUDE_vTaskDelayUntil                  1
#define INCLUDE_vTaskDelete                      0
#define INCLUDE_vTaskSuspend                     0
#define INCLUDE_xTaskGetSchedulerState           1

void vApplicationAssert(const char *file, unsigned long line);

#endif
