#include "../firmware/app_logic.h"

#include <stdio.h>
#include <string.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #condition); \
        return 1; \
    } \
} while (0)

static int test_debounce(void)
{
    app_state_t state;
    unsigned int i;
    app_state_init(&state);

    for (i = 0; i < 19; i++)
        app_state_tick_1ms(&state, 0x01);
    CHECK(state.stable == 0);
    app_state_tick_1ms(&state, 0x00); /* short bounce cancels the candidate */
    CHECK(state.stable == 0);

    for (i = 0; i < APP_DEBOUNCE_SAMPLES; i++)
        app_state_tick_1ms(&state, 0x05); /* simultaneous buttons 0 and 2 */
    CHECK(state.stable == 0x05);
    for (i = 0; i < APP_DEBOUNCE_SAMPLES - 1; i++)
        app_state_tick_1ms(&state, 0x00);
    CHECK(state.stable == 0x05);
    app_state_tick_1ms(&state, 0x00);
    CHECK(state.stable == 0);

    /* S4 is the Dock's reset/configuration switch and is excluded from GPIO. */
    app_state_init(&state);
    for (i = 0; i < APP_DEBOUNCE_SAMPLES; i++)
        app_state_tick_1ms(&state, 0x10);
    CHECK(state.stable == 0);

    /* Independent candidates settle independently. */
    app_state_init(&state);
    for (i = 0; i < 20; i++)
        app_state_tick_1ms(&state, 0x02);
    CHECK(state.stable == 0x02);
    for (i = 0; i < 19; i++)
        app_state_tick_1ms(&state, 0x06);
    CHECK(state.stable == 0x02);
    app_state_tick_1ms(&state, 0x06);
    CHECK(state.stable == 0x06);
    return 0;
}

static int test_commands(void)
{
    app_state_t state;
    app_command_t command;
    uint8_t mask = 0xaa;
    app_state_init(&state);

    CHECK(app_parse_command("help", &mask) == APP_CMD_HELP);
    CHECK(app_parse_command("buttons", &mask) == APP_CMD_BUTTONS);
    CHECK(app_parse_command("demo", &mask) == APP_CMD_DEMO);
    CHECK(app_parse_command("", &mask) == APP_CMD_EMPTY);
    CHECK(app_parse_command("what", &mask) == APP_CMD_INVALID);

    command = app_parse_command("leds 3f", &mask);
    CHECK(command == APP_CMD_LEDS && mask == 0x3f);
    app_apply_command(&state, command, mask);
    CHECK(state.demo_mode == 0 && state.led_mask == 0x3f);
    CHECK(app_parse_command("leds 0X09", &mask) == APP_CMD_LEDS && mask == 9);
    CHECK(app_parse_command("leds 00", &mask) == APP_CMD_LEDS && mask == 0);

    CHECK(app_parse_command("leds 40", &mask) == APP_CMD_INVALID);
    CHECK(app_parse_command("leds 0x", &mask) == APP_CMD_INVALID);
    CHECK(app_parse_command("leds 123", &mask) == APP_CMD_INVALID);
    CHECK(app_parse_command("leds ff", &mask) == APP_CMD_INVALID);
    CHECK(app_parse_command("leds 0x1g", &mask) == APP_CMD_INVALID);
    CHECK(app_parse_command("leds", &mask) == APP_CMD_INVALID);
    CHECK(state.demo_mode == 0 && state.led_mask == 0x3f);

    app_state_tick_1ms(&state, 0x01);
    CHECK(state.led_mask == 0x3f); /* manual LED state is not overwritten */
    app_apply_command(&state, APP_CMD_DEMO, 0);
    CHECK(state.demo_mode == 1 && state.led_mask == 0);
    return 0;
}

static int test_line_parser(void)
{
    app_line_parser_t parser;
    app_line_result_t result = APP_LINE_NONE;
    unsigned int i;
    app_line_parser_init(&parser);
    CHECK(app_line_parser_feed(&parser, 'h') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, 'i') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, '\r') == APP_LINE_READY);
    CHECK(strcmp(parser.text, "hi") == 0);
    CHECK(app_line_parser_feed(&parser, '\n') == APP_LINE_NONE); /* CRLF is one line */
    CHECK(app_line_parser_feed(&parser, '\n') == APP_LINE_READY);
    CHECK(strcmp(parser.text, "") == 0);

    for (i = 0; i < 100; i++)
        result = app_line_parser_feed(&parser, 'x');
    CHECK(result == APP_LINE_NONE && parser.discarding);
    CHECK(app_line_parser_feed(&parser, '\r') == APP_LINE_TOO_LONG);
    CHECK(app_line_parser_feed(&parser, '\n') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, 'd') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, 'o') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, 'n') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, 'e') == APP_LINE_NONE);
    CHECK(app_line_parser_feed(&parser, '\n') == APP_LINE_READY);
    CHECK(strcmp(parser.text, "done") == 0);
    return 0;
}

static int test_continuous_uart_activity(void)
{
    app_state_t state;
    app_line_parser_t parser;
    unsigned int i;
    app_state_init(&state);
    app_line_parser_init(&parser);
    for (i = 0; i < 2000; i++) {
        (void)app_line_parser_feed(&parser, (i % 2) ? '\n' : 'z');
        app_state_tick_1ms(&state, 0x08);
    }
    CHECK(state.stable == 0x08);
    return 0;
}

int main(void)
{
    CHECK(test_debounce() == 0);
    CHECK(test_commands() == 0);
    CHECK(test_line_parser() == 0);
    CHECK(test_continuous_uart_activity() == 0);
    puts("firmware logic checks passed");
    return 0;
}
