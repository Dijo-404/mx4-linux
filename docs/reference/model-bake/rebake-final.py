"""Bake user supplied model using already provisioned local research tools only."""

from pathlib import Path
import hashlib, json, os, subprocess, math

p = Path(__file__).resolve().parent
src = Path(os.environ.get("MX4_MODEL_PATH", str(p.parent / "models" / "mxmaster4mac-whitesilver-ar-360.glb"))).resolve()
os.environ["MX4_MODEL_PATH"] = str(src)
assert (
    hashlib.sha256(src.read_bytes()).hexdigest() == "305d2b776c0c0d064406253e13c9f222b01b55abf68667bb93ceb588e4e4fd35"
)
subprocess.run(["python3", str(p / "wheel-island.py")], check=True)
subprocess.run(
    [
        "node",
        str(p / "bake.mjs"),
        json.dumps(
            [["overview", -0.15, 0.23, 0.13, 0.17, 1.047198], ["underside", -0.04, -0.3, 0.07, 0.17, 3.14159265]]
        ),
    ],
    check=True,
)
views = [
    [f"frame-{i:02d}", -0.15, 0.23, 0.13, 0.17, 1.047198, 1, math.radians(12) * math.sin(math.pi * i / 15) ** 2]
    for i in range(8)
]
subprocess.run(["node", str(p / "bake.mjs"), json.dumps(views)], check=True, env=os.environ | {"MX4_WHEEL": "1"})
print(
    "Rendered overview.png, underside.png and frame-00.png through frame-07.png. Final manifest records copy names/hashes. Outputs are written alongside this research script, not to application source or runtime assets."
)
