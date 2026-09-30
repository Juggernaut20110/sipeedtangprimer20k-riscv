#include "app_logic.h"

#include <string.h>

void app_line_parser_init(app_line_parser_t *parser)
{
    memset(parser, 0, sizeof(*parser));
}

app_line_result_t app_line_parser_feed(app_line_parser_t *parser, char ch)
{
    if (parser->swallow_lf) {
        parser->swallow_lf = 0;
        if (ch == '\n')
            return APP_LINE_NONE;
    }

    if (ch == '\r' || ch == '\n') {
        app_line_result_t result = parser->discarding ? APP_LINE_TOO_LONG : APP_LINE_READY;
        parser->text[parser->length] = '\0';
        parser->length = 0;
        parser->discarding = 0;
        parser->swallow_lf = (ch == '\r');
        return result;
    }

    if (parser->discarding)
        return APP_LINE_NONE;

    if (parser->length >= APP_LINE_CAPACITY - 1) {
        parser->discarding = 1;
        parser->length = 0;
        return APP_LINE_NONE;
    }

    parser->text[parser->length++] = ch;
    return APP_LINE_NONE;
}

void app_state_init(app_state_t *state)
{
    memset(state, 0, sizeof(*state));
    state->demo_mode = 1;
}

void app_state_tick_1ms(app_state_t *state, uint8_t synchronized_buttons)
{
    unsigned int i;
    synchronized_buttons &= APP_BUTTON_MASK;

    for (i = 0; i < 4; i++) {
        uint8_t bit = (uint8_t)(1u << i);
        uint8_t value = synchronized_buttons & bit;
        uint8_t candidate = state->candidate & bit;

        if (value == candidate) {
            if (state->samples[i] < APP_DEBOUNCE_SAMPLES)
                state->samples[i]++;
        } else {
            state->candidate = (uint8_t)((state->candidate & (uint8_t)~bit) | value);
            state->samples[i] = 1;
        }

        if (state->samples[i] >= APP_DEBOUNCE_SAMPLES) {
            state->stable = (uint8_t)((state->stable & (uint8_t)~bit) | value);
        }
    }

    if (!state->demo_mode)
        return;

    if (++state->heartbeat_ms >= APP_HEARTBEAT_PERIOD_MS) {
        state->heartbeat_ms = 0;
        state->heartbeat ^= 1;
    }
    state->led_mask = (uint8_t)(state->stable | (state->heartbeat ? 0x20u : 0u));
}

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

app_command_t app_parse_command(const char *line, uint8_t *led_mask)
{
    size_t i;
    size_t length = strlen(line);
    unsigned int value = 0;
    unsigned int digits = 0;

    if (line[0] == '\0')
        return APP_CMD_EMPTY;
    if (strcmp(line, "help") == 0)
        return APP_CMD_HELP;
    if (strcmp(line, "buttons") == 0)
        return APP_CMD_BUTTONS;
    if (strcmp(line, "demo") == 0)
        return APP_CMD_DEMO;
    if (length < 6 || strncmp(line, "leds ", 5) != 0)
        return APP_CMD_INVALID;

    i = 5;
    if (length - i >= 2 && line[i] == '0' && (line[i + 1] == 'x' || line[i + 1] == 'X'))
        i += 2;
    for (; i < length; i++) {
        int digit = hex_value(line[i]);
        if (digit < 0 || digits == 2)
            return APP_CMD_INVALID;
        value = value * 16u + (unsigned int)digit;
        digits++;
    }
    if (digits == 0 || value > APP_LED_MASK)
        return APP_CMD_INVALID;
    *led_mask = (uint8_t)value;
    return APP_CMD_LEDS;
}

void app_apply_command(app_state_t *state, app_command_t command, uint8_t led_mask)
{
    switch (command) {
    case APP_CMD_LEDS:
        state->demo_mode = 0;
        state->led_mask = led_mask & APP_LED_MASK;
        break;
    case APP_CMD_DEMO:
        state->demo_mode = 1;
        state->heartbeat = 0;
        state->heartbeat_ms = 0;
        state->led_mask = state->stable;
        break;
    default:
        break;
    }
}
