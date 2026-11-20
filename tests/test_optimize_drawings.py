"""price/mass-coupled `optimize` and shop-ready TechDraw hole/BOM callouts.

`fcad optimize` used to rank only by 1D cutlist length or board count, ignoring
stock prices, 2D sheets, COTS unit costs, and mass. drawings emitted bare
diameter numbers without reading `PartDesign::Hole` counterbore/depth/thread
intent, and assembly sheets carried no BOM table. this pins both end to end:

1. `fcad optimize --objective price` minimizes total stock (1D + 2D sheets) +
   COTS unit cost.
2. `fcad optimize --objective mass` minimizes total part mass (grams), reading
   explicit `mass_g` or computing `volume * density`.
3. TechDraw part drawings format `PartDesign::Hole` metadata (`THRU`, `CBORE`,
   blind depth) onto the dimension callout, and assembly drawings include a
   BOM annotation listing parts and quantities.

needs FreeCAD: run with `freecadcmd tests/test_optimize_drawings.py`.
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App

from fcad import testing

_PROJECT = """\
from FreeCAD import Placement, Rotation, Vector
import fcad
from fcad import types
from fcad.freecad import partdesign

PARAMS = {"bar_len": 160.0, "plate_w": 120.0}

# 300 mm bar is cheaper per mm than 200 mm, and 150 mm lets 2 bars fit one 300.
STOCK = {
    "al bar 20x10": [(200.0, 9.00), (300.0, 10.00)],
    "2mm sheet": [fcad.Sheet(250.0, 200.0, price=12.00),
                  fcad.Sheet(400.0, 300.0, price=22.00)],
}

MATERIALS = {"al": {"E": 69000.0, "nu": 0.33, "rho": 2700.0}}
MATERIAL = "al"

CONSTRAINTS = [
    lambda p, d: p["bar_len"] >= 140.0 or "bar_len under 140",
    lambda p, d: p["plate_w"] >= 100.0 or "plate_w under 100",
]

def _rect(w, h):
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

def _bar(doc, body, length):
    sk = partdesign.add_sketch(doc, body, "bar_sk",
                               points=_rect(length, 20.0), z=-5.0)
    pad = body.newObject(types.PartDesign.Pad, "bar_pad")
    pad.Profile = sk
    pad.Length = 10.0
    doc.recompute()
    bsk = partdesign.add_sketch(doc, body, "cbore_sk",
                                circles=[(0.0, 0.0, 6.0)], z=-5.0)
    hole = body.newObject(types.PartDesign.Hole, "cbore_hole")
    hole.Profile = bsk
    hole.Diameter = 6.0
    hole.DepthType = "ThroughAll"
    hole.HoleCutType = "Counterbore"
    hole.HoleCutDiameter = 11.0
    hole.HoleCutDepth = 4.0
    hole.Reversed = True
    doc.recompute()

def compute(p):
    l, pw = p["bar_len"], p["plate_w"]
    return [
        fcad.PartSpec("bar", profile="al bar 20x10", length=l,
                      placements=[Placement(),
                                  Placement(Vector(0, 30, 0), Rotation())],
                      grounded=True, dimension_sketches=["cbore_sk"],
                      openings=["cbore_sk"], material="al",
                      mass_g=lambda: l * 20.0 * 10.0 * 2.7e-3,
                      build=lambda d, b: _bar(d, b, l)),
        fcad.PartSpec("plate", profile="2mm sheet", length=pw,
                      sheet_size=(pw, 120.0),
                      placements=[Placement(Vector(0, 0, 10), Rotation())],
                      material="al",
                      mass_g=lambda: pw * 120.0 * 2.0 * 2.7e-3,
                      build=lambda d, b: partdesign.pad_and_bore(
                          d, b, _rect(pw, 120.0), 2.0, name="plate")),
        fcad.PartSpec("motor", profile="cots motor", length=20.0,
                      placements=[Placement(Vector(0, 0, 22), Rotation())],
                      unit_cost=15.0, mass_g=200.0,
                      build=lambda d, b: partdesign.pad_and_bore(
                          d, b, _rect(20.0, 20.0), 20.0, name="motor")),
    ]
"""


def main():
    c = testing.Checks()
    tmp = tempfile.mkdtemp(prefix="fcad_optdwg_")
    try:
        with open(os.path.join(tmp, "optdwg.fcad"), "w") as f:
            f.write(_PROJECT)

        # 1. optimize --objective price drops bar_len from 160 -> 140 (fits 2 bars
        #    on one 300 mm stock = $10 instead of 2 x 200 mm = $18).
        rp = subprocess.run(
            ["fcad", "-p", tmp, "optimize", "bar_len",
             "--span", "20", "--step", "20", "--objective", "price"],
            cwd=ROOT, capture_output=True, text=True)
        c("optimize --objective price succeeds (rc=%d)" % rp.returncode,
          rp.returncode == 0)
        c("price sweep ranks bar_len=140 cheapest with price column",
          "price" in rp.stdout and "bar_len=140" in rp.stdout)

        # 2. optimize --objective mass ranks lightest candidate first.
        rm = subprocess.run(
            ["fcad", "-p", tmp, "optimize", "bar_len", "plate_w",
             "--span", "20", "--step", "20", "--objective", "mass"],
            cwd=ROOT, capture_output=True, text=True)
        c("optimize --objective mass succeeds (rc=%d)" % rm.returncode,
          rm.returncode == 0)
        c("mass sweep ranks bar_len=140, plate_w=100 lightest",
          "mass_g" in rm.stdout and "bar_len=140, plate_w=100" in rm.stdout)

        # 3. drawings carry Hole counterbore callout and assembly BOM annotation.
        rb = subprocess.run(["fcad", "-p", tmp, "build", "drawings"],
                            cwd=ROOT, capture_output=True, text=True)
        c("build drawings succeeds (rc=%d)" % rb.returncode, rb.returncode == 0)
        bar_dxf = os.path.join(tmp, "dist", "drawings", "bar.dxf")
        asm_dxf = os.path.join(tmp, "dist", "drawings", "assembly.dxf")
        c("bar.dxf includes counterbore callout (CBORE / THRU)",
          os.path.exists(bar_dxf)
          and "CBORE" in open(bar_dxf).read()
          and "THRU" in open(bar_dxf).read())
        c("assembly.dxf includes BOM annotation listing parts",
          os.path.exists(asm_dxf)
          and "BOM" in open(asm_dxf).read()
          and "bar" in open(asm_dxf).read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
