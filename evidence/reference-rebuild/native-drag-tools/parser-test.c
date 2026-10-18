#include "command.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    struct command c;
    uint32_t value;
    assert(uint32_token("4294967295", &value) && value == UINT32_MAX);
    assert(!uint32_token("4294967296", &value));
    assert(!uint32_token("-1", &value));
    assert(!uint32_token("+1", &value));
    assert(!uint32_token("", &value));
    assert(!uint32_token(" 1", &value));
    const char *invalid[] = {"", "M", "M 1 2", "M 1 2 3 4", "M 0 2 3",
        "M 1 -2 3", "M 1 2 3junk", "M 1 nan 3", "M 1 4294967296 3",
        "D 1 2", "U 1 2", "Q 1 2", "MM 1", "K 1", "D -1", "D +1",
        "D 1\vjunk", "D 1\x01"};
    for (unsigned i=0; i < sizeof invalid / sizeof invalid[0]; ++i)
        assert(!parse_command(invalid[i], &c));
    assert(parse_command("\tM 1 256 512\r\n", &c));
    assert(c.kind == 'M' && c.sequence == 1 && c.x == 256 && c.y == 512);
    struct bounds box = {256, 256, 768, 768};
    struct guard state = {0}, before = state;
    assert(parse_command("D 1", &c));
    assert(!guard_command(&box, &state, &c));
    assert(memcmp(&state, &before, sizeof state) == 0);
    assert(parse_command("M 1 256 512", &c));
    assert(guard_command(&box, &state, &c));
    before = state;
    assert(parse_command("M 2 768 512", &c));
    assert(!guard_command(&box, &state, &c));
    assert(memcmp(&state, &before, sizeof state) == 0);
    assert(parse_command("D 2", &c) && guard_command(&box, &state, &c));
    assert(state.down);
    assert(parse_command("D 3", &c) && !guard_command(&box, &state, &c));
    assert(parse_command("M 3 767 767", &c) && guard_command(&box, &state, &c));
    assert(parse_command("U 4", &c) && guard_command(&box, &state, &c));
    assert(!state.down && !state.positioned);
    assert(parse_command("D 5", &c) && !guard_command(&box, &state, &c));
    assert(parse_command("M 6 300 300", &c) && !guard_command(&box, &state, &c));
    assert(parse_command("Q 5", &c) && guard_command(&box, &state, &c));
    state.count = MX4_COMMAND_LIMIT;
    assert(parse_command("M 6 300 300", &c) && !guard_command(&box, &state, &c));
    puts("PASS: pure command parser, bounds, sequencing, press/release and cap guards");
    return 0;
}
