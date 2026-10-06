#include "app_logic.h"
#include "profile.h"

#include <stdio.h>

#include <irq.h>
#include <libbase/uart.h>
#include <generated/csr.h>

#if PROJECT_PERIPHERAL_DIAGNOSTIC
#include "peripherals/peripheral_diag.h"
#else
#if PROJECT_SDCARD_SPI
#include "peripherals/sdcard_app.h"
#endif
#if PROJECT_ETHERNET_RMII
#include "peripherals/ethernet_lwip.h"
#endif
#endif

#define SYS_CLOCK_HZ 48000000u
#define SAMPLE_CYCLES (SYS_CLOCK_HZ / 1000u)

static app_state_t app;
static app_line_parser_t line_parser;

static void write_leds(void)
{
    leds_out_write(app.led_mask & APP_LED_MASK);
}

static void print_help(void)
{
    puts("Commands:");
    puts("  help            show commands");
    puts("  buttons         show synchronized and debounced S0-S3 masks");
    puts("  leds <hex-mask> set LEDs 0-5 (0x00 through 0x3f), manual mode");
    puts("  demo            mirror S0-S3 to LEDs 0-3; LED 5 heartbeats");
#if PROJECT_PERIPHERAL_DIAGNOSTIC
    puts("  periph status   show the fitted peripheral controller CSRs");
#else
#if PROJECT_SDCARD_SPI
    puts("  sd status       initialize card and show CID/CSD/capacity/clocks");
    puts("  sd ls [/path]   list a FAT16/FAT32/exFAT directory");
    puts("  sd read /path   stream-read a file and report size/CRC32");
    puts("  sd roundtrip N  create a new test file (N=512, 4096, or 1048576)");
#endif
#if PROJECT_ETHERNET_RMII
    puts("  net status      show PHY, link, address and packet/error counters");
    puts("  net restart     restart PHY negotiation and clear power-down/isolation");
    puts("  net static IP MASK GATEWAY | net dhcp | net mac XX:XX:XX:XX:XX:XX");
#endif
#endif
}

static void handle_line(void)
{
    uint8_t mask = 0;
    app_command_t command = app_parse_command(line_parser.text, &mask);

#if PROJECT_PERIPHERAL_DIAGNOSTIC
    if (peripheral_diag_command(line_parser.text)) {
        fputs("gpio> ", stdout);
        return;
    }
#else
#if PROJECT_SDCARD_SPI
    if (sdcard_app_command(line_parser.text)) {
        fputs("gpio> ", stdout);
        return;
    }
#endif
#if PROJECT_ETHERNET_RMII
    if (ethernet_app_command(line_parser.text)) {
        fputs("gpio> ", stdout);
        return;
    }
#endif
#endif

    switch (command) {
    case APP_CMD_EMPTY:
        break;
    case APP_CMD_HELP:
        print_help();
        break;
    case APP_CMD_BUTTONS:
        printf("buttons synchronized=0x%02x debounced=0x%02x\n",
            (unsigned int)(buttons_in_read() & APP_BUTTON_MASK), app.stable);
        break;
    case APP_CMD_LEDS:
        app_apply_command(&app, command, mask);
        write_leds();
        printf("manual LEDs=0x%02x\n", app.led_mask);
        break;
    case APP_CMD_DEMO:
        app_apply_command(&app, command, 0);
        write_leds();
        puts("demo mode restored");
        break;
    case APP_CMD_INVALID:
        puts("error: invalid command or LED mask; type help");
        break;
    }
    fputs("gpio> ", stdout);
}

static void service_uart(void)
{
    while (uart_read_nonblock()) {
        char ch = uart_read();
        app_line_result_t result = app_line_parser_feed(&line_parser, ch);
        if (result == APP_LINE_READY) {
            handle_line();
        } else if (result == APP_LINE_TOO_LONG) {
            puts("error: command line too long; discarded");
            fputs("gpio> ", stdout);
        }
    }
}

int main(void)
{
    uint32_t previous_timer;
    uint32_t sample_cycles = 0;

#ifdef CONFIG_CPU_HAS_INTERRUPT
    irq_setmask(0);
    irq_setie(0);
#endif
    uart_init();
    app_state_init(&app);
    app_line_parser_init(&line_parser);

    timer0_en_write(0);
    timer0_reload_write(0xffffffffu);
    timer0_load_write(0xffffffffu);
    timer0_en_write(1);
    timer0_update_value_write(1);
    previous_timer = timer0_value_read();

    puts("Sipeed Tang Primer 20K with standard Dock");
    printf("CPU profile: %s\n", CPU_PROFILE_NAME);
    puts("System clock: 48 MHz; UART: 115200 baud");
    print_help();
    puts("GPIO demo ready");
#if PROJECT_PERIPHERAL_DIAGNOSTIC
    peripheral_diag_init();
#else
#if PROJECT_SDCARD_SPI
    sdcard_app_init();
#endif
#if PROJECT_ETHERNET_RMII
    ethernet_app_init();
#endif
#endif
    fputs("gpio> ", stdout);

    for (;;) {
        uint32_t current_timer;
        uint32_t elapsed_cycles;

        timer0_update_value_write(1);
        current_timer = timer0_value_read();
        elapsed_cycles = previous_timer - current_timer;
        previous_timer = current_timer;
        sample_cycles += elapsed_cycles;

#if PROJECT_ETHERNET_RMII && !PROJECT_PERIPHERAL_DIAGNOSTIC
        ethernet_app_poll();
#endif

        while (sample_cycles >= SAMPLE_CYCLES) {
            sample_cycles -= SAMPLE_CYCLES;
            app_state_tick_1ms(&app, (uint8_t)buttons_in_read());
            write_leds();
        }
        service_uart();
    }
}
