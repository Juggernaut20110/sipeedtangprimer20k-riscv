#include <generated/csr.h>
#include <generated/mem.h>
#include <stdio.h>
#include <stdint.h>
#include <system.h>
#include <uart.h>

#ifndef CPU_PROFILE_NAME
#define CPU_PROFILE_NAME "unknown"
#endif
#ifndef STRESS_SECONDS
#define STRESS_SECONDS 1800u
#endif

#define DDR_BYTES          0x10000000u
#define DDR_CACHED_BASE    0x40000000u
#define DDR_WORDS          (DDR_BYTES / sizeof(uint32_t))
#define TEST_CLOCK_HZ      48000000u
#define STRESS_BLOCK_BYTES (1024u * 1024u)
#define STRESS_BLOCK_WORDS (STRESS_BLOCK_BYTES / sizeof(uint32_t))
#define PAGE_BYTES         4096u

#if !defined(MAIN_RAM_BASE) || MAIN_RAM_BASE != DDR_CACHED_BASE
#error "DDR diagnostic firmware requires the 0x40000000 cached main RAM mapping"
#endif
#if !defined(MAIN_RAM_SIZE) || MAIN_RAM_SIZE != DDR_BYTES
#error "DDR diagnostic firmware requires the complete 256 MiB DDR geometry"
#endif
#if !defined(DDR_UNCACHED_BASE) || DDR_UNCACHED_BASE != 0xc0000000
#error "DDR diagnostic firmware requires the controller-bypassing uncached alias"
#endif
#if !defined(DDR_UNCACHED_SIZE) || DDR_UNCACHED_SIZE != DDR_BYTES
#error "DDR diagnostic firmware requires a full-range uncached alias"
#endif

static volatile uint32_t *const uncached_words = (volatile uint32_t *)DDR_UNCACHED_BASE;
static volatile uint32_t *const cached_words = (volatile uint32_t *)DDR_CACHED_BASE;
static uint32_t completed_phases;

static uint64_t read_ticks(void)
{
    timer0_uptime_latch_write(1);
    return timer0_uptime_cycles_read();
}

/* Booted diagnostics keep interrupts disabled. Flush every UART record so
 * startup/failure/completion markers cannot remain in the software TX ring. */
static void print_u64_words(const char *name, uint64_t value)
{
    printf("%s_hi=%08lx %s_lo=%08lx", name,
        (unsigned long)(value >> 32), name,
        (unsigned long)(value & 0xffffffffu));
    uart_sync();
}

static void phase_start(const char *name, uint32_t range_start, uint32_t range_end)
{
    printf("DDR_TEST_PHASE_START name=%s range_start=0x%08lx range_end=0x%08lx coverage_bytes=%lu\n",
        name, (unsigned long)(DDR_UNCACHED_BASE + range_start),
        (unsigned long)(DDR_UNCACHED_BASE + range_end),
        (unsigned long)(range_end - range_start));
    uart_sync();
}

static void phase_end(const char *name, uint32_t bytes, uint32_t operations, uint64_t ticks)
{
    printf("DDR_TEST_PHASE_END name=%s status=passed bytes=%lu operations=%lu ",
        name, (unsigned long)bytes, (unsigned long)operations);
    uart_sync();
    print_u64_words("elapsed_ticks", ticks);
    printf(" errors=0\n");
    uart_sync();
    completed_phases++;
}

#ifdef DDR_TEST_READ_DELAY_PROBE
/* This runs only in the separate on-chip diagnostic probe, after a failure.
 * Keep the failed data unchanged while moving the implicated lane's receive
 * delay around the trained position. Restore both delay and FIFO state. */
static void probe_failed_read(uint32_t address, uint32_t expected, uint32_t actual)
{
    static const int offsets[] = {0, 20, -20, 40, -40, 0};
    uint32_t delta = expected ^ actual;
    uint32_t lanes = ((delta & 0x00ff00ffu) ? 1u : 0u) |
                     ((delta & 0xff00ff00u) ? 2u : 0u);
    int position = 0;
    for (unsigned int index = 0; index < sizeof(offsets)/sizeof(offsets[0]); index++) {
        int steps = offsets[index] - position;
        ddrphy_dly_sel_write(lanes);
        ddrphy_rdly_dq_dir_write(steps < 0 ? 1 : 0);
        for (int step = 0; step < (steps < 0 ? -steps : steps); step++)
            ddrphy_rdly_dq_inc_write(1);
        ddrphy_dly_sel_write(0);
        position = offsets[index];
        unsigned int errors = 0;
        uint32_t first = 0;
        for (unsigned int repeat = 0; repeat < 16; repeat++) {
            uint32_t value = *(volatile uint32_t *)address;
            if (!repeat)
                first = value;
            if (value != expected)
                errors++;
        }
        printf("DDR_TEST_READ_PROBE offset=%d lanes=%u address=0x%08lx expected=0x%08lx actual_first=0x%08lx errors=%u reads=16\n",
            position, (unsigned int)lanes, (unsigned long)address, (unsigned long)expected,
            (unsigned long)first, errors);
        uart_sync();
    }
}
#endif

__attribute__((noreturn)) static void fail(const char *phase, uint32_t address,
    uint32_t expected, uint32_t actual)
{
    printf("DDR_TEST_FAILURE phase=%s address=0x%08lx expected=0x%08lx actual=0x%08lx errors=1\n",
        phase, (unsigned long)address, (unsigned long)expected, (unsigned long)actual);
    uart_sync();
#ifdef DDR_TEST_READ_DELAY_PROBE
    probe_failed_read(address, expected, actual);
#endif
    printf("DDR_TEST_END status=failed profile=%s phases=%lu errors=1 tested_bytes=%u\n",
        CPU_PROFILE_NAME, (unsigned long)completed_phases, DDR_BYTES);
    uart_sync();
    for (;;) {
        asm volatile("wfi");
    }
}

static uint32_t address_pattern(uint32_t index)
{
    uint32_t address = index << 2;
    return 0x9e3779b9u ^ address ^ ((address << 13) | (address >> 19));
}

static uint32_t random_next(uint32_t state)
{
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

static void test_walking_ones_zeros(void)
{
    uint64_t start;
    uint32_t i;

    phase_start("walking_ones", 0, DDR_BYTES);
    start = read_ticks();
    for (i = 0; i < DDR_WORDS; i++)
        uncached_words[i] = 1u << (i & 31u);
    for (i = 0; i < DDR_WORDS; i++) {
        uint32_t expected = 1u << (i & 31u);
        uint32_t actual = uncached_words[i];
        if (actual != expected)
            fail("walking_ones", DDR_UNCACHED_BASE + (i << 2), expected, actual);
    }
    phase_end("walking_ones", DDR_BYTES, DDR_WORDS, read_ticks() - start);

    phase_start("walking_zeros", 0, DDR_BYTES);
    start = read_ticks();
    for (i = 0; i < DDR_WORDS; i++)
        uncached_words[i] = ~(1u << (i & 31u));
    for (i = 0; i < DDR_WORDS; i++) {
        uint32_t expected = ~(1u << (i & 31u));
        uint32_t actual = uncached_words[i];
        if (actual != expected)
            fail("walking_zeros", DDR_UNCACHED_BASE + (i << 2), expected, actual);
    }
    phase_end("walking_zeros", DDR_BYTES, DDR_WORDS, read_ticks() - start);
}

static void test_fixed_inverted(void)
{
    uint64_t start;
    uint32_t i;
    const uint32_t fixed = 0xa55ac33cu;
    const uint32_t inverted = ~fixed;

    phase_start("fixed_pattern", 0, DDR_BYTES);
    start = read_ticks();
    for (i = 0; i < DDR_WORDS; i++)
        uncached_words[i] = fixed;
    for (i = 0; i < DDR_WORDS; i++) {
        uint32_t actual = uncached_words[i];
        if (actual != fixed)
            fail("fixed_pattern", DDR_UNCACHED_BASE + (i << 2), fixed, actual);
    }
    phase_end("fixed_pattern", DDR_BYTES, DDR_WORDS, read_ticks() - start);

    phase_start("inverted_pattern", 0, DDR_BYTES);
    start = read_ticks();
    for (i = 0; i < DDR_WORDS; i++)
        uncached_words[i] = inverted;
    for (i = 0; i < DDR_WORDS; i++) {
        uint32_t actual = uncached_words[i];
        if (actual != inverted)
            fail("inverted_pattern", DDR_UNCACHED_BASE + (i << 2), inverted, actual);
    }
    phase_end("inverted_pattern", DDR_BYTES, DDR_WORDS, read_ticks() - start);
}

static void test_address_patterns(void)
{
    uint64_t start;
    uint32_t i;

    phase_start("address_pattern", 0, DDR_BYTES);
    start = read_ticks();
    for (i = 0; i < DDR_WORDS; i++)
        uncached_words[i] = address_pattern(i);
    for (i = 0; i < DDR_WORDS; i++) {
        uint32_t expected = address_pattern(i);
        uint32_t actual = uncached_words[i];
        if (actual != expected)
            fail("address_pattern", DDR_UNCACHED_BASE + (i << 2), expected, actual);
    }
    phase_end("address_pattern", DDR_BYTES, DDR_WORDS, read_ticks() - start);
}

static void test_pseudorandom(void)
{
    uint64_t start;
    uint32_t state = 0x4d595df4u;
    uint32_t i;

    phase_start("deterministic_pseudorandom", 0, DDR_BYTES);
    start = read_ticks();
    for (i = 0; i < DDR_WORDS; i++) {
        state = random_next(state);
        uncached_words[i] = state;
    }
    state = 0x4d595df4u;
    for (i = 0; i < DDR_WORDS; i++) {
        state = random_next(state);
        uint32_t actual = uncached_words[i];
        if (actual != state)
            fail("deterministic_pseudorandom", DDR_UNCACHED_BASE + (i << 2), state, actual);
    }
    phase_end("deterministic_pseudorandom", DDR_BYTES, DDR_WORDS, read_ticks() - start);
}

static void test_address_aliasing(void)
{
    uint64_t start = read_ticks();
    uint32_t offsets[26];
    uint32_t expected[26];
    uint32_t count = 0;
    uint32_t bit;

    printf("DDR_TEST_PHASE_START name=address_bank_row_column_alias range_start=0x%08lx range_end=0x%08lx coverage_bytes=%lu alias_offsets=26 geometry=8x16384x1024\n",
        (unsigned long)DDR_UNCACHED_BASE,
        (unsigned long)(DDR_UNCACHED_BASE + DDR_BYTES),
        (unsigned long)(26u * sizeof(uint32_t)));
    uart_sync();
    uncached_words[0] = 0x13579bdfu;
    for (bit = 2; bit < 28; bit++) {
        uint32_t offset = 1u << bit;
        uint32_t value = 0xc0010000u ^ (bit * 0x01010101u);
        if (offset >= DDR_BYTES)
            continue;
        offsets[count] = offset;
        expected[count] = value;
        *(volatile uint32_t *)(DDR_UNCACHED_BASE + offset) = value;
        count++;
        if (uncached_words[0] != 0x13579bdfu)
            fail("address_bank_row_column_alias", DDR_UNCACHED_BASE,
                0x13579bdfu, uncached_words[0]);
    }
    for (bit = 0; bit < count; bit++) {
        uint32_t actual = *(volatile uint32_t *)(DDR_UNCACHED_BASE + offsets[bit]);
        if (actual != expected[bit])
            fail("address_bank_row_column_alias", DDR_UNCACHED_BASE + offsets[bit],
                expected[bit], actual);
    }
    if (uncached_words[0] != 0x13579bdfu)
        fail("address_bank_row_column_alias", DDR_UNCACHED_BASE,
            0x13579bdfu, uncached_words[0]);
    phase_end("address_bank_row_column_alias", count * sizeof(uint32_t), count, read_ticks() - start);
}

static void test_subword_lanes(void)
{
    uint64_t start;
    uint32_t page;
    uint32_t page_count = DDR_BYTES / PAGE_BYTES;

    printf("DDR_TEST_PHASE_START name=byte_halfword_neighbor_preservation range_start=0x%08lx range_end=0x%08lx coverage_bytes=%u sample_stride_bytes=%u samples_per_page=5\n",
        (unsigned long)DDR_UNCACHED_BASE,
        (unsigned long)(DDR_UNCACHED_BASE + DDR_BYTES), DDR_BYTES, PAGE_BYTES);
    uart_sync();
    start = read_ticks();
    for (page = 0; page < page_count; page++) {
        uint32_t base = page * PAGE_BYTES;
        uint32_t byte_offsets[2] = {base, base + PAGE_BYTES - 1u};
        uint32_t half_offsets[3] = {base, base + 2u, base + PAGE_BYTES - 2u};
        uint32_t item;
        for (item = 0; item < 2; item++) {
            uint32_t offset = byte_offsets[item];
            volatile uint32_t *word = (volatile uint32_t *)(DDR_UNCACHED_BASE + (offset & ~3u));
            volatile uint8_t *byte = (volatile uint8_t *)(DDR_UNCACHED_BASE + offset);
            uint32_t expected = 0x5a963cc3u ^ (page * 0x01010101u);
            unsigned int lane = offset & 3u;
            uint8_t value = (uint8_t)(0x80u ^ page ^ item);
            *word = expected;
            *byte = value;
            expected = (expected & ~(0xffu << (lane * 8u))) | ((uint32_t)value << (lane * 8u));
            if (*word != expected)
                fail("byte_halfword_neighbor_preservation", DDR_UNCACHED_BASE + (offset & ~3u), expected, *word);
        }
        for (item = 0; item < 3; item++) {
            uint32_t offset = half_offsets[item];
            volatile uint32_t *word = (volatile uint32_t *)(DDR_UNCACHED_BASE + (offset & ~3u));
            volatile uint16_t *half = (volatile uint16_t *)(DDR_UNCACHED_BASE + offset);
            uint32_t expected = 0xa55a6996u ^ (page * 0x01010101u);
            unsigned int lane = (offset & 2u) ? 1u : 0u;
            uint16_t value = (uint16_t)(0x8001u ^ page ^ (item * 0x1111u));
            *word = expected;
            *half = value;
            expected = (expected & ~(0xffffu << (lane * 16u))) | ((uint32_t)value << (lane * 16u));
            if (*word != expected)
                fail("byte_halfword_neighbor_preservation", DDR_UNCACHED_BASE + (offset & ~3u), expected, *word);
        }
    }
    phase_end("byte_halfword_neighbor_preservation", DDR_BYTES, page_count * 5u, read_ticks() - start);
}

static void test_cache_visibility(void)
{
    uint64_t start = read_ticks();
    uint32_t offset;

    printf("DDR_TEST_PHASE_START name=cached_uncached_visibility range_start=0x%08lx range_end=0x%08lx coverage_bytes=512 sample_stride_bytes=4194304 samples=64 cache_maintenance=fence,dflush,l2flush\n",
        (unsigned long)(DDR_UNCACHED_BASE + 1024u * 1024u),
        (unsigned long)(DDR_UNCACHED_BASE + DDR_BYTES));
    uart_sync();
    for (offset = 1024u * 1024u; offset < DDR_BYTES; offset += 4u * 1024u * 1024u) {
        uint32_t index = offset >> 2;
        uint32_t value = 0x6d2b79f5u ^ offset;
        cached_words[index] = value;
        asm volatile("fence" ::: "memory");
        flush_cpu_dcache();
        asm volatile("fence" ::: "memory");
        flush_l2_cache();
        asm volatile("fence" ::: "memory");
        uint32_t actual = *(volatile uint32_t *)(DDR_UNCACHED_BASE + offset);
        if (actual != value)
            fail("cached_to_uncached_visibility", DDR_UNCACHED_BASE + offset, value, actual);

        value = ~value;
        *(volatile uint32_t *)(DDR_UNCACHED_BASE + offset) = value;
        asm volatile("fence" ::: "memory");
        flush_cpu_dcache();
        asm volatile("fence" ::: "memory");
        flush_l2_cache();
        asm volatile("fence" ::: "memory");
        actual = cached_words[index];
        if (actual != value)
            fail("uncached_to_cached_visibility", DDR_CACHED_BASE + offset, value, actual);
    }
    phase_end("cached_uncached_visibility", 512u, 128u,
        read_ticks() - start);
}

static void test_stress(void)
{
    uint64_t start = read_ticks();
    uint64_t deadline_ticks = (uint64_t)STRESS_SECONDS * TEST_CLOCK_HZ;
    uint64_t bytes_read = 0;
    uint64_t bytes_written = 0;
    uint64_t stress_start = start;
    uint32_t seed = 0x243f6a88u;
    uint32_t sweep = 0;

    phase_start("sustained_delayed_readback_stress", 0, DDR_BYTES);
    while (read_ticks() - stress_start < deadline_ticks) {
        uint32_t block;
        for (block = 0; block < DDR_BYTES; block += STRESS_BLOCK_BYTES) {
            uint32_t words = STRESS_BLOCK_WORDS;
            uint32_t state = seed ^ block ^ (sweep * 0x9e3779b9u);
            uint32_t i;
            uint64_t write_start;
            uint64_t read_start;

            write_start = read_ticks();
            for (i = 0; i < words; i++) {
                state = random_next(state);
                *(volatile uint32_t *)(DDR_UNCACHED_BASE + block + (i << 2)) = state;
            }
            asm volatile("fence" ::: "memory");
            bytes_written += STRESS_BLOCK_BYTES;
            busy_wait_us(10000);

            state = seed ^ block ^ (sweep * 0x9e3779b9u);
            read_start = read_ticks();
            for (i = 0; i < words; i++) {
                state = random_next(state);
                uint32_t address = DDR_UNCACHED_BASE + block + (i << 2);
                uint32_t actual = *(volatile uint32_t *)address;
                if (actual != state)
                    fail("sustained_delayed_readback_stress", address, state, actual);
            }
            asm volatile("fence" ::: "memory");
            bytes_read += STRESS_BLOCK_BYTES;
            (void)write_start;
            (void)read_start;
            seed = random_next(seed + block + 1u);
            if (read_ticks() - stress_start >= deadline_ticks)
                break;
        }
        sweep++;
    }

    uint64_t elapsed = read_ticks() - stress_start;
    uint32_t read_bps = elapsed ? (uint32_t)((bytes_read * TEST_CLOCK_HZ) / elapsed) : 0;
    uint32_t write_bps = elapsed ? (uint32_t)((bytes_written * TEST_CLOCK_HZ) / elapsed) : 0;
    printf("DDR_TEST_BANDWIDTH path=uncached_controller_alias transfer_bytes=%u clock_hz=%u ",
        STRESS_BLOCK_BYTES, TEST_CLOCK_HZ);
    uart_sync();
    print_u64_words("read_bytes", bytes_read);
    printf(" ");
    uart_sync();
    print_u64_words("write_bytes", bytes_written);
    printf(" ");
    uart_sync();
    print_u64_words("elapsed_ticks", elapsed);
    printf(" read_bytes_per_second=%lu write_bytes_per_second=%lu sweeps=%lu\n",
        (unsigned long)read_bps, (unsigned long)write_bps, (unsigned long)sweep);
    uart_sync();
    printf("DDR_TEST_STRESS requested_seconds=%u actual_seconds_whole=%lu ",
        STRESS_SECONDS, (unsigned long)(elapsed / TEST_CLOCK_HZ));
    uart_sync();
    print_u64_words("elapsed_ticks", elapsed);
    printf("\n");
    uart_sync();
    phase_end("sustained_delayed_readback_stress", DDR_BYTES,
        (uint32_t)(bytes_read / sizeof(uint32_t)), elapsed);
}

int main(void)
{
    uint64_t start;
#ifdef DDR_TEST_SMOKE_ONLY
    uint32_t smoke_words = 4096u;
    uint32_t i;
#endif

    uart_init();
    asm volatile("fence" ::: "memory");
    flush_cpu_dcache();
    asm volatile("fence" ::: "memory");
    flush_cpu_icache();
    asm volatile("fence" ::: "memory");

    start = read_ticks();
    printf("DDR_TEST_START profile=%s memory=ddr3 clock_hz=%u ddr_bytes=%u cached_base=0x%08lx uncached_base=0x%08lx l2_bytes=%u stress_seconds=%u\n",
        CPU_PROFILE_NAME, TEST_CLOCK_HZ, DDR_BYTES, (unsigned long)DDR_CACHED_BASE,
        (unsigned long)DDR_UNCACHED_BASE, 8192u, STRESS_SECONDS);
    uart_sync();

#ifdef DDR_TEST_SMOKE_ONLY
    phase_start("training_smoke_uncached", 0, smoke_words * sizeof(uint32_t));
    for (i = 0; i < smoke_words; i++)
        uncached_words[i] = address_pattern(i);
    for (i = 0; i < smoke_words; i++) {
        uint32_t expected = address_pattern(i);
        uint32_t actual = uncached_words[i];
        if (actual != expected)
            fail("training_smoke_uncached", DDR_UNCACHED_BASE + (i << 2), expected, actual);
    }
    phase_end("training_smoke_uncached", smoke_words * sizeof(uint32_t), smoke_words,
        read_ticks() - start);
    printf("DDR_TEST_SMOKE status=passed tested_bytes=%lu\n",
        (unsigned long)(smoke_words * sizeof(uint32_t)));
    uart_sync();
#else
    test_walking_ones_zeros();
    test_fixed_inverted();
    test_address_patterns();
    test_pseudorandom();
    test_address_aliasing();
    test_subword_lanes();
    test_cache_visibility();
    test_stress();
#endif

    uint64_t total_ticks = read_ticks() - start;
    printf("DDR_TEST_END status=passed profile=%s memory=ddr3 phases=%lu errors=0 tested_bytes=%u ",
        CPU_PROFILE_NAME, (unsigned long)completed_phases, DDR_BYTES);
    uart_sync();
    print_u64_words("elapsed_ticks", total_ticks);
    printf("\n");
    uart_sync();
    return 0;
}
