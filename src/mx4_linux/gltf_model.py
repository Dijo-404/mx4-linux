"""Small, offline glTF 2.0 loader and projection math for the supplied mouse.

Only embedded GLB resources and triangle meshes are accepted. The renderer uses
original attribute buffers, node transforms and textures without a model engine
or a numerical Python dependency. Quaternion ordering is (w, x, y, z).
"""

from dataclasses import dataclass
from functools import lru_cache
import itertools
import json
import math
from pathlib import Path
import struct


ASSETS = Path(__file__).parent / 'assets' / 'mouse'
MODEL_PATH = ASSETS / 'model.glb'
EXTENT = .17
IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)
_COMPONENTS = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
_SIZES = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}


def multiply(a, b):
    """Multiply row-major 4x4 matrices."""
    return tuple(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
                 for r in range(4) for c in range(4))


def transform(matrix, point, w=1.):
    return tuple(sum(matrix[r * 4 + k] * point[k] for k in range(3)) + matrix[r * 4 + 3] * w
                 for r in range(3))


def rotation_matrix(quaternion):
    w, x, y, z = quaternion
    length = math.sqrt(w*w + x*x + y*y + z*z)
    if not math.isfinite(length) or length < 1e-10:
        raise ValueError('Orientation must be a finite, nonzero quaternion')
    w, x, y, z = (v / length for v in (w, x, y, z))
    return (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), 0.,
            2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), 0.,
            2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), 0.,
            0., 0., 0., 1.)


def _quaternion(matrix):
    """Convert a rotation matrix to a normalized quaternion."""
    m = matrix
    trace = m[0] + m[5] + m[10]
    if trace > 0:
        s = math.sqrt(trace + 1.) * 2
        q = (s / 4, (m[9]-m[6])/s, (m[2]-m[8])/s, (m[4]-m[1])/s)
    elif m[0] > m[5] and m[0] > m[10]:
        s = math.sqrt(1 + m[0] - m[5] - m[10]) * 2
        q = ((m[9]-m[6])/s, s/4, (m[1]+m[4])/s, (m[2]+m[8])/s)
    elif m[5] > m[10]:
        s = math.sqrt(1 + m[5] - m[0] - m[10]) * 2
        q = ((m[2]-m[8])/s, (m[1]+m[4])/s, s/4, (m[6]+m[9])/s)
    else:
        s = math.sqrt(1 + m[10] - m[0] - m[5]) * 2
        q = ((m[4]-m[1])/s, (m[2]+m[8])/s, (m[6]+m[9])/s, s/4)
    norm = math.sqrt(sum(v*v for v in q))
    return tuple(v/norm for v in q)


def _normalized(vector):
    length = math.sqrt(sum(v*v for v in vector))
    return tuple(v/length for v in vector)


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def camera_orientation(offset, roll):
    """Match the existing orthographic product camera using model rotation."""
    z = _normalized(offset)
    x = _normalized(_cross((0., 1., 0.), z))
    y = _cross(z, x)
    c, s = math.cos(roll), math.sin(roll)
    right = tuple(c*a+s*b for a, b in zip(x, y))
    up = tuple(-s*a+c*b for a, b in zip(x, y))
    return _quaternion((*right, 0., *up, 0., *z, 0., 0., 0., 0., 1.))


BAKED_UNDERSIDE = camera_orientation((-.04, -.30, .07), math.pi)


POSES = {
    'overview': camera_orientation((-.15, .23, .13), 1.047198),
    'haptic': camera_orientation((-.30, .095, .055), .86),
    'underside': BAKED_UNDERSIDE,
}

def _node_matrix(node):
    if 'matrix' in node:
        values = node['matrix']
        return tuple(values[c*4+r] for r in range(4) for c in range(4))
    x, y, z, w = node.get('rotation', (0., 0., 0., 1.))
    matrix = list(rotation_matrix((w, x, y, z)))
    scale = node.get('scale', (1., 1., 1.))
    for r in range(3):
        for c in range(3):
            matrix[r*4+c] *= scale[c]
    for r, value in enumerate(node.get('translation', (0., 0., 0.))):
        matrix[r*4+3] = value
    return tuple(matrix)


@dataclass(frozen=True)
class Attribute:
    size: int
    component: int
    count: int
    offset: int
    stride: int
    normalized: bool = False


@dataclass(frozen=True)
class Primitive:
    name: str
    attributes: dict
    indices: Attribute
    material: int
    matrix: tuple


class ModelData:
    """Validated embedded geometry, material descriptions and source anchors."""
    def __init__(self, path=MODEL_PATH):
        data = Path(path).read_bytes()
        if len(data) < 20 or len(data) > 32 * 1024 * 1024:
            raise ValueError('Invalid GLB size')
        magic, version, length = struct.unpack_from('<4sII', data)
        if magic != b'glTF' or version != 2 or length != len(data):
            raise ValueError('Expected a complete glTF 2.0 binary')
        chunks = {}
        offset = 12
        while offset < len(data):
            if offset + 8 > len(data):
                raise ValueError('Truncated GLB chunk')
            size, kind = struct.unpack_from('<II', data, offset)
            offset += 8
            if offset + size > len(data):
                raise ValueError('Truncated GLB chunk')
            if kind in chunks:
                raise ValueError('Duplicate GLB chunk')
            chunks[kind] = data[offset:offset+size]
            offset += size
        if 0x4e4f534a not in chunks or 0x004e4942 not in chunks:
            raise ValueError('GLB must embed JSON and binary chunks')
        self.document = doc = json.loads(chunks[0x4e4f534a])
        self.binary = chunks[0x004e4942]
        if doc.get('asset', {}).get('version') != '2.0' or doc.get('extensionsRequired'):
            raise ValueError('Unsupported glTF version or required extension')
        buffers = doc.get('buffers', [])
        if len(buffers) != 1 or 'uri' in buffers[0] or buffers[0]['byteLength'] > len(self.binary):
            raise ValueError('Only a single embedded GLB buffer is supported')
        self.materials = doc.get('materials', [])
        self.primitives = []
        self.nodes = {}
        points = []
        visited = set()

        def visit(index, parent):
            if index in visited:
                raise ValueError('Repeated or cyclic model node')
            visited.add(index)
            node = doc['nodes'][index]
            matrix = multiply(parent, _node_matrix(node))
            self.nodes[node.get('name', str(index))] = matrix
            if 'mesh' in node:
                mesh = doc['meshes'][node['mesh']]
                for primitive in mesh['primitives']:
                    if primitive.get('mode', 4) != 4 or 'targets' in primitive:
                        raise ValueError('Only static triangle meshes are supported')
                    attrs = {key: self.attribute(value) for key, value in primitive['attributes'].items()}
                    if not {'POSITION', 'NORMAL', 'TANGENT', 'TEXCOORD_0'} <= attrs.keys():
                        raise ValueError('Missing product mesh attributes')
                    if attrs['POSITION'].size != 3 or attrs['POSITION'].component != 5126:
                        raise ValueError('Expected float positions')
                    indices = self.attribute(primitive['indices'])
                    if indices.size != 1 or indices.component not in (5121, 5123, 5125):
                        raise ValueError('Invalid triangle indices')
                    if any(attr.count != attrs['POSITION'].count for attr in attrs.values()):
                        raise ValueError('Mesh attribute counts differ')
                    material = primitive.get('material', 0)
                    if not 0 <= material < len(self.materials):
                        raise ValueError('Invalid material')
                    self.primitives.append(Primitive(mesh.get('name', ''), attrs, indices, material, matrix))
                    # Bounds are computed from actual positions, rather than trusting
                    # metadata that could clip the product or its projected controls.
                    attr = attrs['POSITION']
                    coordinates = [struct.unpack_from('<3f', self.binary, attr.offset+i*attr.stride)
                                   for i in range(attr.count)]
                    low = tuple(min(p[k] for p in coordinates) for k in range(3))
                    high = tuple(max(p[k] for p in coordinates) for k in range(3))
                    points.extend(transform(matrix, p) for p in itertools.product(*zip(low, high)))
            for child in node.get('children', []):
                visit(child, matrix)

        for node in doc['scenes'][doc.get('scene', 0)]['nodes']:
            visit(node, IDENTITY)
        if not points:
            raise ValueError('Model has no geometry')
        low = tuple(min(p[k] for p in points) for k in range(3))
        high = tuple(max(p[k] for p in points) for k in range(3))
        self.center = tuple((a+b)/2 for a, b in zip(low, high))
        self.bounds = (low, high)
        for image in doc.get('images', []):
            if 'uri' in image or image.get('mimeType') not in ('image/png', 'image/jpeg'):
                raise ValueError('Only embedded PNG/JPEG images are supported')
            self.buffer_view(image['bufferView'])
        self.anchors = self._anchors()

    def buffer_view(self, index):
        view = self.document['bufferViews'][index]
        start, length = view.get('byteOffset', 0), view['byteLength']
        if view.get('buffer', 0) != 0 or start < 0 or length < 0 or start+length > len(self.binary):
            raise ValueError('Buffer view exceeds embedded data')
        return self.binary[start:start+length]

    def attribute(self, index):
        accessor = self.document['accessors'][index]
        if 'sparse' in accessor:
            raise ValueError('Sparse geometry is not supported')
        view = self.document['bufferViews'][accessor['bufferView']]
        self.buffer_view(accessor['bufferView'])
        component = accessor['componentType']
        size = _SIZES[accessor['type']]
        item = size * _COMPONENTS[component]
        stride = view.get('byteStride', item)
        start, count = accessor.get('byteOffset', 0), accessor['count']
        if start < 0 or count < 1 or stride < item or start+(count-1)*stride+item > view['byteLength']:
            raise ValueError('Accessor exceeds its buffer view')
        return Attribute(size, component, count, view.get('byteOffset', 0)+start, stride,
                         bool(accessor.get('normalized', False)))

    def image_bytes(self, texture):
        image = self.document['textures'][texture]['source']
        return self.buffer_view(self.document['images'][image]['bufferView'])

    def _anchors(self):
        path = ASSETS / 'surface-anchors.json'
        if not path.exists() or 'METAL_EXTRA_PARTS' not in self.nodes:
            return {}
        groups = json.loads(path.read_text())
        matrix = self.nodes['METAL_EXTRA_PARTS']
        result = {}
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            for name, anchor in group.items():
                if 'source_mm' in anchor:
                    result[name] = transform(matrix, anchor['source_mm'])
                else:
                    # The three LED centers were registered in the underside image.
                    # Recover their points on the flat bottom surface once so they
                    # follow every subsequent 3D pose, including a free orbit.
                    x, y = anchor['normalized']
                    rotation = rotation_matrix(BAKED_UNDERSIDE)
                    cy = -self.center[1]
                    a = (x-.5)*EXTENT - rotation[1]*cy
                    b = (.5-y)*EXTENT - rotation[5]*cy
                    determinant = rotation[0]*rotation[6]-rotation[2]*rotation[4]
                    px = (a*rotation[6]-rotation[2]*b)/determinant
                    pz = (rotation[0]*b-a*rotation[4])/determinant
                    result[name] = (px+self.center[0], 0., pz+self.center[2])
        return result

    def project_anchor(self, name, orientation, zoom=1., aspect=1.):
        point = self.anchors[name]
        return project_point(point, self.center, orientation, zoom, aspect)


def project_point(point, center, orientation, zoom=1., aspect=1.):
    centered = tuple(a-b for a, b in zip(point, center))
    x, y, _ = transform(rotation_matrix(orientation), centered, 0.)
    return (.5+x*zoom/(EXTENT*max(1., aspect)), .5-y*zoom/(EXTENT*max(1., 1/aspect)))


@lru_cache(maxsize=1)
def load_model(path=MODEL_PATH):
    """Share the immutable source model between the home and detail canvases."""
    return ModelData(path)
