#include "command.h"

#include <ctype.h>
#include <stddef.h>
#include <string.h>

bool uint32_token(const char *text, uint32_t *value) {
    if (text == NULL || *text == '\0') return false;
    uint32_t current = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
        uint32_t digit = (uint32_t)(*p - '0');
        if (current > (UINT32_MAX - digit) / 10u) return false;
        current = current * 10u + digit;
    }
    *value = current;
    return true;
}

bool parse_command(const char *line, struct command *out) {
    if (line == NULL || strlen(line) > 127) return false;
    char tokens[4][32] = {{0}};
    unsigned count = 0;
    const unsigned char *p = (const unsigned char *)line;
    while (*p) {
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
        if (!*p) break;
        if (count == 4) return false;
        unsigned length = 0;
        while (*p && !isspace(*p)) {
            if (length == 31 || *p < 32 || *p > 126) return false;
            tokens[count][length++] = (char)*p++;
        }
        ++count;
    }
    if (count < 2 || tokens[0][1] != '\0') return false;
    struct command candidate = {.kind = tokens[0][0]};
    if (!uint32_token(tokens[1], &candidate.sequence) || !candidate.sequence)
        return false;
    if (candidate.kind == 'M') {
        if (count != 4 || !uint32_token(tokens[2], &candidate.x) ||
            !uint32_token(tokens[3], &candidate.y)) return false;
    } else if (candidate.kind == 'D' || candidate.kind == 'U' ||
               candidate.kind == 'Q') {
        if (count != 2) return false;
    } else return false;
    *out = candidate;
    return true;
}

bool guard_command(const struct bounds *box, struct guard *state,
                   const struct command *command) {
    if (state->count >= MX4_COMMAND_LIMIT ||
        command->sequence != state->sequence + 1u) return false;
    struct guard next = *state;
    switch (command->kind) {
    case 'M':
        if (command->x < box->left || command->x >= box->right ||
            command->y < box->top || command->y >= box->bottom) return false;
        next.x = command->x; next.y = command->y; next.positioned = true;
        break;
    case 'D':
        if (next.down || !next.positioned) return false;
        next.down = true;
        break;
    case 'U':
        if (!next.down) return false;
        next.down = false; next.positioned = false;
        break;
    case 'Q': break;
    default: return false;
    }
    next.sequence = command->sequence;
    next.count++;
    *state = next;
    return true;
}
