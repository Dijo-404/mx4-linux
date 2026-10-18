/* SPDX-License-Identifier: GPL-3.0-or-later
 * Finite current-cursor input. A sync confirms compositor processing, not an
 * application's delivery or a spatial target inferred from keyboard focus.
 */
#define _POSIX_C_SOURCE 200809L
#include "command.h"
#include "virtual-pointer-client.h"
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>

struct context {
    struct wl_display *display;
    struct wl_registry *registry;
    struct zwlr_virtual_pointer_manager_v1 *manager;
    struct zwlr_virtual_pointer_v1 *pointer;
    uint32_t version, name;
    bool removed;
};
static volatile sig_atomic_t interrupted;
static void stop(int sig) { (void)sig; interrupted = 1; }
static int64_t ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static bool flush(struct wl_display *d, int64_t end) {
    while (wl_display_flush(d) < 0) {
        if (errno != EAGAIN || ms() >= end) return false;
        struct pollfd fd = {wl_display_get_fd(d), POLLOUT, 0};
        int r = poll(&fd, 1, (int)(end - ms()));
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0 || fd.revents & (POLLERR | POLLHUP | POLLNVAL)) return false;
    }
    return true;
}
static void done(void *data, struct wl_callback *cb, uint32_t serial) {
    (void)serial; *(bool *)data = true; wl_callback_destroy(cb);
}
static const struct wl_callback_listener sync_listener = {.done = done};
static bool sync_display(struct context *c, int64_t end) {
    bool finished = false;
    struct wl_callback *cb = wl_display_sync(c->display);
    if (!cb) return false;
    if (wl_callback_add_listener(cb, &sync_listener, &finished)) { wl_callback_destroy(cb); return false; }
    while (!finished) {
        while (wl_display_prepare_read(c->display)) {
            if (wl_display_dispatch_pending(c->display) < 0) goto fail;
            if (finished) return !c->removed;
            if (ms() >= end) goto fail;
        }
        if (!flush(c->display, end) || ms() >= end) { wl_display_cancel_read(c->display); goto fail; }
        struct pollfd fd = {wl_display_get_fd(c->display), POLLIN, 0};
        int r = poll(&fd, 1, (int)(end - ms()));
        if (r < 0 && errno == EINTR) { wl_display_cancel_read(c->display); continue; }
        if (r <= 0 || fd.revents & (POLLERR | POLLHUP | POLLNVAL)) { wl_display_cancel_read(c->display); goto fail; }
        if (wl_display_read_events(c->display) < 0 || wl_display_dispatch_pending(c->display) < 0) goto fail;
    }
    return !c->removed;
fail:
    if (!finished) wl_callback_destroy(cb);
    return false;
}
static void global(void *data, struct wl_registry *r, uint32_t name, const char *interface, uint32_t version) {
    struct context *c = data;
    if (strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name) || !version) return;
    if (c->manager) { c->removed = true; return; }
    c->version = version > 2 ? 2 : version;
    c->manager = wl_registry_bind(r, name, &zwlr_virtual_pointer_manager_v1_interface, c->version);
    c->name = name;
}
static void removed(void *data, struct wl_registry *r, uint32_t name) {
    (void)r; struct context *c = data; if (name == c->name) c->removed = true;
}
static const struct wl_registry_listener registry_listener = {.global = global, .global_remove = removed};
static bool alive(pid_t parent, int64_t end) { return !interrupted && getppid() == parent && ms() < end; }
/* EOF, parent loss and signal cancel a pending operation. No input is accepted
 * except one exact go line. During a click, any readable control pipe cancels.
 */
static bool pause_control(pid_t parent, int64_t end, int duration) {
    int64_t until = ms() + duration;
    if (until > end) until = end;
    while (ms() < until) {
        if (!alive(parent, end)) return false;
        struct pollfd fd = {STDIN_FILENO, POLLIN, 0};
        int r = poll(&fd, 1, 5);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0 || (r > 0 && fd.revents)) return false;
    }
    return alive(parent, end);
}
int main(int argc, char **argv) {
    bool check = argc == 2 && !strcmp(argv[1], "--check");
    bool validate = argc == 5 && !strcmp(argv[1], "--validate");
    struct command cmd = {0};
    if (!check && (!(argc == 5 && (!strcmp(argv[1], "--run") || validate)) ||
                   !parse_command(argc - 2, (const char *const *)(argv + 2), &cmd))) return 2;
    if (validate) return 0; /* Display-free build/parser verification. */
    struct sigaction sa = {.sa_handler = stop};
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) || sigaction(SIGTERM, &sa, NULL) || sigaction(SIGHUP, &sa, NULL)) return 2;
    signal(SIGPIPE, SIG_IGN);
    int flags = fcntl(STDOUT_FILENO, F_GETFL);
    if (flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK)) return 2;
    setvbuf(stdout, NULL, _IONBF, 0);
    struct context c = {0};
    bool ok = false, held = false, release_ok = true, synced = false;
    pid_t parent = getppid();
    int64_t end = ms() + 1800;
    c.display = wl_display_connect(NULL);
    if (!c.display) goto cleanup;
    c.registry = wl_display_get_registry(c.display);
    if (!c.registry || wl_registry_add_listener(c.registry, &registry_listener, &c) ||
        !sync_display(&c, end) || !c.manager || c.removed) goto cleanup;
    if (check) { ok = true; goto cleanup; } /* Registry only; NEVER create pointer. */
    if (!alive(parent, end)) goto cleanup;
    c.pointer = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(c.manager, NULL);
    if (!c.pointer || !sync_display(&c, end) || !alive(parent, end)) goto cleanup;
    if (puts("{\"event\":\"ready\"}") < 0) goto cleanup;
    struct pollfd control = {STDIN_FILENO, POLLIN, 0};
    int r;
    do { r = poll(&control, 1, (int)(end - ms())); } while (r < 0 && errno == EINTR && alive(parent, end));
    if (r <= 0 || control.revents != POLLIN || !alive(parent, end)) goto cleanup;
    char input[8];
    if (read(STDIN_FILENO, input, sizeof input) != 3 || memcmp(input, "go\n", 3)) goto cleanup;
    if (!alive(parent, end)) goto cleanup;
    if (cmd.kind == 'c') {
        for (unsigned i = 0; i < cmd.count; ++i) {
            if (!alive(parent, end)) goto cleanup;
            held = true;
            zwlr_virtual_pointer_v1_button(c.pointer, (uint32_t)ms(), cmd.button, WL_POINTER_BUTTON_STATE_PRESSED);
            zwlr_virtual_pointer_v1_frame(c.pointer);
            if (!sync_display(&c, end) || !pause_control(parent, end, 25)) goto cleanup;
            zwlr_virtual_pointer_v1_button(c.pointer, (uint32_t)ms(), cmd.button, WL_POINTER_BUTTON_STATE_RELEASED);
            zwlr_virtual_pointer_v1_frame(c.pointer);
            if (!sync_display(&c, end)) goto cleanup;
            held = false;
            if (i + 1 < cmd.count && !pause_control(parent, end, 60)) goto cleanup;
        }
    } else if (cmd.kind == 'm') {
        zwlr_virtual_pointer_v1_motion(c.pointer, (uint32_t)ms(), wl_fixed_from_int(cmd.x), wl_fixed_from_int(cmd.y));
        zwlr_virtual_pointer_v1_frame(c.pointer);
        if (!sync_display(&c, end)) goto cleanup;
    } else {
        zwlr_virtual_pointer_v1_axis_source(c.pointer, WL_POINTER_AXIS_SOURCE_WHEEL);
        if (cmd.x) zwlr_virtual_pointer_v1_axis_discrete(c.pointer, (uint32_t)ms(), WL_POINTER_AXIS_HORIZONTAL_SCROLL,
            wl_fixed_from_int(cmd.x * 15), cmd.x);
        if (cmd.y) zwlr_virtual_pointer_v1_axis_discrete(c.pointer, (uint32_t)ms(), WL_POINTER_AXIS_VERTICAL_SCROLL,
            wl_fixed_from_int(cmd.y * 15), cmd.y);
        zwlr_virtual_pointer_v1_frame(c.pointer);
        if (!sync_display(&c, end)) goto cleanup;
    }
    ok = alive(parent, end);
cleanup:
    if (c.pointer && c.display) {
        int64_t cleanup_end = ms() + 600;
        if (held) {
            zwlr_virtual_pointer_v1_button(c.pointer, (uint32_t)ms(), cmd.button, WL_POINTER_BUTTON_STATE_RELEASED);
            zwlr_virtual_pointer_v1_frame(c.pointer);
            release_ok = flush(c.display, cleanup_end) && sync_display(&c, cleanup_end);
        }
        synced = flush(c.display, cleanup_end) && sync_display(&c, cleanup_end);
        zwlr_virtual_pointer_v1_destroy(c.pointer);
        (void)flush(c.display, cleanup_end);
    }
    if (c.manager) zwlr_virtual_pointer_manager_v1_destroy(c.manager);
    if (c.registry) wl_registry_destroy(c.registry);
    if (c.display) { (void)flush(c.display, ms() + 50); wl_display_disconnect(c.display); }
    if (check) printf("{\"available\":%s,\"protocol_version\":%u}\n", ok ? "true" : "false", c.version);
    else printf("{\"event\":\"result\",\"ok\":%s,\"released\":%s,\"compositor_sync\":%s}\n",
        ok ? "true" : "false", release_ok ? "true" : "false", synced ? "true" : "false");
    return ok && release_ok && (check || synced) ? 0 : 1;
}
