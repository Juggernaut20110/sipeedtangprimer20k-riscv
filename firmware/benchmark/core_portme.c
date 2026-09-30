/*
 * Copyright 2018 Embedded Microprocessor Benchmark Consortium (EEMBC)
 * Licensed under the Apache License, Version 2.0.
 *
 * Tang Primer 20K port support. The CoreMark algorithm sources are kept
 * unchanged at the commit recorded in dependencies.lock.json.
 */
#include "coremark.h"
#include "core_portme.h"

#include <libbase/uart.h>
#include <system.h>
#include <generated/csr.h>

#define TIMING_INTERVAL_CAPACITY 32u

volatile ee_s32 seed1_volatile = BENCHMARK_SEED1;
volatile ee_s32 seed2_volatile = BENCHMARK_SEED2;
volatile ee_s32 seed3_volatile = BENCHMARK_SEED3;
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;
ee_u32 default_num_contexts = 1;

static CORE_TICKS start_ticks;
static CORE_TICKS elapsed_intervals[TIMING_INTERVAL_CAPACITY];
static ee_u32 elapsed_count;
static ee_u32 interval_overflow;

static CORE_TICKS uptime_cycles(void)
{
    timer0_uptime_latch_write(1);
    return timer0_uptime_cycles_read();
}

void start_time(void)
{
    /* Keep all startup/metadata UART traffic outside the measured interval.
     * uart_sync() drains the software-side polling path to the hardware FIFO;
     * TXEMPTY remains clear until the final byte has left the UART PHY. */
    uart_sync();
    while (!uart_txempty_read()) {
    }
    start_ticks = uptime_cycles();
}

void stop_time(void)
{
    CORE_TICKS stop_ticks = uptime_cycles();
    if (elapsed_count < TIMING_INTERVAL_CAPACITY) {
        elapsed_intervals[elapsed_count] = stop_ticks - start_ticks;
    } else {
        interval_overflow = 1;
    }
    elapsed_count++;
}

CORE_TICKS get_time(void)
{
    if (elapsed_count == 0) {
        return 0;
    }
    if (elapsed_count <= TIMING_INTERVAL_CAPACITY) {
        return elapsed_intervals[elapsed_count - 1u];
    }
    return 0;
}

unsigned int benchmark_elapsed_interval_count(void)
{
    return elapsed_count;
}

secs_ret time_in_secs(CORE_TICKS ticks)
{
    return (ee_u32)(ticks / (CORE_TICKS)BENCHMARK_CLOCK_HZ);
}

void portable_init(core_portable *portable, int *argc, char *argv[])
{
    (void)argc;
    (void)argv;

    /* Start each invocation from a coherent CPU cache state before upstream
     * initializes its static data. The cache maintenance is outside timing. */
    asm volatile("fence" ::: "memory");
    flush_cpu_dcache();
    asm volatile("fence" ::: "memory");
    flush_cpu_icache();
    asm volatile("fence" ::: "memory");

    if (sizeof(ee_u8) != 1 || sizeof(ee_s16) != 2 || sizeof(ee_u16) != 2 ||
        sizeof(ee_s32) != 4 || sizeof(ee_u32) != 4 || sizeof(ee_ptr_int) != sizeof(void *)) {
        ee_printf("BENCHMARK_PORT_ERROR invalid CoreMark data type sizes\n");
    }
    portable->portable_id = 1;
}

void portable_fini(core_portable *portable)
{
    portable->portable_id = 0;
}

static void print_ticks(const char *label, ee_u32 index, CORE_TICKS ticks)
{
    ee_printf("%s index=%lu hi=%08lx lo=%08lx\n", label,
        (unsigned long)index,
        (unsigned long)(ticks >> 32),
        (unsigned long)(ticks & 0xffffffffu));
}

int benchmark_report_timing(void)
{
    ee_u32 calibration_count = elapsed_count == 0 ? 0 : elapsed_count - 1u;
    ee_u32 stored_count = elapsed_count < TIMING_INTERVAL_CAPACITY
        ? elapsed_count : TIMING_INTERVAL_CAPACITY;
    ee_printf("BENCHMARK_INTERVAL_COUNT count=%lu overflow=%lu\n",
        (unsigned long)elapsed_count, (unsigned long)interval_overflow);
    ee_printf("BENCHMARK_CALIBRATION_COUNT count=%lu\n",
        (unsigned long)calibration_count);
    for (ee_u32 index = 0; index < calibration_count && index < stored_count; index++) {
        print_ticks("BENCHMARK_CALIBRATION_TICKS", index + 1u, elapsed_intervals[index]);
    }
    if (elapsed_count > 0 && elapsed_count <= TIMING_INTERVAL_CAPACITY) {
        print_ticks("BENCHMARK_SCORED_TICKS", elapsed_count, elapsed_intervals[elapsed_count - 1u]);
    } else {
        ee_printf("BENCHMARK_SCORED_TICKS unavailable=1\n");
    }

    return elapsed_count == 2u && interval_overflow == 0u &&
        elapsed_intervals[0] != 0 && elapsed_intervals[1] != 0;
}

void benchmark_port_error_halt(const char *message)
{
    ee_printf("BENCHMARK_PORT_ERROR %s\n", message);
    uart_sync();
    while (!uart_txempty_read()) {
    }
    for (;;) {
    }
}
