"""headless FEM solve: mesh a target, run CalculiX, export a numpy result.

runs under `freecadcmd`. drives the FEM workbench's gmsh mesher + CalculiX solver
through the granular tool calls (write_inp/ccx_run/load_results), never
`fea.run()`, which pulls in the gui and VTK (neither is available headless). the
analysis inputs come from the project's optional `fem` descriptor (material, fixed
faces, loads, self-weight, mesh size, modes); absent that, a convention fallback
(fix the min-Z faces, self-weight, steel) so any project still yields a result.

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

from fcad import fem_select as fs
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
    """add the fixed, load and self-weight constraints described by the case."""
    for sel in _get(case, "fixed", [fs.min_along("z")]):
        c = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        c.References = _refs(sel, infos, obj)
        analysis.addObject(c)
    loads = _get(case, "loads", []) or []
    for load in loads:
        analysis.addObject(_load(doc, load, obj, infos))
    if _get(case, "self_weight", not loads):
        g = ObjectsFem.makeConstraintSelfWeight(doc, "Gravity")
        g.GravityDirection = App.Vector(*_get(case, "gravity", (0, 0, -1)))
        g.GravityAcceleration = GRAVITY
        analysis.addObject(g)


def _load(doc, load, obj, infos):
    refs = _refs(_get(load, "faces"), infos, obj)
    mag = float(_get(load, "magnitude", 0.0))
    if _get(load, "kind", "force") == "pressure":
        c = ObjectsFem.makeConstraintPressure(doc, "Pressure")
        c.References = refs
        # FreeCAD's internal pressure unit is mN/mm^2; pass MPa explicitly.
        c.Pressure = "%g MPa" % mag
        c.Reversed = bool(_get(load, "reversed", False))
        return c
    c = ObjectsFem.makeConstraintForce(doc, "Force")
    c.References = refs
    # FreeCAD's internal force unit is mN; pass newtons explicitly.
    c.Force = "%g N" % mag
    direction = _get(load, "direction", "-z")
    vec = fs._axis_vec(direction) if isinstance(direction, str) else tuple(direction)
    c.DirectionVector = App.Vector(*vec)
    c.Reversed = False
    return c


def _mesh_size(case, shape):
    size = os.environ.get("FCAD_FEM_MESH") or _get(case, "mesh_size")
    if not size:
        size = shape.BoundBox.DiagonalLength / 20.0
    return max(1.0, min(25.0, float(size)))


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


def _run(analysis, solver, target):
    fea = ccxtools.FemToolsCcx(analysis, solver)
    fea.purge_results()
    fea.update_objects()
    fea.setup_working_dir()
    fea.write_inp_file()
    fea.ccx_run()
    fea.load_results()
    results = [r for r in _result_objs(analysis) if r.Mesh is not None]
    if not results:
        # CalculiX wrote no usable result, almost always degenerate mesh elements
        # ("nonpositive jacobian"), common on heavily-notched/thin solids.
        raise SystemExit(
            "fcad fem: CalculiX produced no result for %r (%s analysis). the mesh "
            "likely has degenerate elements; try a smaller mesh_size or simplify the "
            "part's small features." % (target, solver.AnalysisType))
    return results


def solve(project, values, target, dist):
    """build, mesh and solve the FEM analysis for a target; return the npz dict."""
    shape = build_assembly.target_shape(project, values, target)
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
    _constraints(doc, analysis, case, obj, infos)
    mesh = ObjectsFem.makeMeshGmsh(doc, "FEMMesh")
    mesh.Shape = obj
    mesh.CharacteristicLengthMax = _mesh_size(case, shape)
    analysis.addObject(mesh)
    doc.recompute()
    gmshtools.GmshTools(mesh).create_mesh()

    out = {"target": target, "units": "mm/MPa", "n_modes": 0}
    solver.AnalysisType = "static"
    static = _run(analysis, solver, target)[0]
    coords, tris, node_ids = _boundary(static.Mesh.FemMesh)
    disp = _disp(static, node_ids)
    out.update(nodes=coords, tris=tris,
               von_mises=np.array(_field(static, node_ids, "vonMises"), np.float32),
               disp=disp, disp_mag=np.linalg.norm(disp, axis=1).astype(np.float32),
               bbox_diag=np.float32(shape.BoundBox.DiagonalLength))

    if modes > 0:
        solver.AnalysisType = "frequency"
        solver.EigenmodesCount = modes
        results = sorted(_run(analysis, solver, target),
                         key=lambda r: r.EigenmodeFrequency)
        out["n_modes"] = len(results)
        out["mode_freqs"] = np.array([r.EigenmodeFrequency for r in results], np.float32)
        out["mode_disp"] = np.array([_disp(r, node_ids) for r in results], np.float32)

    doc.saveAs(os.path.join(dist, target + ".fem.FCStd"))
    App.closeDocument(doc.Name)
    return out


def main(target="assembly"):
    project = load_project()
    values = project.defaults()
    dist = project.dist
    dispatch.dirs(project)            # ensure dist/ exists
    out = solve(project, values, target, dist)
    np.savez_compressed(os.path.join(dist, target + ".fem.npz"), **out)
    print("fem ok: target=%s nodes=%d modes=%d -> %s" %
          (target, len(out["nodes"]), out["n_modes"],
           os.path.join(dist, target + ".fem.npz")))
