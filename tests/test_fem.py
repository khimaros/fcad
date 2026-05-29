"""end-to-end test for the headless FEM solve + the npz it hands the renderer.

drives the real stack (gmsh mesher + CalculiX solver) on a tiny in-memory box
project, then asserts the numpy bundle fem.solve returns: a watertight boundary
surface, per-node von Mises + displacement that actually responded to the load,
and the requested eigenmodes. also unit-tests the pure-python face selectors that
decide which faces get fixed/loaded (no FreeCAD needed for those).

needs FreeCAD + gmsh + ccx: run with `freecadcmd tests/test_fem.py`. results go to
$RESULT_FILE (freecadcmd swallows stdout and exits 0 on error, so a file is the
reliable channel), matching the other tests.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App
import Part
from types import SimpleNamespace

from fcad import fem_select as fs
from fcad.freecad import fem


class _Spec:
    name = "box"
    embeds = False
    placements = [App.Placement()]


class _Project:
    name = "tfem"

    def __init__(self):
        case = SimpleNamespace(
            material={"E": 210000.0, "nu": 0.30, "rho": 7900.0},
            fixed=[fs.min_along("z")],
            loads=[SimpleNamespace(kind="force", faces=[fs.max_along("z")],
                                   magnitude=500.0, direction="-z")],
            self_weight=False, mesh_size=6.0, modes=2)
        self.fem = {"box": case}

    def compute(self, values):
        return {"specs": [_Spec()]}

    def from_spec(self, spec):
        return Part.makeBox(100, 20, 10)

    def defaults(self):
        return {}


def _selector_checks():
    """the pure-python selectors over a synthetic unit box (no FreeCAD)."""
    # six faces of a 10mm cube (bigger than the 1mm selector tolerance): the
    # -x,+x,-y,+y,-z,+z faces by center + outward normal.
    faces = [fs.FaceInfo(i, c, n, 1.0, (0, 0, 0, 10, 10, 10)) for i, (c, n) in enumerate([
        ((0, 5, 5), (-1, 0, 0)), ((10, 5, 5), (1, 0, 0)),
        ((5, 0, 5), (0, -1, 0)), ((5, 10, 5), (0, 1, 0)),
        ((5, 5, 0), (0, 0, -1)), ((5, 5, 10), (0, 0, 1))])]
    return [
        ("min_along z -> bottom face", fs.min_along("z")(faces) == [4]),
        ("max_along z -> top face", fs.max_along("z")(faces) == [5]),
        ("min_along x -> -x face", fs.min_along("x")(faces) == [0]),
        ("normal_dir +z -> top face", fs.normal_dir("z")(faces) == [5]),
        ("normal_dir -z (signed) -> bottom", fs.normal_dir("-z")(faces) == [4]),
        ("largest(2) returns two faces", len(fs.largest(2)(faces)) == 2),
        ("resolve ('z','min') == min_along z",
         fs.resolve(("z", "min"))(faces) == [4]),
        ("resolve 'Face6' -> index 5", fs.resolve("Face6")(faces) == [5]),
    ]


def _watertight(tris):
    """every undirected edge of a closed surface is shared by an even count."""
    from collections import Counter
    ec = Counter()
    for a, b, c in tris:
        for e in ((a, b), (b, c), (a, c)):
            ec[tuple(sorted((int(e[0]), int(e[1]))))] += 1
    return all(n % 2 == 0 for n in ec.values())


def _solve_checks():
    tmp = tempfile.mkdtemp()
    try:
        out = fem.solve(_Project(), {}, "box", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = len(out["nodes"])
    tris = out["tris"]
    return [
        ("boundary has triangles", len(tris) > 0),
        ("triangle indices in range", n > 0 and int(tris.max()) < n),
        ("boundary surface is watertight", _watertight(tris)),
        ("von Mises is per-node", out["von_mises"].shape == (n,)),
        ("displacement is per-node vectors", out["disp"].shape == (n, 3)),
        ("the load actually deflected it", float(out["disp_mag"].max()) > 0.0),
        ("von Mises is positive + finite",
         0.0 < float(out["von_mises"].max()) < 1e9),
        ("two eigenmodes returned", int(out["n_modes"]) == 2),
        ("mode frequencies positive", out["mode_freqs"].shape == (2,)
         and float(out["mode_freqs"].min()) > 0.0),
        ("mode shapes are (K, N, 3)", out["mode_disp"].shape == (2, n, 3)),
    ]


def main():
    checks = _selector_checks() + _solve_checks()
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


if any(a.endswith("test_fem.py") for a in sys.argv):
    raise SystemExit(main())
