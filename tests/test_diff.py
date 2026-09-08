"""regression test for the 3d diff (fcad.freecad.diff_doc).

the diff is per part: each part is diffed against its own previous version once,
in the part-local frame, and the green/red/grey results are fanned out to every
instance placement. three things must hold:

  correctness: the right added/removed material for every case an instance can
  fall into (unchanged, resized, moved, surplus, missing, whole part added or
  removed), for the assembly and for a single part target;

  no cross-talk: a part is only ever diffed against itself. cutting the whole
  new residual against the whole old one subtracts every part's old material
  from every other part's new material, so a screw that moved carves a spurious
  void out of a board's added slab;

  speed: the boolean work is per part *type*, not per instance. a design whose
  boards resized and whose screws moved used to melt down (on ../planter's real
  HEAD diff the first of three whole-assembly booleans did not finish in 25
  minutes); the fan-out pays one boolean per changed part plus a transform per
  instance.

needs FreeCAD: run with `freecadcmd tests/test_diff.py`. results go to $RESULT_FILE
(freecadcmd swallows script stdout and exits 0 on error, so a file is the reliable
channel).
"""

import os
import shutil
import sys
import tempfile
import time

# make the in-repo fcad package importable under FreeCAD's bundled python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App
import Part

from fcad.freecad import diff_doc
from fcad.freecad import util as fcutil

V = App.Vector
NAME = "widget"
BOARD_W, BOARD_T = 90.0, 20.0
BOARD_LEN, GROWN_LEN = 400.0, 410.0
HOLES, HOLE_R, HOLE_PITCH, HOLE_X0 = 6, 4.0, 45.0, 30.0
SCREW_R, SCREW_H = 3.0, 60.0
# off the drilled centerline, so the screws pierce solid wood rather than sliding
# down a hole: interpenetration is what made the whole-assembly boolean melt down.
SCREW_Y = 25.0
SCREWS_PER_BOARD = 4
N_STACK = 6
# far enough that a screw and its old self are disjoint, so the volumes are exact.
MOVE = 20.0
BUDGET = 4.0
TOL = 1.0


def _board(length):
    """a drilled board: complex enough that its boolean is worth paying once."""
    s = Part.makeBox(length, BOARD_W, BOARD_T)
    for k in range(HOLES):
        s = s.cut(Part.makeCylinder(HOLE_R, BOARD_T * 2,
                                    V(HOLE_X0 + k * HOLE_PITCH, BOARD_W / 2, -BOARD_T),
                                    V(0, 0, 1)))
    return s


def _screw():
    return Part.makeCylinder(SCREW_R, SCREW_H)


def _pl(x, y, z):
    return App.Placement(V(x, y, z), App.Rotation())


def _place(shape, pl):
    s = shape.copy()
    s.Placement = pl
    return s


def _stack(n, dx):
    """placements for n boards stacked face to face, plus the screws through them."""
    boards = [_pl(0, 0, i * BOARD_T) for i in range(n)]
    screws = [_pl(HOLE_X0 + k * HOLE_PITCH + dx, SCREW_Y, i * BOARD_T - SCREW_H / 2)
              for i in range(n) for k in range(SCREWS_PER_BOARD)]
    return boards, screws


BOARD_VOL = _board(BOARD_LEN).Volume
SLAB_VOL = _board(GROWN_LEN).Volume - BOARD_VOL
SCREW_VOL = _screw().Volume


def _dist(root, parts):
    """a built dist/ as `fcad build step` leaves it: one STEP per part type, the
    placed assembly, and the placement manifest. parts: {name: (shape, [pl, ...])}."""
    os.makedirs(os.path.join(root, "parts"))
    placed = []
    for name, (shape, pls) in parts.items():
        shape.exportStep(os.path.join(root, "parts", name + ".step"))
        placed += [_place(shape, p) for p in pls]
    Part.makeCompound(placed).exportStep(os.path.join(root, NAME + ".step"))
    fcutil.export_placements({n: pls for n, (_, pls) in parts.items()},
                             fcutil.placements_path(root, NAME))
    return root


def _vol(doc, prefix):
    return sum(o.Shape.Volume for o in doc.Objects
              if o.TypeId == "Part::Feature" and o.Name.startswith(prefix))


def _run(old, new, target="assembly"):
    """diff two fixture dists -> (green, red, grey) volumes and the elapsed time."""
    tmp = tempfile.mkdtemp()
    try:
        od = _dist(os.path.join(tmp, "old"), old)
        nd = _dist(os.path.join(tmp, "new"), new)
        diff_doc.NAME = NAME
        t0 = time.time()
        doc = diff_doc.diff(nd, od, target)
        elapsed = time.time() - t0
        out = (_vol(doc, target + "_added"), _vol(doc, target + "_removed"),
               _vol(doc, target + "_unchanged"), elapsed)
        App.closeDocument(doc.Name)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _identical(checks):
    boards, screws = _stack(N_STACK, 0.0)
    parts = {"board": (_board(BOARD_LEN), boards), "screw": (_screw(), screws)}
    a, r, u, dt = _run(parts, dict(parts))
    checks += [
        ("identical: nothing added", abs(a) < TOL),
        ("identical: nothing removed", abs(r) < TOL),
        ("identical: everything grey",
         abs(u - (N_STACK * BOARD_VOL + len(screws) * SCREW_VOL)) < TOL),
        ("identical: no booleans (%.1fs < %.1fs)" % (dt, BUDGET), dt < BUDGET),
    ]


def _fan_out(checks):
    """the melt-down case: every board resized and every screw moved."""
    ob, osc = _stack(N_STACK, 0.0)
    nb, nsc = _stack(N_STACK, MOVE)
    a, r, u, dt = _run({"board": (_board(BOARD_LEN), ob), "screw": (_screw(), osc)},
                       {"board": (_board(GROWN_LEN), nb), "screw": (_screw(), nsc)})
    checks += [
        ("fan-out: added = one slab per instance + the moved screws",
         abs(a - (N_STACK * SLAB_VOL + len(nsc) * SCREW_VOL)) < TOL),
        ("fan-out: removed = the screws that moved away",
         abs(r - len(osc) * SCREW_VOL) < TOL),
        ("fan-out: grey = the surviving board core",
         abs(u - N_STACK * BOARD_VOL) < TOL),
        ("fan-out: fast (%.1fs < %.1fs)" % (dt, BUDGET), dt < BUDGET),
    ]


def _cross_talk(checks):
    """an old screw standing in the space the board grows into must not eat the
    board's added slab: parts are diffed only against themselves."""
    at = _pl(GROWN_LEN - 5.0, SCREW_Y, -SCREW_H / 2)
    away = _pl(GROWN_LEN - 5.0, SCREW_Y + 300.0, -SCREW_H / 2)
    a, r, u, _ = _run({"board": (_board(BOARD_LEN), [_pl(0, 0, 0)]),
                       "screw": (_screw(), [at])},
                      {"board": (_board(GROWN_LEN), [_pl(0, 0, 0)]),
                       "screw": (_screw(), [away])})
    checks += [
        ("cross-talk: the moved screw does not eat the board's slab",
         abs(a - (SLAB_VOL + SCREW_VOL)) < TOL),
        ("cross-talk: the old screw is wholly removed", abs(r - SCREW_VOL) < TOL),
        ("cross-talk: the board core survives", abs(u - BOARD_VOL) < TOL),
    ]


def _instance_count(checks):
    """more instances of an unchanged part: whole green copies, and no booleans."""
    shape = _board(BOARD_LEN)
    a, r, u, dt = _run({"board": (shape, [_pl(0, 0, i * BOARD_T) for i in range(8)])},
                       {"board": (shape, [_pl(0, 0, i * BOARD_T) for i in range(10)])})
    checks += [
        ("surplus: added = two whole boards", abs(a - 2 * BOARD_VOL) < TOL),
        ("surplus: nothing removed", abs(r) < TOL),
        ("surplus: the other eight stay grey", abs(u - 8 * BOARD_VOL) < TOL),
        ("surplus: no booleans (%.1fs < %.1fs)" % (dt, BUDGET), dt < BUDGET),
    ]


def _whole_part(checks):
    """a part that only exists on one side is wholly green or wholly red."""
    board = _board(BOARD_LEN)
    a, r, u, _ = _run({"board": (board, [_pl(0, 0, 0)]),
                       "gone": (_screw(), [_pl(200.0, SCREW_Y, 0)])},
                      {"board": (board, [_pl(0, 0, 0)]),
                       "fresh": (_screw(), [_pl(300.0, SCREW_Y, 0)])})
    checks += [
        ("whole part: the new part is green", abs(a - SCREW_VOL) < TOL),
        ("whole part: the dropped part is red", abs(r - SCREW_VOL) < TOL),
        ("whole part: the board is untouched", abs(u - BOARD_VOL) < TOL),
    ]


def _part_target(checks):
    """`fcad diff PART` diffs one part in its own frame, with no placements."""
    a, r, _, _ = _run({"board": (_board(BOARD_LEN), [_pl(0, 0, 0)])},
                      {"board": (_board(GROWN_LEN), [_pl(0, 0, 0)])}, target="board")
    checks += [
        ("part target: added = the slab", abs(a - SLAB_VOL) < TOL),
        ("part target: nothing removed", abs(r) < TOL),
    ]


def main():
    checks = []
    for case in (_identical, _fan_out, _cross_talk, _instance_count,
                 _whole_part, _part_target):
        case(checks)

    failed = [name for name, ok in checks if not ok]
    lines = ["%s %s" % ("ok  " if ok else "FAIL", name) for name, ok in checks]
    lines.append("RESULT %s" % ("PASS" if not failed else "FAIL"))
    text = "\n".join(lines) + "\n"
    rf = os.environ.get("RESULT_FILE")
    if rf:
        with open(rf, "w") as f:
            f.write(text)
    print(text)
    return 0 if not failed else 1


if any(a.endswith("test_diff.py") for a in sys.argv):
    raise SystemExit(main())
