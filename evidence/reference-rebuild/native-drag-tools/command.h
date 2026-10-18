#ifndef MX4_DRAG_COMMAND_H
#define MX4_DRAG_COMMAND_H

#include <stdbool.h>
#include <stdint.h>

#define MX4_COMMAND_LIMIT 128u
#define MX4_MAX_LOGICAL_EXTENT 16384u

struct command { char kind; uint32_t sequence, x, y; };
struct bounds { uint32_t left, top, right, bottom; };
struct guard {
    uint32_t sequence, count, x, y;
    bool down, positioned;
};

bool uint32_token(const char *text, uint32_t *value);
bool parse_command(const char *line, struct command *out);
bool guard_command(const struct bounds *box, struct guard *state,
                   const struct command *command);

#endif
