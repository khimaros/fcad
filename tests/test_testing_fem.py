"""end-to-end tests for `fcad.testing` FEM result helpers (`fem_result`/`fem_solve`).

projects need to assert physical properties of solved FEM results inside
`fcad test`: deflection bounds, percentile von Mises safety factors (never the
singular nodal max), eigenfrequencies, and sensitivity to parameter overrides,
while catching stale `.npz` bundles rather than reading outdated numbers.

needs FreeCAD + gmsh + CalculiX: run with `freecadcmd tests/test_testing_fem.py`.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fcad import testing

_BEAM = """\
import fcad
from fcad import fem_select as fs
from fcad.freecad import partdesign

PARAMS = {"length": 200.0, "width": 20.0, "height": 15.0, "load": 300.0}
MATERIAL = "steel"
FEM = {"__default__": {
    "fixed": [fs.min_along("x")],
    "loads": [{"faces": fs.max_along("x"),
               "magnitude": lambda p: p["load"],
               "direction": "-z"}],
    "mesh_size": 8.0,
    "modes": 2,
}}

def compute(p):
    l, w, h = p["length"], p["width"], p["height"]
    pts = [(-l / 2, -w / 2), (l / 2, -w / 2), (l / 2, w / 2), (-l / 2, w / 2)]
    return [fcad.PartSpec("beam", length=l,
                          build=lambda d, b: partdesign.pad_and_bore(d, b, pts, h))]
"""


def _raises(fn, exc=RuntimeError):
    try:
        fn()
    except exc:
        return True
    return False


def main():
    c = testing.Checks()
    tmp = tempfile.mkdtemp(prefix="fcad_tfem_")
    try:
        fcad_path = os.path.join(tmp, "beam.fcad")
        with open(fcad_path, "w") as f:
            f.write(_BEAM)

        # 1. solve=False refuses missing bundle loudly.
        c("fem_result(solve=False) raises when no .npz exists",
          _raises(lambda: testing.fem_result("assembly", path=fcad_path,
                                             solve=False)))

        # 2. fem_solve solves, caches stamped .npz, and exposes FemResult fields.
        res = testing.fem_solve("assembly", path=fcad_path)
        e_steel = 210000.0
        inertia = 20.0 * (15.0 ** 3) / 12.0
        want_disp = 300.0 * (200.0 ** 3) / (3.0 * e_steel * inertia)
        err = abs(res.max_disp - want_disp) / want_disp
        c("fem_solve tip deflection matches Euler-Bernoulli (%.4f vs %.4f mm, err=%.2f%%)"
          % (res.max_disp, want_disp, 100.0 * err), err < 0.03)
        c("FemResult exposes positive nodes, vm_p95 <= vm_p99 <= vm_max",
          res.nodes > 50 and 0.0 < res.vm_p95 <= res.vm_p99 <= res.vm_max)
        c("FemResult exposes sorted eigenmode frequencies (%s)" % (res.mode_freqs,),
          len(res.mode_freqs) == 2 and 0.0 < res.mode_freqs[0] < res.mode_freqs[1]
          and res.first_mode == res.mode_freqs[0])
        c("safety_factor uses p95/p99 cleanly (%.2f)" % res.safety_factor(250.0),
          testing.near(res.safety_factor(250.0), 250.0 / res.vm_p95, 1e-4)
          and testing.near(res.safety_factor(250.0, percentile=99),
                           250.0 / res.vm_p99, 1e-4))
        c("cached result loads fresh with solve=False",
          testing.fem_result("assembly", path=fcad_path, solve=False).stale is None)

        # 3. values override solves fresh in scratch without clobbering cached dist.
        double = testing.fem_solve("assembly", path=fcad_path,
                                   values={"length": 200.0, "width": 20.0,
                                           "height": 15.0, "load": 600.0})
        ratio = double.max_disp / res.max_disp
        c("fem_solve(values=...) doubles deflection at 2x load (%.3f)" % ratio,
          testing.near(ratio, 2.0, 0.02))
        cached_after = testing.fem_result("assembly", path=fcad_path, solve=False)
        c("override solve did not overwrite cached dist bundle (%.4f vs %.4f)"
          % (cached_after.max_disp, res.max_disp),
          testing.near(cached_after.max_disp, res.max_disp, 1e-5))

        # 4. editing the source marks cached .npz stale; solve=False refuses it.
        with open(fcad_path, "a") as f:
            f.write("\n# edited\n")
        c("fem_result(solve=False) refuses stale bundle after source edit",
          _raises(lambda: testing.fem_result("assembly", path=fcad_path,
                                             solve=False)))
        refreshed = testing.fem_solve("assembly", path=fcad_path)
        c("fem_solve automatically refreshes stale bundle",
          refreshed.stale is None
          and testing.near(refreshed.max_disp, res.max_disp, 1e-4))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
