/*
 * Copyright 2026. SPDX-License-Identifier: BSD-2-Clause
 * Thin board integration wrapper around the unchanged upstream CoreMark main.
 */
#include "coremark.h"
#include "core_portme.h"
#include "benchmark_port.h"

#include <irq.h>
#include <libbase/uart.h>
#include <system.h>
#include <stdint.h>

#ifndef BENCHMARK_DCACHE_BYTES
#define BENCHMARK_DCACHE_BYTES 0u
#endif
#ifndef BENCHMARK_L2_BYTES
#define BENCHMARK_L2_BYTES 0u
#endif

#if BENCHMARK_DCACHE_BYTES > 0
#define BENCHMARK_CODE_CONFLICT_PHASE "code_conflict,"
#else
#define BENCHMARK_CODE_CONFLICT_PHASE ""
#endif

extern int coremark_main(int argc, char *argv[]);
extern volatile ee_s32 seed4_volatile;
extern volatile ee_s32 seed1_volatile;
extern volatile ee_s32 seed2_volatile;
extern volatile ee_s32 seed3_volatile;
extern const uint8_t _ftext[];
extern const uint8_t _edata_rom[];

typedef union {
    uint32_t word;
    uint16_t halfword[2];
    int16_t signed_halfword[2];
    uint8_t byte[4];
    int8_t signed_byte[4];
} ram_check_word;

static volatile ram_check_word ram_check[64] __attribute__((aligned(32)));

typedef struct {
    const char *phase;
    uintptr_t address;
    uint32_t expected;
    uint32_t actual;
    uint32_t failmask;
    unsigned int errors;
} ram_check_failure;

static void flush_test_caches(void)
{
    asm volatile("fence" ::: "memory");
    flush_cpu_dcache();
    asm volatile("fence" ::: "memory");
#if BENCHMARK_L2_BYTES > 0
    flush_l2_cache();
    asm volatile("fence" ::: "memory");
    flush_cpu_dcache();
    asm volatile("fence" ::: "memory");
#endif
    flush_cpu_icache();
    asm volatile("fence" ::: "memory");
}

static uint32_t ram_word_pattern(uint32_t base, unsigned int index)
{
    return base ^ ((uint32_t)index * 0x01010101u);
}

static void record_ram_check(ram_check_failure *failure, const char *phase,
    uintptr_t address, uint32_t expected, uint32_t actual)
{
    if (expected != actual) {
        if (failure->errors == 0) {
            failure->phase = phase;
            failure->address = address;
            failure->expected = expected;
            failure->actual = actual;
        }
        failure->failmask |= 0x80000000u;
        failure->errors++;
    }
}

static void record_ram_check_batch(ram_check_failure *failure, const char *phase,
    uintptr_t address, uint32_t expected, uint32_t actual, uint32_t failmask,
    unsigned int errors)
{
    if (errors == 0u) {
        return;
    }
    if (failure->errors == 0u) {
        failure->phase = phase;
        failure->address = address;
        failure->expected = expected;
        failure->actual = actual;
    }
    failure->failmask |= failmask;
    failure->errors += errors;
}

static uintptr_t run_tight_store_checks(ram_check_failure *failure)
{
    uint32_t failmask = 0;
    unsigned int errors = 0;
    const char *first_phase = "";
    uintptr_t first_address = 0;
    uint32_t first_expected = 0;
    uint32_t first_actual = 0;
    uintptr_t code_conflict_address = 0;

#define TIGHT_CHECK(bit_, phase_, address_, expected_, actual_) do { \
        uint32_t check_expected = (uint32_t)(expected_); \
        uint32_t check_actual = (uint32_t)(actual_); \
        if (check_expected != check_actual) { \
            if (errors == 0u) { \
                first_phase = (phase_); \
                first_address = (uintptr_t)(address_); \
                first_expected = check_expected; \
                first_actual = check_actual; \
            } \
            failmask |= (bit_); \
            errors++; \
        } \
    } while (0)

    /* Two adjacent halfword stores followed immediately by halfword and word
     * reads. The repeated low-half stores preserve a fixed upper-half CRC. */
    for (unsigned int index = 0; index < 64u; index++) {
        volatile uint16_t *half = ram_check[index].halfword;
        volatile uint32_t *word = &ram_check[index].word;
        uint16_t fixed_high = (uint16_t)(0x5ac3u ^ (index * 257u));
        *word = ((uint32_t)fixed_high << 16) | 0x1937u;
        for (unsigned int iteration = 0; iteration < 32u; iteration++) {
            uint16_t low_a = (uint16_t)(0x2001u + index + iteration * 17u);
            uint16_t low_b = (uint16_t)(0xa501u ^ (index * 31u + iteration));
            half[1] = fixed_high;
            half[0] = low_a;
            half[0] = low_b;
            TIGHT_CHECK(0x00000001u, "tight_sh_lh_low", &half[0],
                low_b, half[0]);
            TIGHT_CHECK(0x00000002u, "tight_sh_lh_neighbor", &half[1],
                fixed_high, half[1]);
            TIGHT_CHECK(0x00000004u, "tight_sh_lw_word", word,
                ((uint32_t)fixed_high << 16) | low_b, *word);

            /* Exercise a load/modify/store to the same halfword while the
             * neighboring halfword remains fixed. */
            half[0] = (uint16_t)(half[0] + 1u);
            uint16_t incremented = (uint16_t)(low_b + 1u);
            TIGHT_CHECK(0x00000008u, "tight_lh_sh_rmw", &half[0],
                incremented, half[0]);
            TIGHT_CHECK(0x00000010u, "tight_lh_sh_neighbor", &half[1],
                fixed_high, half[1]);
            TIGHT_CHECK(0x00000020u, "tight_lh_sh_word", word,
                ((uint32_t)fixed_high << 16) | incremented, *word);

            /* Back-to-back stores to both halves, then loads from each. */
            uint16_t pair_low = (uint16_t)(0x4100u + iteration + index);
            uint16_t pair_high = (uint16_t)(0xc200u ^ (iteration * 19u + index));
            half[0] = pair_low;
            half[1] = pair_high;
            TIGHT_CHECK(0x00000040u, "tight_sh_pair_lh_low", &half[0],
                pair_low, half[0]);
            TIGHT_CHECK(0x00000080u, "tight_sh_pair_lh_high", &half[1],
                pair_high, half[1]);
            TIGHT_CHECK(0x00000100u, "tight_sh_pair_lw", word,
                ((uint32_t)pair_high << 16) | pair_low, *word);
        }
    }

    /* Repeat byte stores to one lane, increment through a byte load/store,
     * and verify neighboring lanes after every operation. */
    for (unsigned int index = 0; index < 64u; index++) {
        volatile uint8_t *byte = ram_check[index].byte;
        volatile uint32_t *word = &ram_check[index].word;
        uint32_t expected_word = ram_word_pattern(0x6dbca597u, index);
        *word = expected_word;
        for (unsigned int iteration = 0; iteration < 32u; iteration++) {
            for (unsigned int lane = 0; lane < 4u; lane++) {
                uint32_t shift = lane * 8u;
                uint32_t mask = 0xffu << shift;
                uint8_t first = (uint8_t)(iteration + lane * 37u);
                uint8_t final = (uint8_t)(0x91u ^ (iteration * 11u + lane));
                byte[lane] = first;
                byte[lane] = final;
                expected_word = (expected_word & ~mask) | ((uint32_t)final << shift);
                TIGHT_CHECK(0x00000200u, "tight_sb_lb_lane", &byte[lane],
                    final, byte[lane]);
                TIGHT_CHECK(0x00000400u, "tight_sb_neighbor_bytes", word,
                    expected_word, *word);

                byte[lane] = (uint8_t)(byte[lane] + 1u);
                uint8_t incremented = (uint8_t)(final + 1u);
                expected_word = (expected_word & ~mask) |
                    ((uint32_t)incremented << shift);
                TIGHT_CHECK(0x00000800u, "tight_lb_sb_rmw", &byte[lane],
                    incremented, byte[lane]);
                TIGHT_CHECK(0x00001000u, "tight_lb_sb_neighbors", word,
                    expected_word, *word);
            }

            /* Consecutive SB stores to every lane followed by every LB. */
            uint8_t lane0 = (uint8_t)(0x11u + iteration);
            uint8_t lane1 = (uint8_t)(0x52u + iteration);
            uint8_t lane2 = (uint8_t)(0x93u + iteration);
            uint8_t lane3 = (uint8_t)(0xd4u + iteration);
            byte[0] = lane0;
            byte[1] = lane1;
            byte[2] = lane2;
            byte[3] = lane3;
            uint32_t pair_expected = (uint32_t)lane0 |
                ((uint32_t)lane1 << 8) | ((uint32_t)lane2 << 16) |
                ((uint32_t)lane3 << 24);
            TIGHT_CHECK(0x00002000u, "tight_sb_pair_lb_0", &byte[0], lane0, byte[0]);
            TIGHT_CHECK(0x00004000u, "tight_sb_pair_lb_1", &byte[1], lane1, byte[1]);
            TIGHT_CHECK(0x00008000u, "tight_sb_pair_lb_2", &byte[2], lane2, byte[2]);
            TIGHT_CHECK(0x00010000u, "tight_sb_pair_lb_3", &byte[3], lane3, byte[3]);
            TIGHT_CHECK(0x00020000u, "tight_sb_pair_lw", word, pair_expected, *word);
            expected_word = pair_expected;
        }
    }

    /* Force conflicts using this CPU's actual D-cache index span. The
     * project-generated data caches are direct-mapped, so equal modulo-size
     * addresses contend for one line for each candidate capacity. */
#if BENCHMARK_DCACHE_BYTES > 0
    uintptr_t sram_address = (uintptr_t)&ram_check[0].word;
    uintptr_t image_start = (uintptr_t)_ftext;
    uintptr_t image_end = (uintptr_t)_edata_rom;
    uintptr_t cache_index_mask = (uintptr_t)BENCHMARK_DCACHE_BYTES - 1u;
    uintptr_t code_offset = (sram_address - image_start) & cache_index_mask;
    code_conflict_address = image_start + code_offset;
    if (code_conflict_address + sizeof(uint32_t) > image_end ||
        (((code_conflict_address ^ sram_address) & cache_index_mask) != 0u)) {
        if (errors == 0u) {
            first_phase = "tight_code_conflict_bounds";
            first_address = code_conflict_address;
            first_expected = (uint32_t)sram_address & (uint32_t)cache_index_mask;
            first_actual = (uint32_t)code_conflict_address & (uint32_t)cache_index_mask;
        }
        failmask |= 0x00040000u;
        errors++;
    } else {
        volatile uint32_t *sram_word = &ram_check[0].word;
        volatile uint16_t *sram_half = ram_check[0].halfword;
        const volatile uint32_t *code_word =
            (const volatile uint32_t *)code_conflict_address;
        uint32_t code_expected = *code_word;

        for (unsigned int iteration = 0; iteration < 128u; iteration++) {
            uint16_t low_a = (uint16_t)(0x1201u + iteration * 13u);
            uint16_t low_b = (uint16_t)(0x8a01u ^ (iteration * 29u));
            uint16_t high = (uint16_t)(0x43d7u ^ iteration);
            uint32_t expected_word = ((uint32_t)high << 16) | low_b;

            *sram_word = ((uint32_t)high << 16) | 0x7654u;
            uint32_t code_before = *code_word;
            sram_half[0] = low_a;
            sram_half[0] = low_b;
            uint32_t code_after = *code_word;
            uint16_t sram_low_after = sram_half[0];
            uint32_t sram_word_after = *sram_word;

            TIGHT_CHECK(0x00040000u, "tight_code_conflict_read", code_word,
                code_expected, code_before);
            TIGHT_CHECK(0x00040000u, "tight_code_conflict_refill", code_word,
                code_expected, code_after);
            TIGHT_CHECK(0x00080000u, "tight_sram_conflict_lh", sram_half,
                low_b, sram_low_after);
            TIGHT_CHECK(0x00080000u, "tight_sram_conflict_lw", sram_word,
                expected_word, sram_word_after);
        }
    }
#endif

#undef TIGHT_CHECK
    record_ram_check_batch(failure, first_phase, first_address, first_expected,
        first_actual, failmask, errors);
    return code_conflict_address;
}

static void run_ram_preflight(void)
{
    static const uint32_t word_patterns[2] = { 0x13579bdfu, 0x2468ace0u };
    static const uint16_t halfword_patterns[4] = {
        0x0000u, 0x7fffu, 0x8000u, 0xffffu,
    };
    static const uint8_t byte_patterns[4] = { 0x00u, 0x7fu, 0x80u, 0xffu };
    static const char *byte_phases[4] = {
        "byte_lane_0", "byte_lane_1", "byte_lane_2", "byte_lane_3",
    };
    ram_check_failure failure = { 0, 0, 0, 0, 0, 0 };

    for (unsigned int pattern = 0; pattern < 2u; pattern++) {
        for (unsigned int index = 0; index < 64u; index++) {
            ram_check[index].word = ram_word_pattern(word_patterns[pattern], index);
        }
        for (unsigned int index = 0; index < 64u; index++) {
            uint32_t expected = ram_word_pattern(word_patterns[pattern], index);
            record_ram_check(&failure,
                pattern == 0u ? "word_cached_0" : "word_cached_1",
                (uintptr_t)&ram_check[index].word, expected, ram_check[index].word);
        }
        flush_test_caches();
        for (unsigned int index = 0; index < 64u; index++) {
            uint32_t expected = ram_word_pattern(word_patterns[pattern], index);
            record_ram_check(&failure,
                pattern == 0u ? "word_flushed_0" : "word_flushed_1",
                (uintptr_t)&ram_check[index].word, expected, ram_check[index].word);
        }
    }

    for (unsigned int index = 0; index < 64u; index++) {
        uint32_t initial = ram_word_pattern(0xa5c3693cu, index);
        uint16_t low = halfword_patterns[index % 4u];
        uint16_t high = halfword_patterns[(index + 1u) % 4u];
        uint32_t expected = initial;
        ram_check[index].word = initial;
        ram_check[index].halfword[0] = low;
        expected = (expected & 0xffff0000u) | (uint32_t)low;
        record_ram_check(&failure, "halfword_low_cached",
            (uintptr_t)&ram_check[index].halfword[0], expected, ram_check[index].word);
        record_ram_check(&failure, "halfword_u16_low_cached",
            (uintptr_t)&ram_check[index].halfword[0], (uint32_t)low,
            (uint32_t)ram_check[index].halfword[0]);
        record_ram_check(&failure, "halfword_s16_low_cached",
            (uintptr_t)&ram_check[index].signed_halfword[0],
            (uint32_t)(int32_t)(int16_t)low,
            (uint32_t)(int32_t)ram_check[index].signed_halfword[0]);
        ram_check[index].halfword[1] = high;
        expected = (expected & 0x0000ffffu) | ((uint32_t)high << 16);
        record_ram_check(&failure, "halfword_high_cached",
            (uintptr_t)&ram_check[index].halfword[1], expected, ram_check[index].word);
        record_ram_check(&failure, "halfword_u16_high_cached",
            (uintptr_t)&ram_check[index].halfword[1], (uint32_t)high,
            (uint32_t)ram_check[index].halfword[1]);
        record_ram_check(&failure, "halfword_s16_high_cached",
            (uintptr_t)&ram_check[index].signed_halfword[1],
            (uint32_t)(int32_t)(int16_t)high,
            (uint32_t)(int32_t)ram_check[index].signed_halfword[1]);
    }
    flush_test_caches();
    for (unsigned int index = 0; index < 64u; index++) {
        uint16_t low = halfword_patterns[index % 4u];
        uint16_t high = halfword_patterns[(index + 1u) % 4u];
        uint32_t expected = (uint32_t)low | ((uint32_t)high << 16);
        record_ram_check(&failure, "halfword_flushed",
            (uintptr_t)&ram_check[index].word, expected, ram_check[index].word);
        record_ram_check(&failure, "halfword_u16_low_flushed",
            (uintptr_t)&ram_check[index].halfword[0], (uint32_t)low,
            (uint32_t)ram_check[index].halfword[0]);
        record_ram_check(&failure, "halfword_s16_low_flushed",
            (uintptr_t)&ram_check[index].signed_halfword[0],
            (uint32_t)(int32_t)(int16_t)low,
            (uint32_t)(int32_t)ram_check[index].signed_halfword[0]);
        record_ram_check(&failure, "halfword_u16_high_flushed",
            (uintptr_t)&ram_check[index].halfword[1], (uint32_t)high,
            (uint32_t)ram_check[index].halfword[1]);
        record_ram_check(&failure, "halfword_s16_high_flushed",
            (uintptr_t)&ram_check[index].signed_halfword[1],
            (uint32_t)(int32_t)(int16_t)high,
            (uint32_t)(int32_t)ram_check[index].signed_halfword[1]);
    }

    for (unsigned int index = 0; index < 64u; index++) {
        uint32_t initial = ram_word_pattern(0x3c69a5f0u, index);
        uint32_t expected = initial;
        ram_check[index].word = initial;
        for (unsigned int lane = 0; lane < 4u; lane++) {
            uint8_t value = byte_patterns[(index + lane) % 4u];
            uint32_t mask = 0xffu << (lane * 8u);
            ram_check[index].byte[lane] = value;
            expected = (expected & ~mask) | ((uint32_t)value << (lane * 8u));
            record_ram_check(&failure, byte_phases[lane],
                (uintptr_t)&ram_check[index].byte[lane], expected, ram_check[index].word);
            record_ram_check(&failure, "byte_u8_cached",
                (uintptr_t)&ram_check[index].byte[lane], (uint32_t)value,
                (uint32_t)ram_check[index].byte[lane]);
            record_ram_check(&failure, "byte_s8_cached",
                (uintptr_t)&ram_check[index].signed_byte[lane],
                (uint32_t)(int32_t)(int8_t)value,
                (uint32_t)(int32_t)ram_check[index].signed_byte[lane]);
        }
    }
    flush_test_caches();
    for (unsigned int index = 0; index < 64u; index++) {
        uint32_t expected = ram_word_pattern(0x3c69a5f0u, index);
        for (unsigned int lane = 0; lane < 4u; lane++) {
            uint8_t value = byte_patterns[(index + lane) % 4u];
            uint32_t mask = 0xffu << (lane * 8u);
            expected = (expected & ~mask) | ((uint32_t)value << (lane * 8u));
            record_ram_check(&failure, "byte_u8_flushed",
                (uintptr_t)&ram_check[index].byte[lane], (uint32_t)value,
                (uint32_t)ram_check[index].byte[lane]);
            record_ram_check(&failure, "byte_s8_flushed",
                (uintptr_t)&ram_check[index].signed_byte[lane],
                (uint32_t)(int32_t)(int8_t)value,
                (uint32_t)(int32_t)ram_check[index].signed_byte[lane]);
        }
        record_ram_check(&failure, "byte_flushed",
            (uintptr_t)&ram_check[index].word, expected, ram_check[index].word);
    }

    uintptr_t code_conflict_address = run_tight_store_checks(&failure);
    flush_test_caches();
    for (unsigned int index = 0; index < 64u; index++) {
        /* A post-flush word read also checks that the final stores reached the
         * visible SRAM state without a stale cache-line value. */
        uint32_t expected = 0xf3b27130u;
#if BENCHMARK_DCACHE_BYTES > 0
        if (index == 0u) {
            expected = ((uint32_t)(uint16_t)(0x43d7u ^ 127u) << 16) |
                (uint16_t)(0x8a01u ^ (127u * 29u));
        }
#endif
        uint32_t actual = ram_check[index].word;
        record_ram_check(&failure, "tight_stores_flushed",
            (uintptr_t)&ram_check[index].word, expected, actual);
    }

    if (failure.errors != 0u) {
        ee_printf("BENCHMARK_RAM_CHECK status=failed phase=%s firstaddr=%08lx "
            "expected=%08lx actual=%08lx errors=%u failmask=%08lx bytes=256\n",
            failure.phase, (unsigned long)failure.address,
            (unsigned long)failure.expected, (unsigned long)failure.actual,
            failure.errors, (unsigned long)failure.failmask);
        benchmark_port_error_halt("on-chip SRAM preflight failed");
    }
    ee_printf("BENCHMARK_RAM_CHECK status=passed words=64 bytes=256 "
        "alignment=32 phases=word,halfword_u16_s16,byte_u8_s8,tight_sh_lh_rmw,"
        "tight_sb_lb_rmw," BENCHMARK_CODE_CONFLICT_PHASE "cache_flushed errors=0 "
        "code_conflict_address=%08lx\n", (unsigned long)code_conflict_address);
}

static void verify_runtime_seeds(const char *phase, ee_s32 expected_iterations)
{
    ee_printf("BENCHMARK_RUNTIME_SEEDS phase=%s seed1=%ld seed2=%ld seed3=%ld seed4=%ld\n",
        phase, (long)seed1_volatile, (long)seed2_volatile,
        (long)seed3_volatile, (long)seed4_volatile);
    if (seed1_volatile != (ee_s32)BENCHMARK_SEED1 ||
        seed2_volatile != (ee_s32)BENCHMARK_SEED2 ||
        seed3_volatile != (ee_s32)BENCHMARK_SEED3 ||
        seed4_volatile != expected_iterations) {
        benchmark_port_error_halt("runtime CoreMark volatile seed mismatch");
    }
}

static uint32_t loaded_image_crc32(size_t *image_size)
{
    const uint8_t *cursor = _ftext;
    uintptr_t start = (uintptr_t)_ftext;
    uintptr_t end = (uintptr_t)_edata_rom;
    uint32_t crc = 0xffffffffu;

    if (end <= start) {
        *image_size = 0;
        return 0;
    }
    *image_size = (size_t)(end - start);
    while ((uintptr_t)cursor < end) {
        crc ^= *cursor++;
        for (unsigned int bit = 0; bit < 8u; bit++) {
            uint32_t mask = 0u - (crc & 1u);
            crc = (crc >> 1) ^ (0xedb88320u & mask);
        }
    }
    return crc ^ 0xffffffffu;
}

static void verify_loaded_image(const char *phase, uint32_t expected_crc,
    size_t expected_size)
{
    size_t cached_size;
    uint32_t cached_crc = loaded_image_crc32(&cached_size);
    flush_test_caches();
    size_t flushed_size;
    uint32_t flushed_crc = loaded_image_crc32(&flushed_size);

    ee_printf("BENCHMARK_IMAGE_RECHECK phase=%s cached=%08lx flushed=%08lx "
        "expected=%08lx size=%lu cached_size=%lu flushed_size=%lu\n",
        phase, (unsigned long)cached_crc, (unsigned long)flushed_crc,
        (unsigned long)expected_crc, (unsigned long)expected_size,
        (unsigned long)cached_size, (unsigned long)flushed_size);
    if (cached_crc != expected_crc || flushed_crc != expected_crc ||
        cached_size != expected_size || flushed_size != expected_size) {
        benchmark_port_error_halt("linked image changed during CoreMark run");
    }
}

static uint64_t derive_scored_iterations(CORE_TICKS calibration_ticks)
{
    uint64_t numerator = (uint64_t)BENCHMARK_CALIBRATION_ITERATIONS *
        (uint64_t)BENCHMARK_TARGET_SECONDS * (uint64_t)BENCHMARK_CLOCK_HZ;
    uint64_t quotient = numerator / calibration_ticks;

    if ((numerator % calibration_ticks) != 0) {
        quotient++;
    }
    return quotient;
}

int main(void)
{
    irq_setmask(0);
    irq_setie(0);
    uart_init();

    size_t image_size;
    uint32_t image_crc32 = loaded_image_crc32(&image_size);
    if (image_size == 0) {
        benchmark_port_error_halt("invalid linked firmware image bounds");
    }
    ee_printf("BENCHMARK_IMAGE_CRC32 build_id=%s value=%08lx size=%lu\n",
        BENCHMARK_BUILD_ID, (unsigned long)image_crc32, (unsigned long)image_size);
    run_ram_preflight();

    ee_printf("BENCHMARK_CALIBRATION_START profile=%s build_id=%s mode=%s "
        "method=fixed_iterations iterations=%u target_seconds=%u clock_hz=%lu "
        "data_size=%u contexts=1 seed1=%u seed2=%u seed3=0x%02x\n",
        BENCHMARK_PROFILE, BENCHMARK_BUILD_ID, BENCHMARK_MODE,
        (unsigned int)BENCHMARK_CALIBRATION_ITERATIONS,
        (unsigned int)BENCHMARK_TARGET_SECONDS,
        (unsigned long)BENCHMARK_CLOCK_HZ, TOTAL_DATA_SIZE,
        (unsigned int)BENCHMARK_SEED1, (unsigned int)BENCHMARK_SEED2,
        (unsigned int)BENCHMARK_SEED3);

    /* Run one fixed-size calibration workload. The second upstream invocation
     * reinitializes its static algorithm state before the scored pass. */
    seed4_volatile = (ee_s32)BENCHMARK_CALIBRATION_ITERATIONS;
    verify_runtime_seeds("calibration", (ee_s32)BENCHMARK_CALIBRATION_ITERATIONS);
    (void)coremark_main(0, (char **)0);

    CORE_TICKS calibration_ticks = get_time();
    if (benchmark_elapsed_interval_count() != 1u || calibration_ticks == 0) {
        benchmark_port_error_halt("invalid fixed-iteration calibration timer");
    }
    verify_loaded_image("calibration", image_crc32, image_size);

    uint64_t scored_iterations = derive_scored_iterations(calibration_ticks);
    if (scored_iterations == 0 || scored_iterations > 0x7fffffffULL) {
        benchmark_port_error_halt("derived iteration count is outside signed CoreMark range");
    }
    seed4_volatile = (ee_s32)scored_iterations;

    ee_printf("BENCHMARK_CALIBRATION_RESULT profile=%s build_id=%s mode=%s "
        "iterations=%u elapsed_hi=%08lx elapsed_lo=%08lx target_seconds=%u "
        "scored_iterations=%lu\n",
        BENCHMARK_PROFILE, BENCHMARK_BUILD_ID, BENCHMARK_MODE,
        (unsigned int)BENCHMARK_CALIBRATION_ITERATIONS,
        (unsigned long)(calibration_ticks >> 32),
        (unsigned long)(calibration_ticks & 0xffffffffu),
        (unsigned int)BENCHMARK_TARGET_SECONDS,
        (unsigned long)scored_iterations);

    ee_printf("BENCHMARK_START profile=%s build_id=%s mode=%s clock_hz=%lu "
        "data_size=%u contexts=1 seed1=%u seed2=%u seed3=0x%02x "
        "calibration_method=fresh_invocation calibration_iterations=%u "
        "target_seconds=%u scored_iterations=%lu\n",
        BENCHMARK_PROFILE, BENCHMARK_BUILD_ID, BENCHMARK_MODE,
        (unsigned long)BENCHMARK_CLOCK_HZ, TOTAL_DATA_SIZE,
        (unsigned int)BENCHMARK_SEED1, (unsigned int)BENCHMARK_SEED2,
        (unsigned int)BENCHMARK_SEED3,
        (unsigned int)BENCHMARK_CALIBRATION_ITERATIONS,
        (unsigned int)BENCHMARK_TARGET_SECONDS,
        (unsigned long)scored_iterations);
    ee_printf("BENCHMARK_CALIBRATION_PLAN iterations=%u target_seconds=%u "
        "scored_iterations=%lu method=fresh_invocation\n",
        (unsigned int)BENCHMARK_CALIBRATION_ITERATIONS,
        (unsigned int)BENCHMARK_TARGET_SECONDS,
        (unsigned long)scored_iterations);
    ee_printf("BENCHMARK_MEMORY code=main_ram data=bss=onchip_sram cache=%s\n",
        BENCHMARK_CACHE_CONFIG);
    verify_runtime_seeds("scored", (ee_s32)scored_iterations);

    (void)coremark_main(0, (char **)0);
    verify_loaded_image("scored", image_crc32, image_size);
    if (!benchmark_report_timing()) {
        benchmark_port_error_halt("expected exactly two valid timing intervals");
    }
    ee_printf("BENCHMARK_END status=returned profile=%s build_id=%s\n",
        BENCHMARK_PROFILE, BENCHMARK_BUILD_ID);

    for (;;) {
        /* Leave the validated UART evidence available until the next SRAM load. */
    }
}
