from pathlib import Path
import struct, json, collections, os

raw = Path(
    os.environ.get(
        "MX4_MODEL_PATH", str(Path(__file__).resolve().parent.parent / "models" / "mxmaster4mac-whitesilver-ar-360.glb")
    )
).read_bytes()
jl = struct.unpack_from("<I", raw, 12)[0]
d = json.loads(raw[20 : 20 + jl])
b = memoryview(raw)[28 + jl :]


def a(i):
    a = d["accessors"][i]
    v = d["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC3": 3}[a["type"]]
    fmt = {5125: "I", 5126: "f"}[a["componentType"]]
    st = v.get("byteStride", n * 4)
    o = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    return [struct.unpack_from("<" + fmt * n, b, o + j * st) for j in range(a["count"])]


p = d["meshes"][0]["primitives"][0]
pos = a(p["attributes"]["POSITION"])
idx = [r[0] for r in a(p["indices"])]
parent = list(range(len(pos)))


def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


def union(a, b):
    a, b = find(a), find(b)
    if a != b:
        parent[b] = a


weld = {}
for i, v in enumerate(pos):
    key = tuple(round(c, 4) for c in v)
    if key in weld:
        union(i, weld[key])
    else:
        weld[key] = i
for i in range(0, len(idx), 3):
    union(idx[i], idx[i + 1])
    union(idx[i], idx[i + 2])
c = collections.Counter(find(idx[i]) for i in range(0, len(idx), 3))
root = c.most_common(1)[0][0]
wheel = []
rest = []
for i in range(0, len(idx), 3):
    (wheel if find(idx[i]) == root else rest).extend(idx[i : i + 3])
pts = [pos[i] for i in set(wheel)]
lo = [min(v[k] for v in pts) for k in range(3)]
hi = [max(v[k] for v in pts) for k in range(3)]
result = {
    "mesh_name": "METAL_EXTRA_PARTS",
    "wheel_indices": wheel,
    "remaining_indices": rest,
    "pivot_source_mm": [(lo[k] + hi[k]) / 2 for k in range(3)],
    "axis_source": [1, 0, 0],
    "wheel_triangles": len(wheel) // 3,
    "remaining_triangles": len(rest) // 3,
}
(Path(__file__).resolve().parent / "wheel-island.json").write_text(json.dumps(result, separators=(",", ":")))
print({k: v for k, v in result.items() if not k.endswith("indices")})
