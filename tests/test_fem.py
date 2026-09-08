"""end-to-end test for the headless FEM solve + the npz it hands the renderer.

drives the real stack (gmsh mesher + CalculiX solver) on tiny in-memory projects,
then asserts the numpy bundle fem.solve returns: a watertight boundary surface,
per-node von Mises + displacement that actually responded to the load, and the
requested eigenmodes. also unit-tests the pure-python face selectors that decide
which faces get fixed/loaded (no FreeCAD needed for those).

the cantilever cases check the solve against closed-form beam theory rather than
against itself, which is the only way to catch a result that is wrong but
plausible: a mesh too stiff to bend, or a load pulling the wrong way.

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
from fcad.freecad import build_assembly, fem


# the cantilever: steel, fixed at its -x end, slender enough (L/h = 15) that
# euler-bernoulli theory holds to well under the tolerance -- shear adds ~0.4%.
BEAM = (300.0, 30.0, 20.0)          # length, width, height (mm)
STEEL = {"E": 210000.0, "nu": 0.30, "rho": 7900.0}
# gmsh reseeds between runs - the same solve here meshed to 365 and to 373 nodes
# on consecutive calls - so a tolerance is only meaningful against the *spread*,
# not against one draw. sampled 12x per size: mesh_size 10 wanders to 4.8% error
# against this 5% tolerance (and a suite run caught a 19% draw), while 6 holds
# 0.4% for the sake of ~1000 nodes instead of ~370. cheap, and not flaky.
BEAM_MESH = 6.0
BEAM_TOL = 0.05
TOP_PRESSURE = 0.1                  # MPa, uniformly on the top face
TIP_FORCE = 500.0                   # N, -z on the +x end face
HOLE_R = 2.0                        # a 4mm pilot, the feature `undrilled` drops
FINER = 0.6                         # mesh_size multiplier for the convergence check


class _Spec:
    name = "box"
    embeds = False
    placements = [App.Placement()]


class _Project:
    """the minimal project surface fem.solve reads: one part, one fem case."""

    name = "tfem"

    def __init__(self, case, shape=None, profile=None):
        self.fem = {"box": case}
        self._shape = shape or (lambda: Part.makeBox(100, 20, 10))
        self._profile = profile

    def compute(self, values):
        return {"specs": [_Spec()]}

    def from_spec(self, spec):
        return self._shape()

    def profile(self, spec):
        return self._profile

    def defaults(self):
        return {}


def _box_case():
    return SimpleNamespace(
        material=STEEL, fixed=[fs.min_along("z")],
        loads=[SimpleNamespace(kind="force", faces=[fs.max_along("z")],
                               magnitude=500.0, direction="-z")],
        self_weight=False, mesh_size=6.0, modes=2)


def _beam_case(load):
    return SimpleNamespace(material=STEEL, fixed=[fs.min_along("x")],
                           loads=[load], self_weight=False,
                           mesh_size=BEAM_MESH, modes=0)


def _solve(project):
    tmp = tempfile.mkdtemp()
    try:
        return fem.solve(project, {}, "box", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


def _solve_checks(out):
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


def _tip(out):
    """(deflection, displacement vector) at the most-displaced node, which for a
    cantilever is the free end."""
    i = int(out["disp_mag"].argmax())
    return float(out["disp_mag"][i]), out["disp"][i]


def _cantilever_checks():
    """the two load paths against closed-form beam theory.

    a uniformly loaded cantilever tips by w*L^4/(8*E*I), a tip-loaded one by
    F*L^3/(3*E*I). checking the numbers rather than just "it moved" is what
    catches a solve that is wrong but plausible: 1st-order tets are over-stiff in
    bending and read ~21% low, and a force load whose direction was silently
    replaced by its face's normal loads the beam axially instead (off by ~600x,
    with CalculiX reporting success either way)."""
    length, width, height = BEAM
    inertia = width * height ** 3 / 12.0
    theory_p = (TOP_PRESSURE * width) * length ** 4 / (8.0 * STEEL["E"] * inertia)
    theory_f = TIP_FORCE * length ** 3 / (3.0 * STEEL["E"] * inertia)
    beam = lambda: Part.makeBox(*BEAM)

    p_tip, _ = _tip(_solve(_Project(_beam_case(SimpleNamespace(
        kind="pressure", faces=fs.max_along("z"), magnitude=TOP_PRESSURE)), beam)))
    f_tip, f_vec = _tip(_solve(_Project(_beam_case(SimpleNamespace(
        kind="force", faces=[fs.max_along("x")], magnitude=TIP_FORCE,
        direction="-z")), beam)))
    near = lambda got, want: abs(got - want) <= BEAM_TOL * want
    return [
        ("pressure tip deflection %.4f mm vs w*L^4/(8EI) = %.4f mm"
         % (p_tip, theory_p), near(p_tip, theory_p)),
        ("tip force deflection %.4f mm vs F*L^3/(3EI) = %.4f mm"
         % (f_tip, theory_f), near(f_tip, theory_f)),
        ("tip force acts along its declared -z, not the face normal",
         float(f_vec[2]) < 0.0 and abs(float(f_vec[2])) > 5.0 * abs(float(f_vec[0]))),
    ]


def _determinism_checks():
    """R6.1: the same design meshes to the same mesh twice.

    gmsh's parallel 3d algorithm is not reproducible - identical runs returned
    365 and 373 nodes here, and 358429 to 359442 on a finer one - and
    `Mesh.RandomSeed` does not fix it, because the variation is thread
    interleaving rather than the seed. FreeCAD sets `General.NumThreads` to the
    cpu count, so every solve inherited it. that reseeding is what lets an
    unchanged model report a peak 350x different from the last run, and it made
    this file's own 5% theory check draw a 19% error. equality is the assertion,
    deliberately: a tolerance would just be the flakiness written down.

    the solver was the other half, and the worse one. CalculiX at 16 threads
    returned four different tip deflections in ten runs of one *fixed* .inp,
    spanning 6.5%, with the low ones 6.4% under the closed form the
    single-threaded run matched to 0.25% - so it was not merely unrepeatable, it
    was intermittently wrong, and this file's own theory check failed on it. one
    thread, one answer. both halves pinned, a result is reproducible end to end,
    which is what lets this assert equality rather than a tolerance."""
    beam = lambda: Part.makeBox(*BEAM)
    load = lambda: SimpleNamespace(kind="force", faces=[fs.max_along("x")],
                                   magnitude=TIP_FORCE, direction="-z")
    runs = [_solve(_Project(_beam_case(load()), beam)) for _ in range(2)]
    counts = [len(r["nodes"]) for r in runs]
    tips = [_tip(r)[0] for r in runs]
    peaks = [float(r["von_mises"].max()) for r in runs]
    return [
        ("a re-mesh gives the same node count (%d, %d)" % tuple(counts),
         counts[0] == counts[1]),
        ("and the same node positions",
         bool((runs[0]["nodes"] == runs[1]["nodes"]).all())),
        ("a re-solve gives the same deflection (%.6f, %.6f mm)" % tuple(tips),
         tips[0] == tips[1]),
        ("and the same peak stress (%.4f, %.4f MPa)" % tuple(peaks),
         peaks[0] == peaks[1]),
    ]


def _convergence_checks():
    """R6.1: refining the mesh moves the deflection hardly at all.

    which is the whole case for reading a result off `disp` and `von_mises_p95`
    rather than off the peak. the peak is a sample of the mesh: an unchanged
    planter board re-solved on a fresh seed reported 6.2 MPa and then 2191.1, a
    factor of 350, with its deflection stable to four figures. a seed cannot be
    varied deterministically enough to assert on, but refinement can, and it
    exercises the same property - so the peak is deliberately left unasserted
    here, because pinning a singular value is exactly the mistake."""
    beam = lambda: Part.makeBox(*BEAM)
    load = lambda: SimpleNamespace(kind="pressure", faces=fs.max_along("z"),
                                   magnitude=TOP_PRESSURE)
    outs = []
    for size in (BEAM_MESH, BEAM_MESH * FINER):
        case = _beam_case(load())
        case.mesh_size = size
        outs.append(_solve(_Project(case, beam)))
    coarse, fine = (_tip(o)[0] for o in outs)
    p95 = [float(o["von_mises_p95"]) for o in outs]
    return [
        ("deflection is mesh-independent: %.4f vs %.4f mm at %gx the elements"
         % (coarse, fine, FINER ** -3), abs(fine - coarse) <= 0.02 * coarse),
        ("p95 is nearly so: %.2f vs %.2f MPa" % tuple(p95),
         abs(p95[1] - p95[0]) <= 0.35 * p95[0]),
    ]


def _held(**held):
    """the beam under the same uniform pressure, held however the case says."""
    return SimpleNamespace(
        material=STEEL, loads=[SimpleNamespace(
            kind="pressure", faces=fs.max_along("z"), magnitude=TOP_PRESSURE)],
        self_weight=False, mesh_size=BEAM_MESH, modes=0, **held)


def _support_checks():
    """R6.2: `supports` restrains only the axes it names.

    a fully fixed face is a clamp - every node pinned, so it cannot rotate - and
    holding a beam that way at both ends reads far stiffer than one resting on
    its bearings. the failure this guards is silent: were `fix` ignored and every
    axis pinned, the roller would simply solve as a second clamp and report a
    plausible number. so it is checked as an ordering, which is the part beam
    theory is unambiguous about, rather than against a closed form no whole-face
    restraint actually matches."""
    beam = lambda: Part.makeBox(*BEAM)
    ends = [fs.min_along("x"), fs.max_along("x")]
    clamped, _ = _tip(_solve(_Project(_held(fixed=ends), beam)))
    rolled, _ = _tip(_solve(_Project(_held(
        fixed=[ends[0]], supports=[{"faces": [ends[1]], "fix": "yz"}]), beam)))
    free, _ = _tip(_solve(_Project(_held(fixed=[ends[0]]), beam)))
    return [
        ("a rolled end is softer than a clamped one (%.4f vs %.4f mm)"
         % (rolled, clamped), rolled > clamped * 1.5),
        ("and stiffer than no support at all (%.4f vs %.4f mm)" % (rolled, free),
         rolled < free),
    ]


def _drilled():
    """the beam with a hole through its thickness, and the 2d profile that
    describes it without one. the profile is the part's *mid-plane* section, the
    same convention `_defining_sketch` places its sketch on, so an undrilled
    rebuild has to land in the same frame as the solid it replaces."""
    length, width, height = BEAM
    pts = [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]
    hole = Part.makeCylinder(HOLE_R, height * 2, App.Vector(
        length / 2.0, width / 2.0, -height))
    box = Part.makeBox(length, width, height, App.Vector(0, 0, -height / 2.0))
    return (lambda: box.cut(hole)), (pts, height)


def _undrilled_checks():
    """R6.2: `undrilled` drops the holes without moving the part.

    a whole fastened assembly is unmeshable drilled - gmsh refines on curvature,
    so every pilot hole demands elements a fraction of its diameter - and the
    escape is to solve the blank. the risk it carries is that the blank is built
    from a different description than the solid, so this pins the frames
    together: same bounding box, more material, fewer faces."""
    shape, profile = _drilled()
    project = _Project(_box_case(), shape, profile)
    kept = build_assembly.target_shape(project, {}, "box")
    blank = build_assembly.target_shape(project, {}, "box", undrilled=True)
    box = lambda s: [round(v, 6) for v in
                     (s.BoundBox.XMin, s.BoundBox.YMin, s.BoundBox.ZMin,
                      s.BoundBox.XMax, s.BoundBox.YMax, s.BoundBox.ZMax)]
    return [
        ("undrilled occupies the same space as the drilled part",
         box(blank) == box(kept)),
        ("undrilled has the hole's material back",
         blank.Volume > kept.Volume * 1.0001),
        ("undrilled has fewer faces to refine on",
         len(blank.Faces) < len(kept.Faces)),
        ("a part with no 2d profile falls back to its real solid",
         abs(build_assembly.target_shape(_Project(_box_case(), shape), {}, "box",
                                         undrilled=True).Volume - kept.Volume)
         < 1e-6),
    ]


def _mesh_limit_checks():
    """R6.2: `mesh_min`/`mesh_curvature` reach the gmsh properties they name.

    both are the escape hatch when the holes must stay, and both fail silently if
    the property name is wrong - the mesh simply comes back as it always did."""
    mesh = SimpleNamespace(CharacteristicLengthMin=None, MeshSizeFromCurvature=12)
    fem._mesh_limits(mesh, {"mesh_min": 2.5, "mesh_curvature": 0})
    default = SimpleNamespace(CharacteristicLengthMin=None,
                              MeshSizeFromCurvature=12)
    fem._mesh_limits(default, {})
    return [
        ("mesh_min sets the element floor", mesh.CharacteristicLengthMin == 2.5),
        ("mesh_curvature=0 stops the curvature chase",
         mesh.MeshSizeFromCurvature == 0),
        ("a case declaring neither leaves gmsh's own curvature default alone",
         default.MeshSizeFromCurvature == 12),
    ]


def _percentile_checks(out):
    """R6.1: the percentiles the npz carries beside the peak are ordered and
    real. the peak itself sits on a singularity and reports the mesh."""
    vm = out["von_mises"]
    return [
        ("von Mises p95 <= p99 <= max",
         0.0 < float(out["von_mises_p95"]) <= float(out["von_mises_p99"])
         <= float(vm.max())),
        ("p95 discards the singular tail",
         float(out["von_mises_p95"]) < float(vm.max())),
    ]


def main():
    box = _solve(_Project(_box_case()))
    checks = (_selector_checks() + _solve_checks(box) + _cantilever_checks()
              + _percentile_checks(box) + _determinism_checks()
              + _convergence_checks() + _support_checks()
              + _undrilled_checks() + _mesh_limit_checks())
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
