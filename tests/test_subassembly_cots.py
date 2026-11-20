"""COTS imported STEP parts, reusable sub-assemblies, and handed BOM variants.

real mechanisms reuse multi-part modules (motor + coupler + bracket placed at
several joints), incorporate vendor STEP parts (stepper motors, bearings) that
are not PartDesign bodies, and carry mirrored left/right plates drilled
asymmetrically. this pins all three end to end:

1. `PartSpec.from_step(...)` loads a vendor STEP file with `cots=True`, `sku`,
   `unit_cost`, and `mass_g`; participates in interference checks, rendering,
   and BOM metadata while skipping PartDesign sketch/void/disjoint checks.
2. `fcad.SubAssembly(name, specs).place([pl1, pl2])` transforms child specs and
   `fcad.compose(...)` merges shared part names across instances.
3. `PartSpec(..., bom_group=...)` keeps asymmetrically drilled handed parts as
   separate `.FCStd` files while grouping them in BOM/cutlist reporting.

needs FreeCAD: run with `freecadcmd tests/test_subassembly_cots.py`.
"""

import csv
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App
import Part

from fcad import testing

_PROJECT = """\
import os
from FreeCAD import Placement, Rotation, Vector
import fcad
from fcad.freecad import partdesign

HERE = os.path.dirname(os.path.abspath(__file__))
MOTOR_STEP = os.path.join(HERE, "nema17.step")

PARAMS = {"span": 100.0}
STOCK = {"al bar 20x6": [(300.0, 6.0)]}

def _rect(w, h):
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

def _plate(doc, body, hole_x):
    partdesign.pad_and_bore(doc, body, _rect(40.0, 20.0), 6.0,
                            [(hole_x, 0.0, 5.0)], name="plate")

def _bracket(doc, body):
    partdesign.pad_and_bore(doc, body, _rect(30.0, 30.0), 4.0, name="brk")

def _actuator_module():
    return fcad.SubAssembly("actuator", [
        fcad.PartSpec("bracket", placements=[Placement(Vector(0, 0, 2.0), Rotation())],
                      build=_bracket, length=30.0, color=(0.3, 0.3, 0.4)),
        fcad.PartSpec.from_step(
            "nema17", MOTOR_STEP,
            placements=[Placement(Vector(0, 0, 4.0), Rotation())],
            profile="NEMA-17 stepper", sku="17HS4401",
            unit_cost=14.50, mass_g=280.0, color=(0.15, 0.15, 0.18)),
    ])

def compute(p):
    base = fcad.PartSpec(
        "base", placements=[Placement(Vector(0, 0, -3.0), Rotation())],
        grounded=True, length=160.0,
        build=lambda d, b: partdesign.pad_and_bore(
            d, b, _rect(160.0, 50.0), 6.0, name="base"))
    # two handed cheek plates drilled asymmetrically, grouped under bom_group.
    left = fcad.PartSpec(
        "cheek_left", profile="al bar 20x6", bom_group="cheek", length=40.0,
        placements=[Placement(Vector(-56.0, 0.0, 3.0), Rotation())],
        openings=["plate_bore1"],
        build=lambda d, b: _plate(d, b, -8.0))
    right = fcad.PartSpec(
        "cheek_right", profile="al bar 20x6", bom_group="cheek", length=40.0,
        placements=[Placement(Vector(56.0, 0.0, 3.0), Rotation())],
        openings=["plate_bore1"],
        build=lambda d, b: _plate(d, b, 8.0))
    act = _actuator_module().place([
        Placement(Vector(-18.0, 0.0, 0.0), Rotation()),
        Placement(Vector(18.0, 0.0, 0.0), Rotation()),
    ])
    return fcad.compose([base, left, right], act)
"""


def _write_vendor_step(path):
    """generate a 2-solid vendor motor STEP (body + shaft) to test COTS import."""
    body = Part.makeBox(26.0, 26.0, 20.0, App.Vector(-13.0, -13.0, 0.0))
    shaft = Part.makeCylinder(2.5, 10.0, App.Vector(0.0, 0.0, 20.0))
    # intentionally a multi-solid compound: vendor STEP files often carry several.
    comp = Part.makeCompound([body, shaft])
    doc = App.newDocument("vendor_step")
    try:
        o = doc.addObject("Part::Feature", "Motor")
        o.Shape = comp
        doc.recompute()
        Part.export([o], path)
    finally:
        App.closeDocument(doc.Name)


def main():
    c = testing.Checks()
    tmp = tempfile.mkdtemp(prefix="fcad_cots_")
    try:
        _write_vendor_step(os.path.join(tmp, "nema17.step"))
        with open(os.path.join(tmp, "cots.fcad"), "w") as f:
            f.write(_PROJECT)
        r = subprocess.run(["fcad", "-p", tmp, "precommit"],
                           cwd=ROOT, capture_output=True, text=True)
        c("COTS STEP + SubAssembly + handed variants pass precommit (rc=%d)"
          % r.returncode, r.returncode == 0)

        bom_path = os.path.join(tmp, "dist", "cots-bom.csv")
        c("bom csv written", os.path.exists(bom_path))
        if os.path.exists(bom_path):
            with open(bom_path) as f:
                rows = {row["part"]: row for row in csv.DictReader(f)}
            c("sub-assembly merged 2 placed actuators into qty=2 bracket & nema17",
              rows.get("bracket", {}).get("qty") == "2"
              and rows.get("nema17", {}).get("qty") == "2")
            c("COTS metadata (sku, unit_cost, mass_g) recorded in BOM",
              rows.get("nema17", {}).get("sku") == "17HS4401"
              and rows.get("nema17", {}).get("unit_cost") == "14.5"
              and rows.get("nema17", {}).get("mass_g") == "280.0")
            c("handed variants record shared bom_group",
              rows.get("cheek_left", {}).get("bom_group") == "cheek"
              and rows.get("cheek_right", {}).get("bom_group") == "cheek")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
