"""Camera playback stays registered, pausable and bounded across GTK lifecycles."""

import hashlib
import json
from pathlib import Path
from time import monotonic, sleep

import pytest

from mx4_linux.mouse_motion import OrbitMotion, anchor_position


ASSETS = Path(__file__).parents[1] / 'src' / 'mx4_linux' / 'assets' / 'mouse'
ATLAS = json.loads((ASSETS / 'orbit.json').read_text())


def test_hidden_time_does_not_advance_camera_on_resume():
    motion = OrbitMotion()
    for step in range(61):
        motion.advance(step / 60)
    phase, pose = motion.phase, motion.pose
    assert pose > .1
    motion.suspend()
    assert motion.advance(1000) == pose
    assert motion.phase == phase
    assert motion.advance(1000 + 1 / 60) != pose


def test_camera_steering_stays_inside_registered_views():
    motion = OrbitMotion()
    motion.steer(1000)
    for step in range(1500):
        assert -1 <= motion.advance(step / 60) <= 1
    motion.steer(float('nan'))
    assert motion.pointer == 1
    pose = motion.pose
    assert motion.advance(float('nan')) == pose


def test_baked_camera_centers_preserve_original_surface_registration():
    original = json.loads((ASSETS / 'surface-anchors.json').read_text())
    for view, frames in ATLAS['views'].items():
        assert len(frames) == 17
        for name, source in original[view].items():
            assert anchor_position(frames, 0, name) == pytest.approx(source['normalized'], abs=1e-7)
            assert anchor_position(frames, -.9, name) != anchor_position(frames, .9, name)
        for frame in frames:
            assert hashlib.sha256((ASSETS / frame['file']).read_bytes()).hexdigest() == frame['sha256']
            assert all(0 < coordinate < 1 for anchor in frame['anchors'].values() for coordinate in anchor)


@pytest.fixture
def native_settings():
    pytest.importorskip('gi')
    from mx4_linux.gtk_support import Adw, Gtk
    if not Gtk.init_check():
        pytest.skip('Native display required')
    Adw.init()
    settings = Gtk.Settings.get_default()
    previous = settings.get_property('gtk-enable-animations')
    settings.set_property('gtk-enable-animations', True)
    yield settings
    settings.set_property('gtk-enable-animations', previous)


@pytest.fixture
def native_view(native_settings):
    from mx4_linux.gtk_support import Gtk
    from mx4_linux.mouse_view import MouseView
    view = MouseView(lambda *_: None)
    view.set_targets({'wheel': ('Wheel', 'main_wheel', (20, -20))})
    window = Gtk.Window(default_width=500, default_height=420, child=view)
    window.present()
    pump_until(lambda: view.get_mapped() and (view.picture.ready or view.picture.renderer.failure), seconds=5)
    try:
        yield view, window, native_settings
    finally:
        view.dispose_art()
        window.destroy()


@pytest.fixture
def native_picture(native_settings):
    from mx4_linux.gtk_support import Gtk
    from mx4_linux.mouse_view import ModelPicture
    picture = ModelPicture(interactive=True)
    picture.set_filename(str(ASSETS / 'overview.png'))
    window = Gtk.Window(default_width=360, default_height=360, child=picture)
    window.present()
    pump_until(lambda: picture.get_mapped() and (picture.ready or picture.renderer.failure), seconds=5)
    try:
        yield picture, window, native_settings
    finally:
        picture.dispose_art()
        window.destroy()


def pump_until(condition, seconds=2):
    from mx4_linux.gtk_support import GLib
    context = GLib.MainContext.default()
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if condition():
            return
        sleep(.005)
    assert condition(), 'Native mouse condition timed out'


def require_live_model(picture):
    if not picture.ready:
        pytest.skip(f'Native OpenGL unavailable: {picture.renderer.failure}')


def test_native_page_transitions_reach_side_and_underside_then_park(native_view):
    from mx4_linux.model_renderer import POSES
    view, _window, _settings = native_view
    picture = view.picture
    require_live_model(picture)
    assert not picture.motion.active and picture.ambient_tick_id == 0
    for scene, anchor in [('haptic', 'haptic_panel'), ('underside', 'easy_switch'), ('overview', 'main_wheel')]:
        before = picture.motion.orientation
        view.set_targets({'target': ('Configure', anchor, (20, -20))},
                         underside=scene == 'underside', view=scene)
        assert picture.motion.orientation == before  # No image swap at page entry.
        assert picture.motion.active and picture.ambient_tick_id
        assert not view.markers['target'].get_visible()
        pump_until(lambda: picture.motion.orientation != before)
        assert picture.motion.orientation != POSES[scene]
        assert picture.motion.active
        pump_until(lambda: picture.ambient_tick_id == 0)
        assert picture.motion.orientation == pytest.approx(POSES[scene])
        assert not picture.motion.active and view.markers['target'].get_visible()
        assert picture.fallback.ambient_tick_id == 0


def test_native_pause_resume_reduced_motion_and_unmap_stop_frame_clock(native_view):
    from mx4_linux.model_renderer import POSES
    view, _window, settings = native_view
    picture = view.picture
    require_live_model(picture)
    picture.set_view('haptic')
    before = picture.motion.orientation
    pump_until(lambda: picture.motion.orientation != before)
    view.set_motion_enabled(False)
    paused = picture.motion.orientation
    assert picture.ambient_tick_id == 0 and picture.motion.active
    view.spin()
    view.pulse()
    assert view.tick_id == view.pulse_source == 0
    view.set_motion_enabled(True)
    assert picture.motion.orientation == paused and picture.ambient_tick_id
    pump_until(lambda: not picture.motion.active)
    assert picture.motion.orientation == pytest.approx(POSES['haptic'])
    picture.set_view('underside')
    assert picture.ambient_tick_id
    settings.set_property('gtk-enable-animations', False)
    assert picture.ambient_tick_id == 0 and not picture.motion.active
    assert picture.motion.orientation == pytest.approx(POSES['underside'])
    settings.set_property('gtk-enable-animations', True)
    assert picture.ambient_tick_id == 0  # An idle page never starts a background orbit.
    picture.set_view('overview')
    assert picture.ambient_tick_id
    view.set_visible(False)
    pump_until(lambda: not view.get_mapped())
    assert picture.ambient_tick_id == 0
    hidden = picture.motion.orientation
    view.set_visible(True)
    assert picture.motion.orientation == hidden
    pump_until(lambda: not picture.motion.active)
    assert picture.motion.orientation == pytest.approx(POSES['overview'])


def test_native_projected_targets_follow_live_page_pose(native_view):
    from math import isfinite
    view, _window, _settings = native_view
    require_live_model(view.picture)
    view.set_motion_enabled(False)
    for scene, names in [('overview', ('main_wheel', 'back_button', 'haptic_panel')),
                         ('haptic', ('haptic_panel',)),
                         ('underside', ('easy_switch', 'power', 'channel_1_indicator'))]:
        specs = {name: ('Configure', name, (20, -20)) for name in names}
        view.set_targets(specs, underside=scene == 'underside', view=scene)
        x, y, size = view.art_bounds
        for name in names:
            anchor = view.picture.anchor(name)
            assert all(isfinite(coordinate) and 0 < coordinate < 1 for coordinate in anchor)
            assert view._surface_position(name) == pytest.approx((x + anchor[0] * size, y + anchor[1] * size))
            assert view.markers[name].get_visible()


def test_native_failure_restores_registered_baked_targets_and_bounds_cache(native_view):
    from mx4_linux.mouse_view import TEXTURE_BUDGET, TEXTURES
    view, _window, _settings = native_view
    picture = view.picture
    picture.set_view('haptic')
    picture.renderer._fail('Simulated display context failure')
    assert not picture.ready and not picture.renderer.get_visible()
    assert picture.fallback.get_opacity() == 1
    assert picture.ambient_tick_id == 0 and not picture.motion.active
    for underside, name in ((False, 'main_wheel'), (True, 'easy_switch')):
        view.set_targets({'target': ('Configure', name, (20, -20))}, underside=underside)
        assert not picture.motion.active and picture.ambient_tick_id == 0
        for frame in ATLAS['views']['underside' if underside else 'overview']:
            picture.set_camera_pose(frame['pose'])
            x, y, size = view.art_bounds
            assert view._surface_position(name) == pytest.approx(
                (x + frame['anchors'][name][0] * size, y + frame['anchors'][name][1] * size))
        assert view.markers['target'].get_visible()
    assert sum(texture.get_width() * texture.get_height() * 4 for texture in TEXTURES.values()) <= TEXTURE_BUDGET


def test_native_wheel_preview_restores_live_model_and_cancels_on_hide(native_view):
    view, _window, _settings = native_view
    picture = view.picture
    require_live_model(picture)
    original = picture.motion.orientation
    view.spin()
    assert picture.ambient_tick_id == 0 and view.tick_id
    assert picture.camera_hold and picture.renderer.get_opacity() == 0
    assert picture.fallback.get_opacity() == 1
    pump_until(lambda: picture.override)
    pump_until(lambda: view.tick_id == 0)
    assert not picture.camera_hold and not picture.override
    assert picture.renderer.get_opacity() == 1 and picture.fallback.get_opacity() == 0
    assert picture.motion.orientation == original and picture.ambient_tick_id == 0
    view.spin()
    view.set_visible(False)
    pump_until(lambda: not view.get_mapped())
    assert view.tick_id == picture.ambient_tick_id == 0
    assert not picture.camera_hold and not picture.override


def test_native_home_drag_zoom_keyboard_and_reset_keep_navigation_independent(native_picture):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from mx4_linux.gtk_support import Gdk, Gtk
    from mx4_linux.model_motion import MAX_ZOOM, MIN_ZOOM
    picture, window, settings = native_picture
    require_live_model(picture)
    home = picture.motion.orientation
    gesture = SimpleNamespace(set_state=Mock())
    picture._drag_begin(gesture, 160, 140)
    gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
    assert window.get_focus() is picture and picture.get_cursor().get_name() == 'grabbing'
    picture._drag_update(None, 230, -290)
    dragged = picture.motion.orientation
    assert dragged != home
    picture._drag_update(None, 230, -290)
    assert picture.motion.orientation == dragged  # Gesture offsets are cumulative.
    picture._drag_end(None, 230, -290)
    assert not picture.motion.dragging and picture.ambient_tick_id == 0
    assert picture.get_cursor().get_name() == 'grab'
    assert picture._scroll(None, 0, -1000) and picture.motion.zoom == MAX_ZOOM
    assert picture._scroll(None, 0, 1000) and picture.motion.zoom == MIN_ZOOM
    for key in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
        before = picture.motion.orientation
        assert picture._key_pressed(None, key, 0, 0)
        assert picture.motion.orientation != before
    before = picture.motion.orientation
    for modifier in (Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.ALT_MASK, Gdk.ModifierType.SUPER_MASK):
        assert not picture._key_pressed(None, Gdk.KEY_Left, 0, modifier)
        assert picture.motion.orientation == before
    settings.set_property('gtk-enable-animations', False)
    assert picture._key_pressed(None, Gdk.KEY_Home, 0, 0)
    assert picture.motion.orientation == pytest.approx(home) and picture.motion.zoom == 1
    assert not picture.motion.active and picture.ambient_tick_id == 0
    # Reduced motion still permits direct, user-driven orbiting.
    picture._drag_begin(None, 100, 100)
    picture._drag_update(None, -120, 160)
    assert picture.motion.orientation != home
    picture.set_visible(False)
    assert not picture.motion.dragging and picture.get_cursor().get_name() == 'grab'
    assert picture.ambient_tick_id == 0


def test_native_home_failure_preserves_image_and_releases_scroll(native_picture):
    from mx4_linux.gtk_support import Gdk
    picture, _window, _settings = native_picture
    picture.renderer._fail('Simulated missing OpenGL support')
    assert not picture.ready and picture.fallback.get_opacity() == 1
    assert picture.fallback.texture is not None and not picture.renderer.get_visible()
    assert not picture._scroll(None, 0, -1)
    assert not picture._key_pressed(None, Gdk.KEY_Left, 0, 0)
    picture._drag_begin(None, 100, 100)
    assert not picture.motion.dragging and picture.ambient_tick_id == 0
    assert 'unavailable' in picture.get_tooltip_text()


def test_native_dispose_disconnects_motion_preferences_and_callbacks(native_picture):
    picture, _window, settings = native_picture
    picture.dispose_art()
    assert picture.disposed and picture.settings_handler == 0
    assert picture.fallback.settings_handler == 0 and picture.ambient_tick_id == 0
    assert picture.renderer.on_ready is None and picture.renderer.on_failure is None
    settings.set_property('gtk-enable-animations', False)
    settings.set_property('gtk-enable-animations', True)
    assert picture.ambient_tick_id == 0
