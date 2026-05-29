"""regression test for the assembly 3d diff (fcad.freecad.diff_doc).

two things must hold for the whole-assembly diff:

  correctness: it reports the right added/removed material across the cases an
  instance can fall into (unchanged, removed, added, resized);

  speed: it must not boolean the unchanged bulk. the monolithic path ran three
  boolean ops over every solid at once, which melts down on a real assembly's
  interpenetrating screws (minutes for `fcad diff`). the per-solid path skips
  signature-matched instances, so an unchanged assembly costs ~no booleans.

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

V = App.Vector
BOX = (5.0, 5.0, 5.0)
VOL = BOX[0] * BOX[1] * BOX[2]
N = 40
# a real assembly's slowness comes from screws sunk into the boards (overlapping
# solids); this many interpenetrating instances takes the monolithic path well
# past the budget while the per-solid path skips them all.
SCREW_BOARDS = 16
BUDGET = 4.0


def _row(n):
    return [Part.makeBox(*BOX, V(i * BOX[0] * 2, 0, 0)) for i in range(n)]


def _screw_assembly(n):
    """boards with screws driven through them: the interpenetrating case."""
    out = []
    for i in range(n):
        x = i * 42
        out.append(Part.makeBox(40, 8, 8, V(x, 0, 0)))
        for k in range(4):
            out.append(Part.makeCylinder(1.6, 14, V(x + 6 + k * 9, 4, -3), V(0, 0, 1)))
    return out


def _vol(doc, prefix):
    return sum(o.Shape.Volume for o in doc.Objects
              if o.TypeId == "Part::Feature" and o.Name.startswith(prefix))


def _run(old, new, target="assembly"):
    tmp = tempfile.mkdtemp()
    try:
        od, nd = os.path.join(tmp, "old"), os.path.join(tmp, "new")
        os.makedirs(od); os.makedirs(nd)
        Part.makeCompound(old).exportStep(os.path.join(od, "planter.step"))
        Part.makeCompound(new).exportStep(os.path.join(nd, "planter.step"))
        diff_doc.NAME = "planter"
        t0 = time.time()
        doc = diff_doc.diff(nd, od, target)
        elapsed = time.time() - t0
        out = (_vol(doc, target + "_added"), _vol(doc, target + "_removed"),
               _vol(doc, target + "_unchanged"), elapsed)
        App.closeDocument(doc.Name)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    checks = []

    # identical assemblies: nothing added or removed, everything unchanged.
    a, r, u, _ = _run(_row(N), _row(N))
    checks.append(("identical: no added", abs(a) < 1.0))
    checks.append(("identical: no removed", abs(r) < 1.0))
    checks.append(("identical: all unchanged", abs(u - N * VOL) < 1.0))

    # remove one board, add one past the end, grow one along x.
    old, new = _row(N), _row(N)
    del new[10]
    new.append(Part.makeBox(*BOX, V(N * BOX[0] * 2, 0, 0)))
    new[19] = Part.makeBox(BOX[0] + 1.0, BOX[1], BOX[2], V(20 * BOX[0] * 2, 0, 0))
    a, r, u, _ = _run(old, new)
    checks.append(("mixed: added", abs(a - (VOL + 1.0 * BOX[1] * BOX[2])) < 1.0))
    checks.append(("mixed: removed", abs(r - VOL) < 1.0))

    # an unchanged assembly full of interpenetrating screws must skip the
    # booleans entirely; this is the case that made `fcad diff` take minutes.
    s = _screw_assembly(SCREW_BOARDS)
    a, r, _, elapsed = _run(list(s), list(s))
    checks.append(("screws: no added", abs(a) < 1.0))
    checks.append(("screws: no removed", abs(r) < 1.0))
    checks.append(("screws: fast (%.1fs < %.1fs budget)" % (elapsed, BUDGET),
                   elapsed < BUDGET))

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
