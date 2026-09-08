"""headless FEM solve: mesh a target, run CalculiX, export a numpy result.

runs under `freecadcmd`. drives the FEM workbench's gmsh mesher + CalculiX solver
through the granular tool calls (write_inp/ccx_run/load_results), never
`fea.run()`, which pulls in the gui and VTK (neither is available headless). the
analysis inputs come from the project's optional `fem` descriptor (material, fixed
faces, partial supports, loads, self-weight, gravity direction, mesh size, modes);
absent that, a convention fallback (fix the min-Z faces, self-weight, steel) so any
project still yields a result. target "all" solves every case the project declares.

the result is written two ways: `<target>.fem.FCStd` (the analysis document) and
`<target>.fem.npz`, a FreeCAD/VTK-free numpy bundle (boundary surface + per-node
von Mises, displacement and mode shapes) that the plain-python renderer consumes.
"""

import os

import numpy as np
import FreeCAD as App
import ObjectsFem
from femmesh import gmshtools
from femtools import ccxtools

from fcad import fem_select as fs, limits
from fcad.loader import load_project
from fcad.freecad import build_assembly, dispatch

# the four triangular faces of a (corner-node) tetrahedron.
TET_FACES = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
# a project may name a FreeCAD material card (loaded from the Materials library,
# carrying proper FEM properties) or hand a {E,nu,rho} dict. the fallback is a real
# library card; library wood cards carry no stiffness, so we ship a softwood dict
# (E along the grain, MPa / kg per m^3) for the common wood case.
DEFAULT_CARD = "CalculiX-Steel"
MATERIALS = {"steel": "CalculiX-Steel", "aluminum": "Aluminum-6061-T6",
             "wood": {"E": 11000.0, "nu": 0.40, "rho": 500.0},
             "softwood": {"E": 9000.0, "nu": 0.40, "rho": 450.0},
             "hardwood": {"E": 13000.0, "nu": 0.40, "rho": 700.0}}
DEFAULT_MODES = 6          # modal count when --modal is given without a number
GRAVITY = "9.81 m/s^2"
# 2nd-order (10-node) tets. FreeCAD defaults its gmsh mesher to 1st order, and
# 4-node tets are badly over-stiff in bending: a cantilever reads ~21% under its
# closed form at the mesh sizes fcad picks, converging from below only as the mesh
# is refined. quadratic elements land within ~0.5% using a quarter the elements,
# and CalculiX solves C3D10 natively, so the accuracy is nearly free.
#
# straight-edged, though (SECOND_ORDER_LINEAR): by default gmsh curves each midside
# node onto the real surface, and around small curved features - a drainage hole in
# a thin slat - that inverts elements, which CalculiX rejects outright as
# "nonpositive jacobian". pinning midside nodes to the edge midpoints keeps the
# element affine, so its jacobian is constant and positive wherever the linear tet
# was valid. only the *geometry* becomes faceted; the displacement field stays
# quadratic, which is where the bending accuracy actually comes from.
ELEMENT_ORDER = "2nd"
SECOND_ORDER_LINEAR = True
KILL_GRACE_MS = 5000       # gmsh gets this long to die before we stop waiting


def _get(case, attr, default=None):
    """read a FemCase attribute whether it's an object or a dict."""
    if isinstance(case, dict):
        return case.get(attr, default)
    return getattr(case, attr, default)


def _resolve_case(project, target, shape, values):
    """the FemCase for a target: project.fem (callable or mapping) or None."""
    fem = project.fem
    if callable(fem):
        return fem(target, shape, values)
    if isinstance(fem, dict):
        return fem.get(target, fem.get("__default__"))
    return fem


def _face_infos(shape):
    """a fem_select.FaceInfo per face of the shape, for selector evaluation."""
    out = []
    for i, f in enumerate(shape.Faces):
        c = f.CenterOfMass
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        bb = f.BoundBox
        out.append(fs.FaceInfo(i, (c.x, c.y, c.z), (n.x, n.y, n.z), f.Area,
                               (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)))
    return out


def _refs(selector, infos, obj):
    """resolve a selector (or list of selectors, unioned) to CalculiX face
    references [(obj, "FaceN"), ...]."""
    sels = selector if isinstance(selector, list) else [selector]
    idxs = sorted({i for s in sels for i in fs.resolve(s)(infos)})
    return [(obj, "Face%d" % (i + 1)) for i in idxs]


def _card_properties(name):
    """the Properties dict of a FreeCAD material library card by Name, or None."""
    try:
        import Materials
    except ImportError:
        return None
    mm = Materials.MaterialManager()
    for uuid, m in mm.Materials.items():
        if m.Name == name and mm.getMaterial(uuid).Properties.get("YoungsModulus"):
            return dict(mm.getMaterial(uuid).Properties)
    return None


def _material(doc, analysis, case, project=None):
    """attach the solid material: a named FreeCAD card (proper, full FEM
    properties) or an explicit {E, nu, rho} dict for materials the library lacks.

    a name is resolved against the project's own `materials` registry first, then
    fcad's built-in aliases. the case wins; absent one, the project-wide
    `material` default; absent that, steel (the frozen fallback)."""
    table = dict(MATERIALS, **(getattr(project, "materials", None) or {}))
    default = getattr(project, "material", None) or DEFAULT_CARD
    spec = _get(case, "material", default)
    spec = table.get(spec.lower(), spec) if isinstance(spec, str) else spec
    mat = ObjectsFem.makeMaterialSolid(doc, "Material")
    m = mat.Material
    if isinstance(spec, str):
        props = _card_properties(spec) or _card_properties(DEFAULT_CARD)
        m.update(props)
        m["Name"] = spec
    else:
        m["Name"] = "custom"
        m["YoungsModulus"] = "%s MPa" % spec["E"]
        m["PoissonRatio"] = str(spec["nu"])
        m["Density"] = "%s kg/m^3" % spec["rho"]
    mat.Material = m
    analysis.addObject(mat)


def _constraints(doc, analysis, case, obj, infos):
    """add the fixed/support, load and self-weight constraints described by the
    case.

    returns the [(force constraint, direction)] pairs `_run` must re-assert."""
    supports = _get(case, "supports", []) or []
    for sel in _get(case, "fixed", [] if supports else [fs.min_along("z")]):
        c = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        c.References = _refs(sel, infos, obj)
        analysis.addObject(c)
    for sup in supports:
        _support(doc, analysis, sup, obj, infos)
    loads = _get(case, "loads", []) or []
    directions = []
    for load in loads:
        c, vec = _load(doc, load, obj, infos)
        analysis.addObject(c)
        if vec is not None:
            directions.append((c, vec))
    if _get(case, "self_weight", not loads):
        g = ObjectsFem.makeConstraintSelfWeight(doc, "Gravity")
        g.GravityDirection = App.Vector(*_get(case, "gravity", (0, 0, -1)))
        g.GravityAcceleration = GRAVITY
        analysis.addObject(g)
    return directions


def _support(doc, analysis, sup, obj, infos):
    """restrain only the named translation axes of a face (`fix="yz"`).

    a fully fixed face is a clamp: every node on it is pinned, so the face cannot
    rotate and a beam held at both end faces reads far stiffer than one merely
    resting on its bearings. leaving an axis free lets the end face rotate into
    the span, which is what a bearing actually allows. face selectors cannot
    isolate an edge, so a true simple support is still out of reach; clamp one
    end and roller the other and a uniformly loaded beam at least carries the
    right peak moment."""
    c = ObjectsFem.makeConstraintDisplacement(doc, "Support")
    c.References = _refs(_get(sup, "faces"), infos, obj)
    fix = str(_get(sup, "fix", "xyz")).lower()
    for axis in "xyz":
        if axis in fix:
            setattr(c, axis + "Free", False)
            setattr(c, axis + "Displacement", 0.0)
    analysis.addObject(c)


def _load(doc, load, obj, infos):
    """(constraint, direction to re-assert later) for one declared load; a
    pressure acts along its face's normal by definition, so it has no direction."""
    refs = _refs(_get(load, "faces"), infos, obj)
    mag = float(_get(load, "magnitude", 0.0))
    if _get(load, "kind", "force") == "pressure":
        c = ObjectsFem.makeConstraintPressure(doc, "Pressure")
        c.References = refs
        # FreeCAD's internal pressure unit is mN/mm^2; pass MPa explicitly.
        c.Pressure = "%g MPa" % mag
        c.Reversed = bool(_get(load, "reversed", False))
        return c, None
    c = ObjectsFem.makeConstraintForce(doc, "Force")
    c.References = refs
    # FreeCAD's internal force unit is mN; pass newtons explicitly.
    c.Force = "%g N" % mag
    direction = _get(load, "direction", "-z")
    vec = fs._axis_vec(direction) if isinstance(direction, str) else tuple(direction)
    c.Reversed = False
    return c, App.Vector(*vec)


def _reassert_directions(directions):
    """write each declared force direction back onto its constraint.

    a ConstraintForce re-derives DirectionVector from its referenced face's
    outward normal whenever it executes - merely adding it to the analysis does
    it, and every later recompute does it again - so a direction set when the
    constraint is built is gone long before the solver reads it, and the solve
    then succeeds with the load pointing along the face normal. re-asserting here,
    with nothing left to recompute in between, is what actually reaches the
    CalculiX *CLOAD block."""
    for c, vec in directions:
        c.DirectionVector = vec


def _mesh_size(case, shape):
    size = os.environ.get("FCAD_FEM_MESH") or _get(case, "mesh_size")
    if not size:
        size = shape.BoundBox.DiagonalLength / 20.0
    return max(1.0, min(25.0, float(size)))


def _mesh_limits(mesh, case):
    """the floor on element size, and how hard gmsh chases curvature.

    `mesh_size` is only a ceiling. gmsh independently refines around curved
    faces - `MeshSizeFromCurvature` elements per full turn, 12 by default - so a
    4 mm pilot hole demands ~1 mm elements no matter how coarse the ceiling is.
    that is the right instinct for a part whose holes are the point, and ruinous
    for a model that merely contains hundreds of them: a whole fastened assembly
    can spend all its nodes resolving fastener holes and never finish meshing.
    `mesh_curvature=0` turns the chase off, `mesh_min` puts a floor under it."""
    mesh.CharacteristicLengthMin = float(_get(case, "mesh_min", 0.0) or 0.0)
    curvature = _get(case, "mesh_curvature")
    if curvature is not None:
        mesh.MeshSizeFromCurvature = int(curvature)


# what a user can actually change when a target is too big to mesh or to solve.
# the same levers in both messages, because the two failures share a cause - an
# element count nobody chose - reached from opposite ends.
LEVERS = ("raise mesh_size; set mesh_curvature/mesh_min if drilled holes are "
          "driving the element count, since gmsh refines on curvature "
          "independently of mesh_size; or undrilled=True to drop the holes")


def _slow_mesh(target, mesh, timeout):
    return ("fcad fem: gmsh did not finish meshing %r within %gs and was killed "
            "(mesh_size %g). %s. raise the bound with %s=SECONDS if the mesh is "
            "simply a large one."
            % (target, timeout, float(mesh.CharacteristicLengthMax), LEVERS,
               limits.MESH_TIMEOUT_ENV))


def _no_mesh(target, mesh):
    return ("fcad fem: gmsh produced no mesh for %r at mesh_size %g. a feature "
            "smaller than the element size cannot be meshed at all - gmsh "
            "returns nothing rather than a coarser approximation of it - so "
            "lower mesh_size, or mesh_min if that is holding the floor up, or "
            "drop the small features with undrilled=True."
            % (target, float(mesh.CharacteristicLengthMax)))


def _too_big(target, mesh, nodes, need, have):
    return ("fcad fem: %r would need about %s to solve and %s is available "
            "(%d nodes at mesh_size %g). CalculiX factors the stiffness matrix "
            "directly, so memory grows as nodes^(4/3) and halving mesh_size "
            "costs about ten times the RAM: %s. or raise the ceiling with %s=%s "
            "if the machine really has it."
            % (target, limits.human(need), limits.human(have), nodes,
               float(mesh.CharacteristicLengthMax), LEVERS, limits.MEM_ENV,
               limits.human(need)))


def _mesh(target, mesh):
    """mesh with gmsh under a wall clock bound; return the node count.

    FreeCAD's create_mesh() waits forever - waitForFinished(-1) - and reports a
    mesher that failed only by leaving FemMesh empty, which then resurfaces
    minutes later as a CalculiX complaint about a model that was never meshed.
    an unbounded wait is not academic: gmsh has run a quarter of an hour at
    7.6 GB on a fastened assembly without finishing. this is the same granular
    seam fcad already uses for the solver - prepare, run, read back - so the run
    is ours to bound and to kill."""
    timeout = limits.mesh_timeout()
    tools = gmshtools.GmshTools(mesh)
    tools.prepare()
    proc = tools.compute()
    if not proc.waitForFinished(int(timeout * 1000)):
        proc.kill()
        proc.waitForFinished(KILL_GRACE_MS)
        raise SystemExit(_slow_mesh(target, mesh, timeout))
    if not mesh.FemMesh.NodeCount:
        raise SystemExit(_no_mesh(target, mesh))
    return mesh.FemMesh.NodeCount


def _preflight(target, mesh, nodes):
    """refuse a solve the machine cannot hold, before CalculiX starts.

    the honest failure: seconds and a sentence, rather than four minutes, an
    exit code of -9 and a desktop that has been in reclaim throughout. the
    estimate is a fit, so it is advisory in both directions - MEM_ENV raises the
    ceiling for a solve that really does fit, and limits.limit_child stands
    behind it for one that really does not."""
    have = limits.budget()
    need = limits.ccx_bytes(nodes)
    if have and need > have:
        raise SystemExit(_too_big(target, mesh, nodes, need, have))


def _modes(case):
    """resolved eigenmode count from the case and FCAD_FEM_* env overrides."""
    modes = int(_get(case, "modes", 0) or 0)
    if os.environ.get("FCAD_FEM_MODES"):
        return int(os.environ["FCAD_FEM_MODES"])
    if os.environ.get("FCAD_FEM_MODAL"):
        return modes or DEFAULT_MODES
    return modes


def _boundary(femmesh):
    """the boundary surface of the tet mesh as (coords, tris, node_ids).

    a tet face shared by two tets is interior; one seen by a single tet is on the
    boundary. we keep only the corner nodes those boundary triangles reference and
    remap to a compact 0..n index (quadratic midside nodes are dropped; a faceted
    surface is all the renderer needs)."""
    seen = {}
    for eid in femmesh.Volumes:
        corners = femmesh.getElementNodes(eid)[:4]
        for a, b, c in TET_FACES:
            tri = (corners[a], corners[b], corners[c])
            key = frozenset(tri)
            if key in seen:
                seen[key][0] += 1
            else:
                seen[key] = [1, tri]
    boundary = [tri for cnt, tri in seen.values() if cnt == 1]
    used = sorted({n for tri in boundary for n in tri})
    row = {nid: i for i, nid in enumerate(used)}
    nodes = femmesh.Nodes
    coords = np.array([[nodes[n].x, nodes[n].y, nodes[n].z] for n in used], np.float32)
    tris = np.array([[row[a], row[b], row[c]] for a, b, c in boundary], np.int32)
    return coords, tris, used


def _field(result, node_ids, attr):
    """gather a per-node result field onto the compact boundary node order."""
    res_row = {nid: i for i, nid in enumerate(result.NodeNumbers)}
    vals = getattr(result, attr)
    return [vals[res_row[n]] for n in node_ids]


def _disp(result, node_ids):
    res_row = {nid: i for i, nid in enumerate(result.NodeNumbers)}
    dv = result.DisplacementVectors
    return np.array([[dv[res_row[n]].x, dv[res_row[n]].y, dv[res_row[n]].z]
                     for n in node_ids], np.float32)


def _result_objs(analysis):
    return [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]


def _no_result(target, solver, mesh):
    """CalculiX wrote nothing usable, having got past the preflight. the two
    remaining causes pull `mesh_size` in opposite directions, so the message
    names both rather than guessing: the solve outgrew the estimate and hit the
    inherited ceiling (a 2nd-order mesh carries several times the nodes the same
    mesh_size gave when elements were linear, which is what bites a project whose
    mesh_size was tuned against the old default), or the mesh is too coarse or
    too notched and gmsh emitted degenerate elements ccx rejects outright as
    "nonpositive jacobian"."""
    return ("fcad fem: CalculiX produced no result for %r (%s analysis, %d nodes "
            "at mesh_size %g). raise mesh_size, or raise %s, if it exhausted "
            "memory - 2nd-order elements need far fewer nodes for the same "
            "accuracy - or lower mesh_size, or simplify the part's small "
            "features, if the mesh has degenerate elements."
            % (target, solver.AnalysisType, mesh.FemMesh.NodeCount,
               float(mesh.CharacteristicLengthMax), limits.MEM_ENV))


def _run(analysis, solver, target, mesh, directions=()):
    _reassert_directions(directions)
    fea = ccxtools.FemToolsCcx(analysis, solver)
    fea.purge_results()
    fea.update_objects()
    fea.setup_working_dir()
    fea.write_inp_file()
    fea.ccx_run()
    fea.load_results()
    results = [r for r in _result_objs(analysis) if r.Mesh is not None]
    if not results:
        raise SystemExit(_no_result(target, solver, mesh))
    return results


def _undrilled(project, target):
    """does this target's case ask for the geometry without its drilled holes?

    read from the mapping before the shape exists, since it decides which shape
    to build; a callable `fem` descriptor is handed the shape and so cannot
    answer this."""
    fem = project.fem
    case = fem.get(target) if isinstance(fem, dict) else None
    return bool(_get(case, "undrilled", False)) if case is not None else False


def solve(project, values, target, dist):
    """build, mesh and solve the FEM analysis for a target; return the npz dict."""
    shape = build_assembly.target_shape(project, values, target,
                                        undrilled=_undrilled(project, target))
    if shape is None:
        raise SystemExit("fcad fem: no such target %r" % target)
    case = _resolve_case(project, target, shape, values)
    infos = _face_infos(shape)
    modes = _modes(case)

    doc = App.newDocument(target + "_fem")
    obj = doc.addObject("Part::Feature", "Target")
    obj.Shape = shape
    doc.recompute()
    analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
    solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
    analysis.addObject(solver)
    _material(doc, analysis, case, project)
    directions = _constraints(doc, analysis, case, obj, infos)
    mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMesh")
    mesh.Shape = obj
    mesh.CharacteristicLengthMax = _mesh_size(case, shape)
    _mesh_limits(mesh, case)
    mesh.ElementOrder = ELEMENT_ORDER
    mesh.SecondOrderLinear = SECOND_ORDER_LINEAR
    analysis.addObject(mesh)
    doc.recompute()
    _preflight(target, mesh, _mesh(target, mesh))

    out = {"target": target, "units": "mm/MPa", "n_modes": 0}
    solver.AnalysisType = "static"
    static = _run(analysis, solver, target, mesh, directions)[0]
    coords, tris, node_ids = _boundary(static.Mesh.FemMesh)
    disp = _disp(static, node_ids)
    vm = np.array(_field(static, node_ids, "vonMises"), np.float32)
    # the nodal maximum lands on whatever singularity the model contains - a
    # clamped face, the sharp internal corner of a drilled hole - where linear
    # elasticity has no finite answer and the value simply grows as the mesh is
    # refined. the high percentiles are the part of the field a reader can size
    # against, so they travel with the result rather than being recovered later.
    out.update(nodes=coords, tris=tris, von_mises=vm,
               von_mises_p95=np.float32(np.percentile(vm, 95)),
               von_mises_p99=np.float32(np.percentile(vm, 99)),
               disp=disp, disp_mag=np.linalg.norm(disp, axis=1).astype(np.float32),
               bbox_diag=np.float32(shape.BoundBox.DiagonalLength))

    if modes > 0:
        solver.AnalysisType = "frequency"
        solver.EigenmodesCount = modes
        results = sorted(_run(analysis, solver, target, mesh, directions),
                         key=lambda r: r.EigenmodeFrequency)
        out["n_modes"] = len(results)
        out["mode_freqs"] = np.array([r.EigenmodeFrequency for r in results], np.float32)
        out["mode_disp"] = np.array([_disp(r, node_ids) for r in results], np.float32)

    doc.saveAs(os.path.join(dist, target + ".fem.FCStd"))
    App.closeDocument(doc.Name)
    return out


def _targets(project, target):
    """the targets to solve; "all" means every case the project declares."""
    if target != "all":
        return [target]
    if not isinstance(project.fem, dict):
        raise SystemExit("fcad fem all: this project declares no fem cases to "
                         "enumerate (its FEM is not a mapping)")
    return [k for k in project.fem if not k.startswith("__")]


def main(target="assembly"):
    project = load_project()
    values = project.defaults()
    dist = project.dist
    dispatch.dirs(project)            # ensure dist/ exists
    for name in _targets(project, target):
        out = solve(project, values, name, dist)
        path = os.path.join(dist, name + ".fem.npz")
        np.savez_compressed(path, **out)
        print("fem ok: target=%s nodes=%d modes=%d vm_p95=%.2f MPa "
              "vm_max=%.1f MPa disp=%.3f mm -> %s" %
              (name, len(out["nodes"]), out["n_modes"], out["von_mises_p95"],
               out["von_mises"].max(), out["disp_mag"].max(), path))
