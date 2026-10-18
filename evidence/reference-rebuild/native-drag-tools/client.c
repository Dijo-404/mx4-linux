#define _POSIX_C_SOURCE 200809L
#include "command.h"
#include "virtual-pointer-client.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>

/* Temporary verification client. Never launched during preparation.
 * Coordinates are output-local logical pixels multiplied by 256.
 * ACK means compositor sync, NOT a GTK native-motion/ownership check.
 * The future owned-GTK harness must verify that check before every D.
 */
#define MAX_OUTPUTS 16
#define WHOLE_MS 30000
#define IDLE_MS 3000
#define SYNC_MS 700
#define CLEANUP_MS 400
#define BTN_LEFT 272u

struct output {
    struct wl_output *proxy;
    uint32_t registry_name;
    char name[65];
};
struct context {
    struct wl_display *display;
    struct wl_registry *registry;
    struct zwlr_virtual_pointer_manager_v1 *manager;
    struct zwlr_virtual_pointer_v1 *pointer;
    struct output outputs[MAX_OUTPUTS];
    unsigned output_count;
    uint32_t manager_name, chosen_name;
    bool fatal;
};
static volatile sig_atomic_t interrupted;

static int64_t milliseconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}
static void signal_received(int number) { (void)number; interrupted = 1; }

static bool flush_until(struct wl_display *display, int64_t end) {
    for (;;) {
        if (wl_display_flush(display) >= 0) return true;
        if (errno != EAGAIN) return false;
        int64_t left = end - milliseconds();
        if (left <= 0) return false;
        struct pollfd fd = {wl_display_get_fd(display), POLLOUT, 0};
        int result = poll(&fd, 1, (int)left);
        if (result < 0 && errno == EINTR) continue;
        if (result <= 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) return false;
    }
}

static void sync_done(void *data, struct wl_callback *callback, uint32_t serial) {
    (void)serial;
    *(bool *)data = true;
    wl_callback_destroy(callback);
}
static const struct wl_callback_listener sync_listener = {.done = sync_done};

/* Bounded roundtrip without wl_display_roundtrip's unbounded blocking read. */
static bool compositor_sync(struct wl_display *display, int64_t end) {
    bool done = false;
    struct wl_callback *callback = wl_display_sync(display);
    if (!callback) return false;
    if (wl_callback_add_listener(callback, &sync_listener, &done) != 0) {
        wl_callback_destroy(callback);
        return false;
    }
    while (!done) {
        while (wl_display_prepare_read(display) != 0) {
            if (wl_display_dispatch_pending(display) < 0) goto failed;
            if (done) return true;
            if (milliseconds() >= end) goto failed;
        }
        if (!flush_until(display, end)) {
            wl_display_cancel_read(display);
            goto failed;
        }
        int64_t remaining = end - milliseconds();
        if (remaining <= 0) {
            wl_display_cancel_read(display);
            goto failed;
        }
        struct pollfd fd = {wl_display_get_fd(display), POLLIN, 0};
        int result = poll(&fd, 1, (int)remaining);
        if (result < 0 && errno == EINTR) {
            wl_display_cancel_read(display);
            continue;
        }
        if (result <= 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) {
            wl_display_cancel_read(display);
            goto failed;
        }
        if (fd.revents & POLLIN) {
            if (wl_display_read_events(display) < 0) goto failed;
        } else wl_display_cancel_read(display);
        if (wl_display_dispatch_pending(display) < 0) goto failed;
    }
    return true;
failed:
    if (!done) wl_callback_destroy(callback);
    return false;
}

static void output_geometry(void *data, struct wl_output *output, int32_t x, int32_t y,
        int32_t width, int32_t height, int32_t subpixel, const char *make,
        const char *model, int32_t transform) {
    (void)data; (void)output; (void)x; (void)y; (void)width; (void)height;
    (void)subpixel; (void)make; (void)model; (void)transform;
}
static void output_mode(void *data, struct wl_output *output, uint32_t flags,
        int32_t width, int32_t height, int32_t refresh) {
    (void)data; (void)output; (void)flags; (void)width; (void)height; (void)refresh;
}
static void output_done(void *data, struct wl_output *output) { (void)data; (void)output; }
static void output_scale(void *data, struct wl_output *output, int32_t scale) {
    (void)data; (void)output; (void)scale;
}
static void output_name(void *data, struct wl_output *output, const char *name) {
    (void)output;
    struct output *item = data;
    if (strlen(name) >= sizeof item->name) return;
    memcpy(item->name, name, strlen(name) + 1);
}
static void output_description(void *data, struct wl_output *output, const char *description) {
    (void)data; (void)output; (void)description;
}
static const struct wl_output_listener output_listener = {
    .geometry = output_geometry, .mode = output_mode, .done = output_done,
    .scale = output_scale, .name = output_name, .description = output_description
};

static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
        const char *interface, uint32_t version) {
    struct context *ctx = data;
    if (!strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name) && version >= 2) {
        if (ctx->manager) { ctx->fatal = true; return; }
        ctx->manager = wl_registry_bind(registry, name,
            &zwlr_virtual_pointer_manager_v1_interface, 2);
        ctx->manager_name = name;
        if (!ctx->manager) ctx->fatal = true;
    } else if (!strcmp(interface, wl_output_interface.name) && version >= 4) {
        if (ctx->output_count == MAX_OUTPUTS) { ctx->fatal = true; return; }
        struct output *item = &ctx->outputs[ctx->output_count++];
        item->proxy = wl_registry_bind(registry, name, &wl_output_interface, 4);
        item->registry_name = name;
        if (!item->proxy || wl_output_add_listener(item->proxy, &output_listener, item) != 0)
            ctx->fatal = true;
    }
}
static void registry_removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)registry;
    struct context *ctx = data;
    if (name == ctx->chosen_name || name == ctx->manager_name) ctx->fatal = true;
    for (unsigned i = 0; i < ctx->output_count; ++i)
        if (ctx->outputs[i].registry_name == name) ctx->outputs[i].name[0] = '\0';
}
static const struct wl_registry_listener registry_listener = {
    .global = registry_global, .global_remove = registry_removed
};

static bool same_parent(pid_t parent) { return getppid() == parent; }
static int64_t command_deadline(int64_t whole_end) {
    int64_t short_end = milliseconds() + SYNC_MS;
    return short_end < whole_end ? short_end : whole_end;
}

static bool execute_command(struct context *ctx, const struct command *command,
        uint32_t width, uint32_t height, int64_t whole_end) {
    uint32_t now = (uint32_t)milliseconds();
    switch (command->kind) {
    case 'M':
        zwlr_virtual_pointer_v1_motion_absolute(ctx->pointer, now,
            command->x, command->y, width * 256u, height * 256u);
        break;
    case 'D':
        zwlr_virtual_pointer_v1_button(ctx->pointer, now, BTN_LEFT,
            WL_POINTER_BUTTON_STATE_PRESSED);
        break;
    case 'U':
        zwlr_virtual_pointer_v1_button(ctx->pointer, now, BTN_LEFT,
            WL_POINTER_BUTTON_STATE_RELEASED);
        break;
    case 'Q': return true;  /* release-first cleanup below handles down state */
    default: return false;
    }
    zwlr_virtual_pointer_v1_frame(ctx->pointer);
    return compositor_sync(ctx->display, command_deadline(whole_end)) && !ctx->fatal;
}

int main(int argc, char **argv) {
    /* No display connection occurs until ALL explicit --run arguments pass. */
    uint32_t parent_number, width, height, left, top, box_width, box_height;
    if (argc != 14 || strcmp(argv[1], "--run") || strcmp(argv[2], "--owner-pid") ||
        strcmp(argv[4], "--output") || strcmp(argv[6], "--extent") ||
        strcmp(argv[9], "--bounds") ||
        !uint32_token(argv[3], &parent_number) || parent_number < 2 || parent_number > INT_MAX ||
        !uint32_token(argv[7], &width) || !uint32_token(argv[8], &height) ||
        !uint32_token(argv[10], &left) || !uint32_token(argv[11], &top) ||
        !uint32_token(argv[12], &box_width) || !uint32_token(argv[13], &box_height) ||
        !width || !height || width > MX4_MAX_LOGICAL_EXTENT || height > MX4_MAX_LOGICAL_EXTENT ||
        !box_width || !box_height || left >= width || top >= height ||
        box_width > width - left || box_height > height - top ||
        !argv[5][0] || strlen(argv[5]) > 64 || !getenv("WAYLAND_DISPLAY") ||
        !same_parent((pid_t)parent_number)) {
        fputs("Refused: explicit --run, live parent, named output, valid logical extent and bounds required.\n", stderr);
        return 2;
    }
    for (const unsigned char *p = (const unsigned char *)argv[5]; *p; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
              (*p >= '0' && *p <= '9') || *p == '-' || *p == '_' || *p == '.')) {
            fputs("Refused: invalid output name.\n", stderr); return 2;
        }
    struct stat stdin_info, stdout_info;
    if (fstat(STDIN_FILENO, &stdin_info) != 0 || !S_ISFIFO(stdin_info.st_mode) ||
        fstat(STDOUT_FILENO, &stdout_info) != 0 || !S_ISFIFO(stdout_info.st_mode)) {
        fputs("Refused: stdin/stdout must be owned harness pipes.\n", stderr);
        return 2;
    }
    /* Nonblocking ACK output prevents an unread owner pipe from defeating the
     * whole/idle deadline while a virtual primary button might be down.
     */
    int input_flags = fcntl(STDIN_FILENO, F_GETFL);
    int output_flags = fcntl(STDOUT_FILENO, F_GETFL);
    if (input_flags < 0 || output_flags < 0 ||
        fcntl(STDIN_FILENO, F_SETFL, input_flags | O_NONBLOCK) == -1 ||
        fcntl(STDOUT_FILENO, F_SETFL, output_flags | O_NONBLOCK) == -1) {
        fputs("Refused: cannot bound owned pipe I/O.\n", stderr);
        return 2;
    }
    const struct bounds box = {left * 256u, top * 256u,
        (left + box_width) * 256u, (top + box_height) * 256u};
    const pid_t parent = (pid_t)parent_number;
    struct context ctx = {0};
    struct guard state = {0};
    const char *failure = "initialization";
    int status = 1;
    int64_t whole_end = milliseconds() + WHOLE_MS;
    struct sigaction action = {.sa_handler = signal_received};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL)) return 2;
    signal(SIGPIPE, SIG_IGN);
    setvbuf(stdout, NULL, _IONBF, 0);

    ctx.display = wl_display_connect(NULL);
    if (!ctx.display) { failure = "display-connect"; goto cleanup; }
    ctx.registry = wl_display_get_registry(ctx.display);
    if (!ctx.registry || wl_registry_add_listener(ctx.registry, &registry_listener, &ctx) != 0 ||
        !compositor_sync(ctx.display, command_deadline(whole_end)) ||
        !compositor_sync(ctx.display, command_deadline(whole_end)) || ctx.fatal || !ctx.manager) {
        failure = "registry-capability"; goto cleanup;
    }
    struct wl_output *output = NULL;
    for (unsigned i = 0; i < ctx.output_count; ++i) {
        if (!strcmp(ctx.outputs[i].name, argv[5])) {
            if (output) { failure = "ambiguous-output"; goto cleanup; }
            output = ctx.outputs[i].proxy;
            ctx.chosen_name = ctx.outputs[i].registry_name;
        }
    }
    if (!output || !same_parent(parent) || interrupted || milliseconds() >= whole_end) {
        failure = "output-or-owner"; goto cleanup;
    }
    ctx.pointer = zwlr_virtual_pointer_manager_v1_create_virtual_pointer_with_output(
        ctx.manager, NULL, output);
    if (!ctx.pointer || !compositor_sync(ctx.display, command_deadline(whole_end)) || ctx.fatal) {
        failure = "pointer-create"; goto cleanup;
    }
    if (puts("{\"event\":\"ready\",\"ack_scope\":\"compositor-sync-only\",\"command_limit\":128,\"idle_ms\":3000,\"whole_ms\":30000}") < 0) {
        failure = "stdout-lost"; goto cleanup;
    }
    char line[128];
    size_t used = 0;
    int64_t idle_end = milliseconds() + IDLE_MS;
    for (;;) {
        if (interrupted) { failure = "signal"; goto cleanup; }
        if (!same_parent(parent)) { failure = "owner-exited"; goto cleanup; }
        int64_t now = milliseconds();
        if (now >= whole_end) { failure = "whole-deadline"; goto cleanup; }
        if (now >= idle_end) { failure = "idle-deadline"; goto cleanup; }
        if (ctx.fatal || wl_display_dispatch_pending(ctx.display) < 0) {
            failure = "display-or-output-lost"; goto cleanup;
        }
        int64_t end = idle_end < whole_end ? idle_end : whole_end;
        struct pollfd fds[2] = {{STDIN_FILENO, POLLIN, 0},
            {wl_display_get_fd(ctx.display), POLLIN, 0}};
        int result = poll(fds, 2, (int)(end - now));
        if (result < 0 && errno == EINTR) continue;
        if (result < 0) { failure = "poll"; goto cleanup; }
        if (fds[1].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            failure = "display-lost"; goto cleanup;
        }
        if (fds[1].revents & POLLIN) {
            if (wl_display_dispatch(ctx.display) < 0 || ctx.fatal) {
                failure = "display-or-output-lost"; goto cleanup;
            }
        }
        if (fds[0].revents & (POLLERR | POLLNVAL)) { failure = "stdin-lost"; goto cleanup; }
        if (fds[0].revents & (POLLIN | POLLHUP)) {
            char input[128];
            ssize_t count = read(STDIN_FILENO, input, sizeof input);
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (count <= 0) { failure = used ? "truncated-command" : "eof"; goto cleanup; }
            /* Stop-and-wait only. The owner must inspect each ACK and actual
             * native GTK motion before issuing a following press command.
             */
            for (ssize_t i = 0; i < count - 1; ++i)
                if (input[i] == '\n') { failure = "pipelined-command"; goto cleanup; }
            for (ssize_t i = 0; i < count; ++i) {
                if (!input[i] || used == sizeof line - 1) { failure = "command-length"; goto cleanup; }
                if (input[i] != '\n') { line[used++] = input[i]; continue; }
                line[used] = '\0'; used = 0;
                struct command command;
                if (!same_parent(parent) || interrupted || milliseconds() >= whole_end) {
                    failure = "owner-signal-or-deadline"; goto cleanup;
                }
                if (!parse_command(line, &command) || !guard_command(&box, &state, &command)) {
                    failure = "command-guard"; goto cleanup;
                }
                if (!execute_command(&ctx, &command, width, height, whole_end)) {
                    failure = "command-sync"; goto cleanup;
                }
                if (printf("{\"event\":\"ack\",\"sequence\":%u,\"kind\":\"%c\",\"ack_scope\":\"compositor-sync-only\"}\n",
                    command.sequence, command.kind) < 0) {
                    failure = "stdout-lost"; goto cleanup;
                }
                if (command.kind == 'Q') { status = 0; failure = NULL; goto cleanup; }
                idle_end = milliseconds() + IDLE_MS;
            }
        }
    }

cleanup:;
    bool attempted = false, flushed = false, synced = false;
    /* Independent cleanup deadline: even if the whole/idle deadline expired,
     * attempt RELEASE, FRAME and flush before pointer destroy/disconnect.
     * A dead server cannot confirm delivery; report that truthfully.
     */
    if (ctx.pointer && ctx.display) {
        attempted = true;
        zwlr_virtual_pointer_v1_button(ctx.pointer, (uint32_t)milliseconds(), BTN_LEFT,
            WL_POINTER_BUTTON_STATE_RELEASED);
        zwlr_virtual_pointer_v1_frame(ctx.pointer);
        int64_t cleanup_end = milliseconds() + CLEANUP_MS;
        flushed = flush_until(ctx.display, cleanup_end);
        if (flushed) synced = compositor_sync(ctx.display, cleanup_end);
        zwlr_virtual_pointer_v1_destroy(ctx.pointer);
        (void)flush_until(ctx.display, cleanup_end);
    }
    if (ctx.manager) zwlr_virtual_pointer_manager_v1_destroy(ctx.manager);
    for (unsigned i = 0; i < ctx.output_count; ++i)
        if (ctx.outputs[i].proxy) wl_output_release(ctx.outputs[i].proxy);
    if (ctx.registry) wl_registry_destroy(ctx.registry);
    if (ctx.display) {
        (void)flush_until(ctx.display, milliseconds() + 50);
        wl_display_disconnect(ctx.display);
    }
    printf("{\"event\":\"cleanup\",\"release_attempted\":%s,\"release_flushed\":%s,\"release_compositor_sync\":%s,\"status\":%d}\n",
        attempted ? "true" : "false", flushed ? "true" : "false",
        synced ? "true" : "false", status);
    if (failure) fprintf(stderr, "Stopped: %s.\n", failure);
    return status;
}
