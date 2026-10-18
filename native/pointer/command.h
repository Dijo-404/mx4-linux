/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef MX4_POINTER_COMMAND_H
#define MX4_POINTER_COMMAND_H
#include <errno.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
struct command { char kind; unsigned button, count; int x, y; };
static bool integer(const char *s, int bound, int *out) {
    if (!s || !*s) return false;
    const char *p = s;
    if (*p == '-') ++p;
    if (!*p) return false;
    for (; *p; ++p) if (*p < '0' || *p > '9') return false;
    errno = 0;
    char *end;
    long v = strtol(s, &end, 10);
    if (errno || *end || v < -bound || v > bound) return false;
    *out = (int)v;
    return true;
}
static bool parse_command(int argc, const char *const *argv, struct command *out) {
    if (argc != 3) return false;
    memset(out, 0, sizeof *out);
    if (!strcmp(argv[0], "click")) {
        int count;
        if (!integer(argv[2], 2, &count) || count < 1) return false;
        if (!strcmp(argv[1], "left")) out->button = 272;
        else if (!strcmp(argv[1], "right")) out->button = 273;
        else if (!strcmp(argv[1], "middle")) out->button = 274;
        else return false;
        out->kind = 'c'; out->count = (unsigned)count;
        return true;
    }
    int bound;
    if (!strcmp(argv[0], "move")) { out->kind = 'm'; bound = 4096; }
    else if (!strcmp(argv[0], "scroll")) { out->kind = 's'; bound = 20; }
    else return false;
    return integer(argv[1], bound, &out->x) && integer(argv[2], bound, &out->y) && (out->x || out->y);
}
#endif
