/*
 * Tang Primer 20K CoreMark port.
 * EEMBC algorithm files remain unchanged; this file supplies platform types
 * and timing declarations for the permitted CoreMark port interface.
 */
#ifndef TANG20K_CORE_PORTME_H
#define TANG20K_CORE_PORTME_H

#include <stddef.h>
#include <stdint.h>

#include "benchmark_profile.h"

#define HAS_FLOAT 0
#define HAS_TIME_H 0
#define USE_CLOCK 0
#define HAS_STDIO 0
#define HAS_PRINTF 0

#define COMPILER_VERSION __VERSION__
#define COMPILER_FLAGS BENCHMARK_COMPILER_FLAGS
#define MEM_LOCATION "Code in main RAM, static data in on-chip SRAM"

typedef int16_t ee_s16;
typedef uint16_t ee_u16;
typedef int32_t ee_s32;
typedef double ee_f32;
typedef uint8_t ee_u8;
typedef uint32_t ee_u32;
typedef uintptr_t ee_ptr_int;
typedef size_t ee_size_t;

#define align_mem(x) ((void *)(4u + (((ee_ptr_int)(x) - 1u) & ~(ee_ptr_int)3u)))

#define CORETIMETYPE uint64_t
typedef uint64_t CORE_TICKS;

#define SEED_METHOD SEED_VOLATILE
#define MEM_METHOD MEM_STATIC
#define MULTITHREAD 1
#define MAIN_HAS_NOARGC 0
#define MAIN_HAS_NORETURN 0
#define TOTAL_DATA_SIZE 2000

typedef struct CORE_PORTABLE_S {
    ee_u8 portable_id;
} core_portable;

extern ee_u32 default_num_contexts;

void portable_init(core_portable *portable, int *argc, char *argv[]);
void portable_fini(core_portable *portable);
void start_time(void);
void stop_time(void);
CORE_TICKS get_time(void);
ee_u32 time_in_secs(CORE_TICKS ticks);
int benchmark_report_timing(void);
int ee_printf(const char *format, ...);

#endif
