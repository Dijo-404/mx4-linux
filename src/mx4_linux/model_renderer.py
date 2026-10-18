"""Native, demand-rendered GLB product canvas using GTK's own GL context.

libepoxy is already a GTK dependency. Its entry points are bound with ctypes so
installing a separate Python OpenGL or web runtime is unnecessary. Context or
model failures are reported to the host, which retains the static illustration.
"""

import ctypes as C
from ctypes.util import find_library
import logging
import math

from .gltf_model import EXTENT, POSES, load_model, rotation_matrix
from .gtk_support import Gdk, Gtk

import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf


LOG = logging.getLogger(__name__)
VERTEX_SHADER = '''#version 330 core
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec4 tangent;
layout(location=3) in vec2 uv;
uniform mat4 node;
uniform mat4 rotation;
uniform vec3 center;
uniform vec2 scale;
out vec3 vNormal;
out vec3 vTangent;
out float vHandedness;
out vec2 vUV;
out vec3 vPosition;
void main() {
    vec3 world = (node * vec4(position, 1.0)).xyz - center;
    vec3 p = (rotation * vec4(world, 0.0)).xyz;
    gl_Position = vec4(p.x * scale.x, p.y * scale.y, -p.z * 5.0, 1.0);
    vNormal = normalize(mat3(rotation * node) * normal);
    vTangent = normalize(mat3(rotation * node) * tangent.xyz);
    vHandedness = tangent.w;
    vPosition = p;
    vUV = uv;
}
'''
FRAGMENT_SHADER = '''#version 330 core
in vec3 vNormal;
in vec3 vTangent;
in float vHandedness;
in vec2 vUV;
in vec3 vPosition;
uniform sampler2D baseMap;
uniform sampler2D normalMap;
uniform sampler2D ormMap;
uniform vec4 baseFactor;
uniform vec2 materialFactors;
uniform bool transparent;
out vec4 color;
const float PI = 3.14159265359;
vec3 light(vec3 n, vec3 v, vec3 l, vec3 radiance, vec3 albedo, float metal, float rough) {
    vec3 h = normalize(v + l);
    float nl = max(dot(n,l),0.0);
    float nv = max(dot(n,v),0.001);
    float nh = max(dot(n,h),0.0);
    float vh = max(dot(v,h),0.0);
    // Broad studio sources retain soft reflections even on polished wheels.
    float a = max(rough * rough, 0.12);
    float a2 = a*a;
    float d = a2 / max(PI * pow(nh*nh*(a2-1.0)+1.0,2.0),0.001);
    float k = pow(rough+1.0,2.0)/8.0;
    float g = nv/(nv*(1.0-k)+k) * nl/(nl*(1.0-k)+k);
    vec3 f0 = mix(vec3(0.04),albedo,metal);
    vec3 f = f0+(1.0-f0)*pow(1.0-vh,5.0);
    vec3 spec = d*g*f/max(4.0*nv*nl,0.001);
    vec3 diffuse = (1.0-f)*(1.0-metal)*albedo/PI;
    return (diffuse+spec)*radiance*nl;
}
vec3 filmic(vec3 x) {
    return clamp((x*(2.51*x+0.03))/(x*(2.43*x+0.59)+0.14),0.0,1.0);
}
vec3 srgb(vec3 x) {
    return mix(12.92*x,1.055*pow(x,vec3(1.0/2.4))-0.055,step(vec3(0.0031308),x));
}
void main() {
    vec4 base = texture(baseMap,vUV)*baseFactor;
    vec3 albedo = pow(base.rgb,vec3(2.2));
    vec3 orm = texture(ormMap,vUV).rgb;
    float rough = clamp(orm.g*materialFactors.y,0.08,1.0);
    float metal = clamp(orm.b*materialFactors.x,0.0,1.0);
    vec3 n = normalize(vNormal);
    vec3 t = normalize(vTangent-n*dot(n,vTangent));
    vec3 b = normalize(cross(n,t))*vHandedness;
    n = normalize(mat3(t,b,n)*(texture(normalMap,vUV).xyz*2.0-1.0));
    if (!gl_FrontFacing) n = -n;
    vec3 v = vec3(0.0,0.0,1.0);
    // Fixed neutral studio lighting makes the shape readable throughout a turn.
    float hemisphere = 0.60 + 0.20*max(n.y,0.0) + 0.14*max(n.z,0.0);
    vec3 ambient = albedo*hemisphere*mix(1.0,0.85,metal)*mix(0.45,1.0,orm.r);
    vec3 lit = ambient;
    lit += light(n,v,normalize(vec3(-0.65,0.85,1.4)),vec3(2.6),albedo,metal,rough);
    lit += light(n,v,normalize(vec3(1.0,0.2,0.7)),vec3(1.2),albedo,metal,rough);
    lit += light(n,v,normalize(vec3(-0.25,-0.8,0.6)),vec3(0.65),albedo,metal,rough);
    float alpha = transparent ? base.a : 1.0;
    if (alpha < 0.01) discard;
    color = vec4(srgb(filmic(lit * 0.82))*alpha,alpha);
}
'''


class GL:
    """Typed libepoxy entry points; resolved only with a current GL context."""
    def __init__(self):
        self.library = C.CDLL(find_library('epoxy') or 'libepoxy.so.0')
        u, i, f, p, b = C.c_uint, C.c_int, C.c_float, C.c_void_p, C.c_ubyte
        signatures = {
            'CreateShader': (u, u), 'ShaderSource': (None, u, i, p, p),
            'CompileShader': (None, u), 'GetShaderiv': (None, u, u, p),
            'GetShaderInfoLog': (None, u, i, p, p), 'DeleteShader': (None, u),
            'CreateProgram': (u,), 'AttachShader': (None, u, u), 'LinkProgram': (None, u),
            'GetProgramiv': (None, u, u, p), 'GetProgramInfoLog': (None, u, i, p, p),
            'DeleteProgram': (None, u), 'UseProgram': (None, u),
            'GenBuffers': (None, i, p), 'BindBuffer': (None, u, u),
            'BufferData': (None, u, C.c_ssize_t, p, u), 'DeleteBuffers': (None, i, p),
            'GenVertexArrays': (None, i, p), 'BindVertexArray': (None, u),
            'DeleteVertexArrays': (None, i, p), 'EnableVertexAttribArray': (None, u),
            'VertexAttribPointer': (None, u, i, u, b, i, p),
            'GenTextures': (None, i, p), 'BindTexture': (None, u, u),
            'TexImage2D': (None, u, i, i, i, i, i, u, u, p),
            'TexParameteri': (None, u, u, i), 'GenerateMipmap': (None, u),
            'PixelStorei': (None, u, i), 'DeleteTextures': (None, i, p),
            'ActiveTexture': (None, u), 'GetUniformLocation': (i, u, C.c_char_p),
            'UniformMatrix4fv': (None, i, i, b, p), 'Uniform3f': (None, i, f, f, f),
            'Uniform4f': (None, i, f, f, f, f), 'Uniform2f': (None, i, f, f),
            'Uniform1i': (None, i, i), 'Viewport': (None, i, i, i, i),
            'ClearColor': (None, f, f, f, f), 'Clear': (None, u),
            'Enable': (None, u), 'Disable': (None, u), 'DepthFunc': (None, u),
            'DepthMask': (None, b), 'BlendFunc': (None, u, u),
            'DrawElements': (None, u, i, u, p), 'GetError': (u,),
            'ReadPixels': (None, i, i, i, i, u, u, p),
            'GetIntegerv': (None, u, p), 'GenFramebuffers': (None, i, p),
            'BindFramebuffer': (None, u, u), 'DeleteFramebuffers': (None, i, p),
            'GenRenderbuffers': (None, i, p), 'BindRenderbuffer': (None, u, u),
            'DeleteRenderbuffers': (None, i, p),
            'RenderbufferStorageMultisample': (None, u, i, u, i, i),
            'FramebufferRenderbuffer': (None, u, u, u, u),
            'CheckFramebufferStatus': (u, u),
            'BlitFramebuffer': (None, i, i, i, i, i, i, i, i, u, u),
        }
        for name, (result, *arguments) in signatures.items():
            address = C.c_void_p.in_dll(self.library, 'epoxy_gl'+name).value
            setattr(self, name, C.CFUNCTYPE(result, *arguments)(address))

    def shader(self, kind, source):
        shader = self.CreateShader(kind)
        encoded = C.c_char_p(source.encode())
        self.ShaderSource(shader, 1, C.byref(encoded), None)
        self.CompileShader(shader)
        status = C.c_int()
        self.GetShaderiv(shader, 0x8B81, C.byref(status))
        if not status.value:
            message = C.create_string_buffer(4096)
            self.GetShaderInfoLog(shader, len(message), None, message)
            self.DeleteShader(shader)
            raise RuntimeError(message.value.decode(errors='replace'))
        return shader

    def program(self):
        shaders = []
        program = 0
        try:
            shaders.append(self.shader(0x8B31, VERTEX_SHADER))
            shaders.append(self.shader(0x8B30, FRAGMENT_SHADER))
            program = self.CreateProgram()
            for shader in shaders:
                self.AttachShader(program, shader)
            self.LinkProgram(program)
            status = C.c_int()
            self.GetProgramiv(program, 0x8B82, C.byref(status))
            if not status.value:
                message = C.create_string_buffer(4096)
                self.GetProgramInfoLog(program, len(message), None, message)
                raise RuntimeError(message.value.decode(errors='replace'))
            return program
        except Exception:
            if program:
                self.DeleteProgram(program)
            raise
        finally:
            for shader in shaders:
                self.DeleteShader(shader)


class ModelRenderer(Gtk.GLArea):
    """Transparent mouse viewport. The host owns all interaction and timing."""
    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, can_target=False)
        self.set_required_version(3, 3)
        if hasattr(self, 'set_allowed_apis'):
            self.set_allowed_apis(Gdk.GLAPI.GL)
        else:
            self.set_use_es(False)
        self.set_has_depth_buffer(True)
        self.set_auto_render(False)
        self.orientation = POSES['overview']
        self.zoom = 1.
        self.ready = False
        self.failure = None
        self.on_ready = None
        self.on_failure = None
        self.model = None
        self.gl = None
        self.program = 0
        self.buffer = C.c_uint()
        self.vertex_arrays = []
        self.textures = {}
        self.locations = {}
        self.multisample = C.c_uint()
        self.color_buffer = C.c_uint()
        self.depth_buffer = C.c_uint()
        self.render_size = (0, 0)
        self.samples = 0
        self.disposed = False
        self.connect('realize', self._realize)
        self.connect('unrealize', self._unrealize)
        self.connect('render', self._render)
        self.connect('resize', lambda *_: self.queue_render())

    def set_orientation(self, orientation):
        rotation_matrix(orientation)  # Reject nonfinite quaternions before GL upload.
        self.orientation = tuple(orientation)
        self.queue_render()

    def set_zoom(self, zoom):
        if not math.isfinite(zoom):
            return
        self.zoom = max(.65, min(1.8, zoom))
        self.queue_render()

    def project_anchor(self, name):
        model = self.model or load_model()
        aspect = max(1, self.get_width()) / max(1, self.get_height())
        return model.project_anchor(name, self.orientation, self.zoom, aspect)

    def anchor_visible(self, name):
        rotation = rotation_matrix(self.orientation)
        if name in ('easy_switch', 'power') or name.startswith('channel_'):
            normal = (0., -1., 0.)
        elif name in ('haptic_panel', 'forward_button', 'back_button', 'extra_thumb_button', 'thumb_wheel'):
            normal = (-.9, .45, 0.)
        else:
            normal = (0., 1., 0.)
        return sum(rotation[8+i]*normal[i] for i in range(3)) > .10

    def _fail(self, error):
        self.failure = str(error)
        self.ready = False
        LOG.warning('Native mouse rendering unavailable: %s', error)
        if self.on_failure:
            self.on_failure(self.failure)

    def _realize(self, _area):
        if self.disposed:
            return
        self.make_current()
        error = self.get_error()
        if error:
            self._fail(error)
            return
        try:
            self.model = load_model()
            self.gl = gl = GL()
            self.program = gl.program()
            maximum = C.c_int()
            gl.GetIntegerv(0x8D57, C.byref(maximum))
            self.samples = min(4, maximum.value)
            for name in ('node', 'rotation', 'center', 'scale', 'baseMap', 'normalMap', 'ormMap',
                         'baseFactor', 'materialFactors', 'transparent'):
                self.locations[name] = gl.GetUniformLocation(self.program, name.encode())
            gl.GenBuffers(1, C.byref(self.buffer))
            gl.BindBuffer(0x8892, self.buffer.value)
            geometry_end = max(a.offset+(a.count-1)*a.stride+a.size*({5121: 1, 5123: 2}.get(a.component, 4))
                               for primitive in self.model.primitives
                               for a in (*primitive.attributes.values(), primitive.indices))
            data = self.model.binary[:geometry_end]
            gl.BufferData(0x8892, len(data), data, 0x88E4)
            # Opaque surfaces establish depth before the two transparent button covers.
            primitives = sorted(self.model.primitives,
                                key=lambda p: self.model.materials[p.material].get('alphaMode') == 'BLEND')
            for primitive in primitives:
                vao = C.c_uint()
                gl.GenVertexArrays(1, C.byref(vao))
                self.vertex_arrays.append((vao, primitive))
                gl.BindVertexArray(vao.value)
                gl.BindBuffer(0x8892, self.buffer.value)
                gl.BindBuffer(0x8893, self.buffer.value)
                for location, name in enumerate(('POSITION', 'NORMAL', 'TANGENT', 'TEXCOORD_0')):
                    attribute = primitive.attributes[name]
                    gl.EnableVertexAttribArray(location)
                    gl.VertexAttribPointer(location, attribute.size, attribute.component,
                                           attribute.normalized, attribute.stride, C.c_void_p(attribute.offset))
                material = self.model.materials[primitive.material]
                pbr = material.get('pbrMetallicRoughness', {})
                for texture in (pbr.get('baseColorTexture'), material.get('normalTexture'),
                                pbr.get('metallicRoughnessTexture')):
                    if texture and texture['index'] not in self.textures:
                        self._upload_texture(texture['index'])
            gl.BindVertexArray(0)
            gl.BindBuffer(0x8892, 0)
            error = gl.GetError()
            if error:
                raise RuntimeError(f'OpenGL setup error 0x{error:04x}')
            self.failure = None
            self.queue_render()
        except Exception as error:
            self._release_gl()
            self._fail(error)

    def _upload_texture(self, index):
        loader = GdkPixbuf.PixbufLoader.new()
        loader.write(self.model.image_bytes(index))
        loader.close()
        pixbuf = loader.get_pixbuf()
        if not pixbuf:
            raise ValueError('Embedded material texture could not be decoded')
        if not pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width > 2048 or height > 2048:
            raise ValueError('Embedded material texture exceeds the product texture limit')
        pixels = pixbuf.get_pixels()
        stride = pixbuf.get_rowstride()
        if stride != width*4:
            pixels = b''.join(pixels[row*stride:row*stride+width*4] for row in range(height))
        texture = C.c_uint()
        gl = self.gl
        gl.GenTextures(1, C.byref(texture))
        self.textures[index] = texture
        gl.BindTexture(0x0DE1, texture.value)
        gl.PixelStorei(0x0CF5, 1)
        gl.TexImage2D(0x0DE1, 0, 0x8058, width, height, 0, 0x1908, 0x1401, pixels)
        gl.TexParameteri(0x0DE1, 0x2801, 0x2703)
        gl.TexParameteri(0x0DE1, 0x2800, 0x2601)
        gl.TexParameteri(0x0DE1, 0x2802, 0x2901)
        gl.TexParameteri(0x0DE1, 0x2803, 0x2901)
        gl.GenerateMipmap(0x0DE1)

    def _ensure_multisample(self, width, height):
        if self.samples < 2 or self.render_size == (width, height):
            return
        self._release_multisample()
        gl = self.gl
        gl.GenFramebuffers(1, C.byref(self.multisample))
        gl.BindFramebuffer(0x8D40, self.multisample.value)
        for buffer, format_, attachment in ((self.color_buffer, 0x8058, 0x8CE0),
                                             (self.depth_buffer, 0x81A6, 0x8D00)):
            gl.GenRenderbuffers(1, C.byref(buffer))
            gl.BindRenderbuffer(0x8D41, buffer.value)
            gl.RenderbufferStorageMultisample(0x8D41, self.samples, format_, width, height)
            gl.FramebufferRenderbuffer(0x8D40, attachment, 0x8D41, buffer.value)
        if gl.CheckFramebufferStatus(0x8D40) != 0x8CD5:
            raise RuntimeError('Multisample product framebuffer is incomplete')
        gl.BindRenderbuffer(0x8D41, 0)
        self.render_size = (width, height)

    def _release_multisample(self):
        for buffer in (self.color_buffer, self.depth_buffer):
            if buffer.value:
                self.gl.DeleteRenderbuffers(1, C.byref(buffer))
        if self.multisample.value:
            self.gl.DeleteFramebuffers(1, C.byref(self.multisample))
        self.multisample = C.c_uint()
        self.color_buffer = C.c_uint()
        self.depth_buffer = C.c_uint()
        self.render_size = (0, 0)

    def _matrix(self, location, value):
        self.gl.UniformMatrix4fv(self.locations[location], 1, True, (C.c_float*16)(*value))

    def _render(self, _area, _context):
        if self.disposed or self.failure or not self.program:
            return False
        target = C.c_int()
        try:
            gl, loc = self.gl, self.locations
            width, height = max(1, self.get_width()), max(1, self.get_height())
            factor = self.get_scale_factor()
            gl.GetIntegerv(0x8CA6, C.byref(target))
            pixel_width, pixel_height = width*factor, height*factor
            self._ensure_multisample(pixel_width, pixel_height)
            if self.multisample.value:
                gl.BindFramebuffer(0x8D40, self.multisample.value)
                gl.Enable(0x809D)
            gl.Viewport(0, 0, pixel_width, pixel_height)
            gl.Disable(0x0C11)
            gl.Disable(0x8DB9)  # Shader writes sRGB, matching GTK's legacy GLArea texture.
            gl.ClearColor(0., 0., 0., 0.)
            gl.DepthMask(True)
            gl.Clear(0x4000 | 0x0100)
            gl.Enable(0x0B71)
            gl.DepthFunc(0x0203)
            gl.Disable(0x0B44)
            gl.Enable(0x0BE2)
            gl.BlendFunc(1, 0x0303)  # Premultiplied alpha, including the GTK texture.
            gl.UseProgram(self.program)
            self._matrix('rotation', rotation_matrix(self.orientation))
            gl.Uniform3f(loc['center'], *self.model.center)
            aspect = width/height
            gl.Uniform2f(loc['scale'], 2*self.zoom/(EXTENT*max(1., aspect)),
                         2*self.zoom/(EXTENT*max(1., 1/aspect)))
            for vao, primitive in self.vertex_arrays:
                gl.BindVertexArray(vao.value)
                self._matrix('node', primitive.matrix)
                material = self.model.materials[primitive.material]
                pbr = material.get('pbrMetallicRoughness', {})
                transparent = material.get('alphaMode') == 'BLEND'
                gl.Uniform1i(loc['transparent'], transparent)
                gl.DepthMask(not transparent)
                gl.Uniform4f(loc['baseFactor'], *pbr.get('baseColorFactor', (1., 1., 1., 1.)))
                gl.Uniform2f(loc['materialFactors'], pbr.get('metallicFactor', 1.), pbr.get('roughnessFactor', 1.))
                for unit, (uniform, spec) in enumerate((('baseMap', pbr.get('baseColorTexture')),
                                                        ('normalMap', material.get('normalTexture')),
                                                        ('ormMap', pbr.get('metallicRoughnessTexture')))):
                    gl.ActiveTexture(0x84C0+unit)
                    gl.BindTexture(0x0DE1, self.textures[spec['index']].value if spec else 0)
                    gl.Uniform1i(loc[uniform], unit)
                gl.DrawElements(4, primitive.indices.count, primitive.indices.component,
                                C.c_void_p(primitive.indices.offset))
            gl.DepthMask(True)
            gl.BindVertexArray(0)
            gl.UseProgram(0)
            if self.multisample.value:
                gl.BindFramebuffer(0x8CA8, self.multisample.value)
                gl.BindFramebuffer(0x8CA9, target.value)
                gl.BlitFramebuffer(0, 0, pixel_width, pixel_height, 0, 0, pixel_width, pixel_height,
                                   0x4000, 0x2600)
                gl.BindFramebuffer(0x8D40, target.value)
            error = gl.GetError()
            if error:
                raise RuntimeError(f'OpenGL render error 0x{error:04x}')
            if not self.ready:
                self.ready = True
                if self.on_ready:
                    self.on_ready()
            return True
        except Exception as error:
            if self.gl:
                self.gl.BindFramebuffer(0x8D40, target.value)
                self._release_gl()
            self._fail(error)
            return False

    def _release_gl(self):
        gl = self.gl
        if not gl:
            return
        self._release_multisample()
        for texture in self.textures.values():
            gl.DeleteTextures(1, C.byref(texture))
        for vao, _ in self.vertex_arrays:
            gl.DeleteVertexArrays(1, C.byref(vao))
        if self.buffer.value:
            gl.DeleteBuffers(1, C.byref(self.buffer))
        if self.program:
            gl.DeleteProgram(self.program)
        self.textures.clear()
        self.vertex_arrays.clear()
        self.buffer = C.c_uint()
        self.program = 0
        self.ready = False
        self.gl = None

    def _unrealize(self, _area):
        self.make_current()
        if not self.get_error():
            self._release_gl()
        else:
            # A lost context owns no recoverable resources; do not issue GL calls.
            self.multisample = C.c_uint()
            self.color_buffer = C.c_uint()
            self.depth_buffer = C.c_uint()
            self.render_size = (0, 0)
            self.textures.clear()
            self.vertex_arrays.clear()
            self.buffer = C.c_uint()
            self.program = 0
            self.ready = False
            self.gl = None

    def dispose_art(self):
        self.disposed = True
        self.on_ready = self.on_failure = None
        if self.get_realized():
            self.make_current()
            if not self.get_error():
                self._release_gl()
        self.model = None
