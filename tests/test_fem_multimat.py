"""multi-material assembly FEM, multi-pose FEM cases, and FEM mesh decimation.

an assembly of steel and aluminum parts used to fuse into one monolithic
single-material solid, giving the wrong stiffness whenever parts differed in
modulus. this pins three extensions end to end:

1. per-part `material` on `PartSpec` builds a conformal CompSolid with separate
   FEM material regions, verified against the closed-form Euler-Bernoulli
   deflection of a two-material cantilever (`(F*L^3)/(3*I) * (7/(8*E1) + 1/(8*E2))`).
2. a `FEM` case may declare `"target"` + `"params"` overrides so `fcad fem`
   solves multiple kinematic poses of the same assembly.
3. `fem_render.decimate` reduces a dense boundary mesh while carrying per-node
   scalar and vector fields through clustering.

needs FreeCAD + gmsh + CalculiX: run with `freecadcmd tests/test_fem_multimat.py`.
"""

import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fcad import testing

# 200 mm cantilever: root half [0, 100] in soft material (E1=70 GPa),
# tip half [100, 200] in stiff material (E2=210 GPa), 20x20 cross-section.
L = 200.0
W = 20.0
H = 20.0
F = 500.0
E1 = 70000.0
E2 = 210000.0

_PROJECT = """\
from FreeCAD import Placement, Vector
import fcad
from fcad import fem_select as fs
from fcad.freecad import partdesign

PARAMS = {"half_l": 100.0, "w": 20.0, "h": 20.0, "load": 500.0}

MATERIALS = {
    "soft": {"E": 70000.0, "nu": 0.30, "rho": 2700.0},
    "stiff": {"E": 210000.0, "nu": 0.30, "rho": 7850.0},
}

FEM = {
    "assembly": {
        "fixed": [fs.min_along("x")],
        "loads": [{"faces": fs.max_along("x"), "magnitude": 500.0,
                   "direction": "-z"}],
        "mesh_size": 7.0,
    },
    "long_pose": {
        "target": "assembly",
        "params": {"half_l": 120.0},
        "fixed": [fs.min_along("x")],
        "loads": [{"faces": fs.max_along("x"), "magnitude": 500.0,
                   "direction": "-z"}],
        "mesh_size": 8.0,
    },
}

def _rect(l, w):
    return [(-l / 2.0, -w / 2.0), (l / 2.0, -w / 2.0),
            (l / 2.0, w / 2.0), (-l / 2.0, w / 2.0)]

def compute(p):
    hl, w, h = p["half_l"], p["w"], p["h"]
    return [
        fcad.PartSpec("root",
                      placements=[Placement(Vector(hl / 2.0, 0.0, 0.0),
                                            Placement().Rotation)],
                      grounded=True, material="soft",
                      build=lambda d, b: partdesign.pad_and_bore(
                          d, b, _rect(hl, w), h, name="root")),
        fcad.PartSpec("tip",
                      placements=[Placement(Vector(1.5 * hl, 0.0, 0.0),
                                            Placement().Rotation)],
                      material="stiff",
                      build=lambda d, b: partdesign.pad_and_bore(
                          d, b, _rect(hl, w), h, name="tip")),
    ]
"""


def _closed_form(length, e_root, e_tip):
    inertia = W * (H ** 3) / 12.0
    return (F * (length ** 3) / (3.0 * inertia)) * (
        7.0 / (8.0 * e_root) + 1.0 / (8.0 * e_tip))


def main():
    c = testing.Checks()
    tmp = tempfile.mkdtemp(prefix="fcad_mmat_")
    try:
        with open(os.path.join(tmp, "bimat.fcad"), "w") as f:
            f.write(_PROJECT)
        r = subprocess.run(["fcad", "-p", tmp, "fem", "all"],
                           cwd=ROOT, capture_output=True, text=True)
        c("fcad fem all succeeds on multi-material + multi-pose project (rc=%d)"
          % r.returncode, r.returncode == 0)

        npz = os.path.join(tmp, "dist", "bimat.fem.npz")
        c("assembly fem bundle written", os.path.exists(npz))
        if os.path.exists(npz):
            data = np.load(npz)
            got = float(data["disp_mag"].max())
            want = _closed_form(L, E1, E2)
            # single-material steel would give 0.36x want; single aluminum 1.09x want.
            # closed form is 1.488 mm vs all-stiff 0.541 mm and all-soft 1.624 mm.
            err = abs(got - want) / want
            c("multi-material tip deflection matches closed form "
              "(%.4f vs %.4f mm, err=%.2f%%)" % (got, want, 100.0 * err),
              err < 0.04)

        pose_npz = os.path.join(tmp, "dist", "parts", "long_pose.fem.npz")
        c("multi-pose case bundle written", os.path.exists(pose_npz))
        if os.path.exists(pose_npz):
            pdata = np.load(pose_npz)
            got_p = float(pdata["disp_mag"].max())
            want_p = _closed_form(240.0, E1, E2)
            err_p = abs(got_p - want_p) / want_p
            c("multi-pose deflection matches longer beam closed form "
              "(%.4f vs %.4f mm, err=%.2f%%)" % (got_p, want_p, 100.0 * err_p),
              err_p < 0.04)

        # 3. fem_render.decimate reduces a dense grid mesh while preserving fields.
        from fcad.render import fem_render
        nx, ny = 30, 30
        xs, ys = np.meshgrid(np.linspace(0, 100, nx), np.linspace(0, 100, ny))
        nodes = np.column_stack([xs.ravel(), ys.ravel(),
                                 np.zeros(nx * ny)]).astype(np.float32)
        tris = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                a = j * nx + i
                tris.append([a, a + 1, a + nx])
                tris.append([a + 1, a + nx + 1, a + nx])
        tris = np.array(tris, np.int32)
        vm = nodes[:, 0].copy()
        disp = np.column_stack([np.zeros_like(vm), np.zeros_like(vm), vm * 0.01])
        dn, dt, dvm, ddisp = fem_render.decimate(nodes, tris, vm, disp,
                                                 max_tris=400)
        c("decimate reduces triangle count (%d -> %d <= 400)"
          % (len(tris), len(dt)), 0 < len(dt) <= 400 and len(dt) < len(tris))
        c("decimate preserves field range (%.1f..%.1f)"
          % (float(dvm.min()), float(dvm.max())),
          dvm.min() < 15.0 and dvm.max() > 85.0 and ddisp.shape == (len(dn), 3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
