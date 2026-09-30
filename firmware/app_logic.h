#ifndef TANG20K_APP_LOGIC_H
#define TANG20K_APP_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define APP_BUTTON_MASK 0x0fu
#define APP_LED_MASK 0x3fu
#define APP_LINE_CAPACITY 64u
#define APP_DEBOUNCE_SAMPLES 20u
#define APP_HEARTBEAT_PERIOD_MS 500u

typedef struct {
    char text[APP_LINE_CAPACITY];
    size_t length;
    uint8_t discarding;
    uint8_t swallow_lf;
} app_line_parser_t;

typedef enum {
    APP_LINE_NONE,
    APP_LINE_READY,
    APP_LINE_TOO_LONG
} app_line_result_t;

typedef struct {
    uint8_t candidate;
    uint8_t stable;
    uint8_t led_mask;
    uint8_t demo_mode;
    uint8_t heartbeat;
    uint16_t heartbeat_ms;
    uint8_t samples[4];
} app_state_t;

typedef enum {
    APP_CMD_EMPTY,
    APP_CMD_HELP,
    APP_CMD_BUTTONS,
    APP_CMD_LEDS,
    APP_CMD_DEMO,
    APP_CMD_INVALID
} app_command_t;

void app_line_parser_init(app_line_parser_t *parser);
app_line_result_t app_line_parser_feed(app_line_parser_t *parser, char ch);
void app_state_init(app_state_t *state);
void app_state_tick_1ms(app_state_t *state, uint8_t synchronized_buttons);
app_command_t app_parse_command(const char *line, uint8_t *led_mask);
void app_apply_command(app_state_t *state, app_command_t command, uint8_t led_mask);

#endif
