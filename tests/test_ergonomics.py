"""contract ergonomics and predictability improvements.

pins five contract refinements end to end while keeping part geometry in plain,
transferable FreeCAD Python (no wrapper layer over PartDesign features):

1. `PartSpec` validation (rejects both `build` and `solid`, bad transparency or
   color) and single-placement / omitted-placement defaults.
2. reactive `STOCK` callable `(values) -> dict` and callable FEM load `magnitude`
   `(values) -> float` so changing parameters updates stock matching and loads.
3. `pad_and_bore` defaulting `name` to `body.Name` and glob matching (`*_bore*`)
   in `openings` and `dimension_sketches` (still failing on unmatched globs).
4. `build_assembly.joint` and `fix_remaining` eliminating `/usr/share/freecad`
   `sys.path` and `JointObject` lookup boilerplate in `ASSEMBLE`, while returning
   the real FreeCAD joint object.
5. `optimize` warning when a swept stock part omits explicit `length=`.

needs FreeCAD + gmsh + CalculiX: run with `freecadcmd tests/test_ergonomics.py`.
"""

import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App
import Part

import fcad
from fcad import optimize, testing


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    return False


_PROJECT = """\
from FreeCAD import Placement, Rotation, Vector
import fcad
from fcad import fem_select as fs, geom_select as gs, types
from fcad.freecad import build_assembly as asmlib, partdesign

PARAMS = {"length": 120.0, "width": 30.0, "thk": 10.0, "tip_load": 400.0}

STOCK = lambda p: {
    "bar %gx%g" % (p["width"], p["thk"]): [(200.0, 5.0), (300.0, 7.0)],
}

CONSTRAINTS = [
    lambda p, d: p["width"] >= 20.0 or "too narrow",
]

FEM = {
    "__default__": {
        "material": "steel",
        "fixed": [fs.min_along("x")],
        "loads": [{"faces": fs.max_along("x"),
                   "magnitude": lambda p: p["tip_load"],
                   "direction": "-z"}],
        "mesh_size": 7.0,
    },
    "heavy": {
        "target": "bar",
        "params": {"tip_load": 800.0},
        "material": "steel",
        "fixed": [fs.min_along("x")],
        "loads": [{"faces": fs.max_along("x"),
                   "magnitude": lambda p: p["tip_load"],
                   "direction": "-z"}],
        "mesh_size": 7.0,
    },
}

def _bar(doc, body, l, w, t):
    pts = [(-l / 2, -w / 2, 4.0), (l / 2, -w / 2, 4.0),
           (l / 2, w / 2, 4.0), (-l / 2, w / 2, 4.0)]
    partdesign.pad_and_bore(doc, body, pts, t,
                            [(-l / 4.0, 0.0, 6.0), (l / 4.0, 0.0, 6.0)])
    base = body.Tip
    ch = body.newObject(types.PartDesign.Chamfer, "top_bevel")
    ch.Base = (base, gs.edges(base.Shape, gs.at_z(t / 2.0)))
    ch.Size = 0.6
    doc.recompute()

def _pin(doc, body):
    sk = partdesign.add_sketch(
        doc, body, "pin_sk",
        points=[(0, 5), (3, 5), (3, -5), (0, -5)],
        placement=Placement(Vector(0, 0, 0), Rotation(Vector(1, 0, 0), 90)))
    rev = body.newObject(types.PartDesign.Revolution, "pin_rev")
    rev.Profile = sk
    rev.ReferenceAxis = (sk, ["V_Axis"])
    rev.Angle = 360.0
    doc.recompute()

def compute(p):
    l, w, t = p["length"], p["width"], p["thk"]
    return [
        fcad.PartSpec("bar", profile="bar %gx%g" % (w, t), length=l,
                      grounded=True,
                      dimension_sketches=["*_bore*"],
                      openings=["*_bore*"],
                      build=lambda d, b: _bar(d, b, l, w, t)),
        fcad.PartSpec("pin", placements=Placement(Vector(l / 4.0, 0, 0),
                                                  Rotation()),
                      length=10.0, embeds=True, build=_pin),
    ]

def ASSEMBLE(doc, asm, links):
    asmlib.joint(doc, asm, "Rev_pin", "Revolute",
                 links["bar"][0], links["pin"][0])
    asmlib.fix_remaining(doc, asm, links, links["bar"][0])
"""


def main():
    c = testing.Checks()

    # 1. PartSpec validation + placements ergonomics.
    c("PartSpec rejects passing both build and solid",
      _raises(lambda: fcad.PartSpec(
          "bad", build=lambda d, b: None, solid=lambda: Part.makeBox(1, 1, 1))))
    c("PartSpec rejects out-of-range transparency",
      _raises(lambda: fcad.PartSpec("bad", transparency=150)))
    c("PartSpec rejects invalid color values",
      _raises(lambda: fcad.PartSpec("bad", color=(255, 0, 0))))
    s_default = fcad.PartSpec("one")
    c("PartSpec defaults placements to [Placement()]",
      len(s_default.placements) == 1)
    s_single = fcad.PartSpec("two", placements=App.Placement(
        App.Vector(10, 0, 0), App.Rotation()))
    c("PartSpec coerces a single Placement into a 1-element list",
      len(s_single.placements) == 1
      and testing.near(s_single.placements[0].Base.x, 10.0))

    # 2. optimize warns when a stock part omits explicit `length=`.
    nol = fcad.Project(
        "nol", params={"l": 100.0},
        stock={"bar": [200.0]},
        constraints=[lambda p, d: p["l"] >= 80.0],
        compute=lambda p: [fcad.PartSpec(
            "b", profile="bar",
            solid=lambda: Part.makeBox(p["l"], 10, 10))])
    _, _, warn = optimize.sweep(nol, ["l"], span=10.0, step=10.0)
    c("optimize warns when a stock part omits explicit `length=` (%r)" % warn,
      warn is not None and "length" in warn.lower())

    # 3. end-to-end project using callable STOCK, callable FEM magnitude,
    #    glob openings/dimension_sketches, and asmlib.joint + fix_remaining.
    tmp = tempfile.mkdtemp(prefix="fcad_erg_")
    try:
        with open(os.path.join(tmp, "erg.fcad"), "w") as f:
            f.write(_PROJECT)
        r = subprocess.run(["fcad", "-p", tmp, "precommit"],
                           cwd=ROOT, capture_output=True, text=True)
        c("ergonomic project passes precommit (rc=%d)" % r.returncode,
          r.returncode == 0)

        # an unmatched glob opening must still fail check loudly.
        bad_dir = os.path.join(tmp, "bad")
        os.makedirs(bad_dir)
        bad = os.path.join(bad_dir, "bad.fcad")
        with open(bad, "w") as f:
            f.write(_PROJECT.replace('openings=["*_bore*"]',
                                     'openings=["*_nonexistent*"]'))
        rb = subprocess.run(["fcad", "-p", bad, "check"],
                            cwd=ROOT, capture_output=True, text=True)
        c("unmatched glob opening still fails check (rc=%d)" % rb.returncode,
          rb.returncode != 0 and "*_nonexistent*" in rb.stdout)

        # sweeping `width` still matches reactive callable STOCK.
        ro = subprocess.run(
            ["fcad", "-p", tmp, "optimize", "width",
             "--span", "10", "--step", "10"],
            cwd=ROOT, capture_output=True, text=True)
        c("reactive STOCK matches across width sweep (rc=%d)" % ro.returncode,
          ro.returncode == 0 and "200" in ro.stdout)

        # callable FEM magnitude doubles deflection when tip_load doubles (400 -> 800 N).
        rf1 = subprocess.run(["fcad", "-p", tmp, "fem", "bar"],
                             cwd=ROOT, capture_output=True, text=True)
        rf2 = subprocess.run(["fcad", "-p", tmp, "fem", "heavy"],
                             cwd=ROOT, capture_output=True, text=True)
        p1 = os.path.join(tmp, "dist", "parts", "bar.fem.npz")
        p2 = os.path.join(tmp, "dist", "parts", "heavy.fem.npz")
        c("callable FEM magnitude solves both loads (rc=%d, %d)"
          % (rf1.returncode, rf2.returncode),
          rf1.returncode == 0 and rf2.returncode == 0
          and os.path.exists(p1) and os.path.exists(p2))
        if os.path.exists(p1) and os.path.exists(p2):
            d1 = float(np.load(p1)["disp_mag"].max())
            d2 = float(np.load(p2)["disp_mag"].max())
            ratio = d2 / d1 if d1 else 0.0
            c("doubling tip_load via callable magnitude doubles deflection (%.3f)"
              % ratio, testing.near(ratio, 2.0, 0.02))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return c.report()


testing.main(main, __file__)
