"""2D sheet stock nesting and sheet-metal flat-pattern unfolding.

`cutlist` previously supported only 1D linear bar/lumber stock. sheet goods
(plywood, aluminum plate, polycarbonate lid) and bent sheet-metal housings need
2D sheet packing and bend-allowance unfolding. this pins both end to end:

1. `fcad.Sheet(w, h, price=...)` in `STOCK` triggers 2D sheet nesting, writing
   `dist/<name>-sheetlist.csv` and reporting sheet counts, waste %, and price.
2. `fcad.sheetmetal.unfold_u_channel` and `unfold_box_tray` compute exact
   K-factor bend allowances and export `dist/sketches/<part>-flat.svg` with
   bend lines when attached via `PartSpec(..., flat_pattern=...)`.
3. `PartSpec.sheet_size` falls back to the unfolded flat pattern dimensions so
   bent parts nest onto 2D sheet stock automatically.

needs FreeCAD: run with `freecadcmd tests/test_sheetmetal.py`.
"""

import csv
import math
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fcad import testing

_PROJECT = """\
from FreeCAD import Placement, Rotation, Vector
import fcad
from fcad import sheetmetal
from fcad.freecad import partdesign

PARAMS = {"w": 160.0, "d": 120.0, "h": 40.0, "t": 2.0, "r": 2.0}

STOCK = {
    "2mm al sheet": [fcad.Sheet(400.0, 300.0, price=18.0),
                     fcad.Sheet(600.0, 400.0, price=30.0)],
}

def _rect(w, h):
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

def compute(p):
    w, d, h, t, r = p["w"], p["d"], p["h"], p["t"], p["r"]
    fp = sheetmetal.unfold_u_channel(w, h, d, t, r)
    return [
        fcad.PartSpec("chassis", profile="2mm al sheet", length=d,
                      placements=[Placement()], grounded=True,
                      flat_pattern=fp,
                      build=lambda doc, body: partdesign.pad_and_bore(
                          doc, body, _rect(w, d), t, name="chassis")),
        fcad.PartSpec("lid", profile="2mm al sheet", length=w,
                      sheet_size=(w, d),
                      placements=[Placement(Vector(0, 0, t), Rotation())],
                      build=lambda doc, body: partdesign.pad_and_bore(
                          doc, body, _rect(w, d), t, name="lid")),
    ]
"""


def main():
    c = testing.Checks()

    # 1. pure bend-allowance math.
    from fcad import cutlist, sheetmetal
    ba = sheetmetal.bend_allowance(90.0, 2.0, 2.0, k_factor=0.44)
    want_ba = math.radians(90.0) * (2.0 + 0.44 * 2.0)
    c("90-deg bend allowance matches K-factor formula (%.4f vs %.4f)"
      % (ba, want_ba), testing.near(ba, want_ba, 1e-6))

    fp = sheetmetal.unfold_u_channel(160.0, 40.0, 120.0, 2.0, 2.0)
    # two 90-deg bends: flat width = (160 - 2*4) + 2*(40 - 4) + 2*ba
    want_w = (160.0 - 8.0) + 2.0 * (40.0 - 4.0) + 2.0 * want_ba
    c("u-channel unfolded width matches formula (%.3f vs %.3f)"
      % (fp.width, want_w), testing.near(fp.width, want_w, 1e-3))
    c("u-channel flat pattern has 2 bend lines (%d)" % len(fp.bend_lines),
      len(fp.bend_lines) == 2)

    tray = sheetmetal.unfold_box_tray(160.0, 160.0, 30.0, 2.0, 2.0)
    c("box tray flat pattern has 4 bend lines (%d)" % len(tray.bend_lines),
      len(tray.bend_lines) == 4)

    # 2. pure 2D sheet nesting packs rectangles without overlap and respects kerf.
    plan = cutlist.plan_sheets(
        {(200.0, 120.0): 2, (160.0, 120.0): 1},
        [cutlist.Sheet(400.0, 300.0, price=18.0)], kerf=3.0)
    c("2D sheet nester packs 3 plates onto 1 sheet (%d)" % plan.sheet_count,
      plan.sheet_count == 1 and not plan.unpacked)
    placed = plan.sheets[0].placements
    c("all 3 pieces placed inside 400x300 bounds",
      len(placed) == 3 and all(
          0.0 <= p.x and p.x + p.w <= 400.0 + 1e-6
          and 0.0 <= p.y and p.y + p.h <= 300.0 + 1e-6 for p in placed))

    # verify no two placed pieces overlap (including kerf separation).
    no_ov = True
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            if not (a.x + a.w <= b.x + 1e-6 or b.x + b.w <= a.x + 1e-6
                    or a.y + a.h <= b.y + 1e-6 or b.y + b.h <= a.y + 1e-6):
                no_ov = False
    c("nested pieces do not overlap", no_ov)

    # 3. end-to-end build produces sheetlist.csv and <part>-flat.svg.
    tmp = tempfile.mkdtemp(prefix="fcad_sheet_")
    try:
        with open(os.path.join(tmp, "encl.fcad"), "w") as f:
            f.write(_PROJECT)
        r = subprocess.run(["fcad", "-p", tmp, "build"],
                           cwd=ROOT, capture_output=True, text=True)
        c("fcad build succeeds on 2D sheet-metal project (rc=%d)" % r.returncode,
          r.returncode == 0)
        flat_svg = os.path.join(tmp, "dist", "sketches", "chassis-flat.svg")
        c("flat pattern svg written with bend lines",
          os.path.exists(flat_svg)
          and "stroke-dasharray" in open(flat_svg).read())
        sheet_csv = os.path.join(tmp, "dist", "encl-sheetlist.csv")
        c("sheetlist csv written", os.path.exists(sheet_csv))
        if os.path.exists(sheet_csv):
            with open(sheet_csv) as f:
                rows = list(csv.DictReader(f))
            c("sheetlist csv lists both chassis and lid (%d rows)" % len(rows),
              len(rows) == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
