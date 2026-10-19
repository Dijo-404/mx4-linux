"""Original geometry and registered controls survive continuous 3D rotation."""

import hashlib
import json
import math
import struct
from time import monotonic, sleep

import pytest

from mx4_linux.gltf_model import (
    ASSETS, BAKED_UNDERSIDE, EXTENT, MODEL_PATH, ModelData, POSES, contact_shadow, load_model,
    project_point, rotation_matrix, transform,
)


def test_runtime_model_is_the_supplied_embedded_glb():
    model = load_model()
    manifest = json.loads((ASSETS / 'manifest.json').read_text())
    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == manifest['source']['sha256']
    assert len(model.primitives) == 8
    assert sum(p.indices.count // 3 for p in model.primitives) == 64001
    assert len(model.document['images']) == 25
    assert model.center == pytest.approx(manifest['cameras']['common']['target_world_m'], abs=2e-8)
    assert all(model.image_bytes(t['index']).startswith(b'\x89PNG\r\n\x1a\n')
               for material in model.materials for t in (material['pbrMetallicRoughness']['baseColorTexture'],))


def test_source_anchors_project_to_the_reference_including_recovered_leds():
    model = load_model()
    original = json.loads((ASSETS / 'surface-anchors.json').read_text())
    for scene in ('overview', 'underside'):
        orientation = POSES['overview'] if scene == 'overview' else BAKED_UNDERSIDE
        for name, anchor in original[scene].items():
            assert model.project_anchor(name, orientation) == pytest.approx(anchor['normalized'], abs=2e-8)


def test_three_dimensional_rotation_and_zoom_keep_surface_registration():
    model = load_model()
    for angle in (-math.pi, -.7, .7, math.pi):
        pose = (math.cos(angle/2), math.sin(angle/2), 0., 0.)
        projected = model.project_anchor('haptic_panel', pose, 1.15, 1.5)
        delta = tuple(a-b for a, b in zip(model.anchors['haptic_panel'], model.center))
        rotated = transform(rotation_matrix(pose), delta, 0.)
        assert projected == pytest.approx((.5+rotated[0]*1.15/(EXTENT*1.5), .5-rotated[1]*1.15/EXTENT))
    assert model.project_anchor('haptic_panel', POSES['overview']) != model.project_anchor('haptic_panel', POSES['haptic'])
    assert project_point((0., 0., 0.), (0., 0., 0.), POSES['overview'], 1.7, .6) == (.5, .5)


@pytest.mark.parametrize('pose', [POSES['overview'], POSES['haptic'], BAKED_UNDERSIDE, (0., 0., 0., 1.)])
def test_contact_shadow_sits_under_the_true_silhouette_for_any_pose(pose):
    model = load_model()
    assert 500 < len(model.samples) < 1500  # Sparse enough to evaluate every frame.
    rotation = rotation_matrix(pose)
    ys = [transform(rotation, p, 0.)[1] for p in model.samples]
    xs = [transform(rotation, p, 0.)[0] for p in model.samples]
    scale = 2 / EXTENT
    cx, cy, rx, ry = contact_shadow(model.samples, pose)
    assert cy == pytest.approx(min(ys) * scale)
    assert min(xs) * scale <= cx <= max(xs) * scale
    assert 0 < ry < rx <= (max(xs) - min(xs)) * scale
    wide = contact_shadow(model.samples, pose, 1.4, 2.)
    assert wide == pytest.approx((cx * .7, cy * 1.4, rx * .7, ry * 1.4))


def test_nonfinite_or_empty_rotations_are_rejected():
    for pose in ((0., 0., 0., 0.), (math.nan, 0., 0., 1.), (math.inf, 0., 0., 1.)):
        with pytest.raises(ValueError, match='quaternion'):
            rotation_matrix(pose)


def _rewrite_glb(tmp_path, update):
    model = load_model()
    document = json.loads(json.dumps(model.document))
    update(document)
    data = json.dumps(document).encode()
    data += b' ' * (-len(data) % 4)
    binary = model.binary
    total = 12+8+len(data)+8+len(binary)
    path = tmp_path / 'invalid.glb'
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, total)
                     +struct.pack('<II', len(data), 0x4e4f534a)+data
                     +struct.pack('<II', len(binary), 0x004e4942)+binary)
    return path


def test_glb_rejects_truncation_and_external_resources(tmp_path):
    path = tmp_path / 'truncated.glb'
    path.write_bytes(MODEL_PATH.read_bytes()[:-4])
    with pytest.raises(ValueError, match='complete'):
        ModelData(path)
    path = _rewrite_glb(tmp_path, lambda doc: doc['buffers'][0].update(uri='https://example.com/private.bin'))
    with pytest.raises(ValueError, match='embedded'):
        ModelData(path)
    path = _rewrite_glb(tmp_path, lambda doc: doc['images'][0].update(uri='file:///not-an-asset.png'))
    with pytest.raises(ValueError, match='embedded'):
        ModelData(path)


def test_glb_rejects_out_of_bounds_geometry_and_cycles(tmp_path):
    path = _rewrite_glb(tmp_path, lambda doc: doc['accessors'][1].update(count=99999999))
    with pytest.raises(ValueError, match='Accessor exceeds'):
        ModelData(path)
    path = _rewrite_glb(tmp_path, lambda doc: doc['nodes'][0]['children'].append(0))
    with pytest.raises(ValueError, match='cyclic'):
        ModelData(path)


def _pump(condition, timeout=5):
    from mx4_linux.gtk_support import GLib
    context = GLib.MainContext.default()
    deadline = monotonic()+timeout
    while monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if condition():
            return
        sleep(.005)
    assert condition(), 'Native GL condition timed out'


@pytest.fixture
def native_renderer():
    pytest.importorskip('gi')
    from mx4_linux.gtk_support import Gtk
    from mx4_linux.model_renderer import ModelRenderer
    if not Gtk.init_check():
        pytest.skip('Native display required')
    renderer = ModelRenderer()
    window = Gtk.Window(default_width=500, default_height=420, child=renderer)
    window.present()
    _pump(lambda: renderer.ready or renderer.failure)
    if renderer.failure:
        window.destroy()
        pytest.skip(f'OpenGL context unavailable: {renderer.failure}')
    yield renderer, window
    renderer.dispose_art()
    window.destroy()


def test_native_textured_frame_changes_with_real_rotation(native_renderer):
    import ctypes as C
    renderer, _window = native_renderer
    renderer.make_current()
    renderer.attach_buffers()
    renderer._render(renderer, renderer.get_context())
    width = renderer.get_width()*renderer.get_scale_factor()
    height = renderer.get_height()*renderer.get_scale_factor()
    pixels = (C.c_ubyte*(width*height*4))()
    renderer.gl.ReadPixels(0, 0, width, height, 0x1908, 0x1401, pixels)
    first = bytes(pixels)
    assert any(first[3::4]) and first[3] == 0
    if renderer.samples >= 2:
        assert renderer.multisample.value and renderer.render_size == (width, height)
        assert any(0 < alpha < 255 for alpha in first[3::4])
    renderer.set_orientation(POSES['haptic'])
    renderer._render(renderer, renderer.get_context())
    renderer.gl.ReadPixels(0, 0, width, height, 0x1908, 0x1401, pixels)
    assert first != bytes(pixels)
    assert renderer.gl.GetError() == 0
    assert renderer.get_auto_render() is False


def test_native_projection_visibility_and_gpu_cleanup(native_renderer):
    renderer, _window = native_renderer
    renderer.set_orientation(POSES['overview'])
    assert not renderer.anchor_visible('easy_switch')
    assert renderer.anchor_visible('main_wheel')
    renderer.set_orientation(POSES['underside'])
    assert renderer.anchor_visible('easy_switch')
    assert renderer.anchor_visible('channel_2_indicator')
    assert not renderer.anchor_visible('main_wheel')
    point = renderer.project_anchor('easy_switch')
    renderer.set_zoom(1.2)
    assert renderer.project_anchor('easy_switch') == pytest.approx(tuple(.5+(v-.5)*1.2 for v in point))
    renderer.dispose_art()
    assert not renderer.ready and not renderer.program and not renderer.buffer.value
    assert not renderer.multisample.value and not renderer.vertex_arrays and not renderer.textures


def test_native_context_recreation_releases_and_reloads_resources(native_renderer):
    renderer, window = native_renderer
    window.set_child(None)
    assert not renderer.ready and not renderer.program and renderer.gl is None
    window.set_child(renderer)
    _pump(lambda: renderer.ready or renderer.failure)
    assert renderer.ready and renderer.failure is None
    assert renderer.program and len(renderer.vertex_arrays) == 8
