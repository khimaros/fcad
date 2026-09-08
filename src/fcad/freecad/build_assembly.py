"""build the final model as a real linked assembly plus its exports.

`<name>.FCStd` links each part file (one object reused N times via App::Link at
each placement) so it is a true assembly. the neutral exports (step/stl/svg/dxf
and the drawing) are taken from an equivalent compound of the placed solids so
they do not depend on cross-document link resolution.
"""

import csv
import os
import sys

import FreeCAD as App
import Part

from fcad import cutlist
from fcad.freecad import util as fcutil

# the built-in Assembly workbench ships its python modules here.
ASSEMBLY_MOD = "/usr/share/freecad/Mod/Assembly"
JOINT_FIXED = 0  # index into JointObject.JointTypes

V = App.Vector
# the assembly drawing sheet uses the same projection-aligned grid as the parts:
# top above front, right beside it, isometric in the free cell. each view has an
# explicit x-axis so its projection frame is well-defined. (label, direction,
# xdir, col, row).
ASM_VIEWS = [
    ("top",   V(0, 0, 1),  V(1, 0, 0),  0, 0),
    ("iso",   V(1, -1, 1), V(1, 1, 0),  1, 0),
    ("front", V(0, -1, 0), V(1, 0, 0),  0, 1),
    ("right", V(1, 0, 0),  V(0, 1, 0),  1, 1),
]


def placed_shapes(project, specs):
    """one placed BREP solid per instance, in assembly coordinates.

    each board is already drilled by project.from_spec (the screw holes belong
    to the part now), so this just builds and places the solids."""
    out = []
    for spec in specs:
        base = project.from_spec(spec)
        for pl in spec.placements:
            s = base.copy()
            s.Placement = pl
            out.append(s)
    return out


def blank_shape(project, spec):
    """a part's solid without its drilled features, from its defining profile.

    a global model wants the load path, not the fastener holes, and the holes are
    what make one unsolvable: gmsh sizes elements from curvature, so a 4 mm pilot
    pulls the local element size to about a millimetre however coarse the ceiling
    is. a whole fastened assembly then spends every node resolving fastener holes
    and never finishes meshing. a spec with no 2d profile falls back to its real
    solid."""
    prof = project.profile(spec)
    if prof is None:
        return project.from_spec(spec)
    pts, thickness = prof
    poly = [App.Vector(x, y, -thickness / 2.0) for x, y in pts]
    return Part.Face(Part.makePolygon(poly + [poly[0]])).extrude(
        App.Vector(0, 0, thickness))


def target_shape(project, values, target, undrilled=False):
    """one connected BREP solid for a FEM target (assembly or a part name).

    a FEM solve needs a single, connected solid. for the assembly we fuse the
    structural placed solids (parts flagged `embeds`, e.g. screws, are dropped,
    as in find_overlaps) into one bonded body; touching boards share faces so the
    union is connected, which CalculiX linear-static treats as a rigid joint. for
    a part name we return its single solid in its natural (unplaced) frame, matching
    how dist/parts/<name>.stl is built. `undrilled` builds every part from its
    profile instead, dropping the holes. returns the Part.Shape, or None if no
    such part."""
    data = project.compute(values)
    specs = data["specs"]
    build = (lambda s: blank_shape(project, s)) if undrilled else project.from_spec
    if target in ("assembly", project.name):
        shapes = []
        for spec in specs:
            if getattr(spec, "embeds", False):
                continue
            base = build(spec)
            for pl in spec.placements:
                s = base.copy()
                s.Placement = pl
                shapes.append(s)
        fused = shapes[0]
        for s in shapes[1:]:
            fused = fused.fuse(s)
        return fused.removeSplitter()
    spec = next((s for s in specs if s.name == target), None)
    return build(spec) if spec is not None else None


def find_overlaps(project, values, tol=1.0):
    """structural solids whose volumes interpenetrate (touching faces give zero
    common volume, so only real interference is reported). parts flagged
    `embeds` (e.g. screws, which intentionally sink into the wood) are excluded.
    returns (name_a, name_b, mm^3)."""
    data = project.compute(values)
    solids = []
    for spec in data["specs"]:
        if getattr(spec, "embeds", False):
            continue
        base = project.from_spec(spec)
        for i, pl in enumerate(spec.placements):
            s = base.copy()
            s.Placement = pl
            solids.append(("%s_%03d" % (spec.name, i + 1), s))
    hits = []
    for i, (ni, si) in enumerate(solids):
        for nj, sj in solids[i + 1:]:
            if not si.BoundBox.intersect(sj.BoundBox):
                continue
            try:
                vol = si.common(sj).Volume
            except Exception:
                vol = 0.0
            if vol > tol:
                hits.append((ni, nj, vol))
    return hits


def find_unconstrained(path):
    """assembly components (App::Links) that are neither grounded nor used by
    any joint. reads the built assembly file so it validates the real artifact."""
    doc = App.openDocument(path)
    try:
        links = [o for o in doc.Objects if o.TypeId == "App::Link"]
        constrained = set()
        for o in doc.Objects:
            g = getattr(o, "ObjectToGround", None)
            if g is not None:
                constrained.add(g.Name)
            for prop in ("Reference1", "Reference2"):
                ref = getattr(o, prop, None)
                if ref and len(ref) == 2:
                    for el in ref[1]:
                        constrained.add(el.split(".")[0])
        return [l.Name for l in links if l.Name not in constrained]
    finally:
        App.closeDocument(doc.Name)


def write_bom(specs, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["part", "qty", "profile", "length_mm"])
        for spec in specs:
            w.writerow([spec.name, len(spec.placements),
                        getattr(spec, "profile", ""), round(spec.length, 1)])


def profile_demand(specs):
    """{bom profile: {cut length: qty}} - the bom, keyed for the cut list."""
    demand = {}
    for spec in specs:
        prof = getattr(spec, "profile", "")
        if not prof:
            continue
        length = round(spec.length, 1)
        rows_ = demand.setdefault(prof, {})
        rows_[length] = rows_.get(length, 0) + len(spec.placements)
    return demand


def write_cutlist(project, specs, path):
    """pack each profile's bom rows into the stock lengths declared for it.

    a profile with no stock (fasteners, bought parts) is not cut from stock and
    is simply absent from the plan."""
    opts = cutlist.options_from_env()
    override = opts.pop("stock")
    out = []
    for prof, demand in profile_demand(specs).items():
        lengths = cutlist.resolve_lengths(project.stock_for(prof), override, prof)
        if not lengths:
            continue
        plan = cutlist.plan(demand, lengths, **opts)
        out.append((prof, plan))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cutlist.CSV_HEADER)
        for prof, plan in out:
            w.writerows(cutlist.rows(prof, plan))
    for prof, plan in out:
        for line in cutlist.summary(prof, plan):
            print(line)


def build_jointed_doc(project, values, data, parts_dir, path):
    """assemble <name>.FCStd as a real Assembly-workbench assembly.

    each instance is an App::Link into its saved part file, positioned at its
    computed placement. parts flagged `grounded` are grounded; every other part
    is mated to a datum (the first grounded part) with a Fixed joint, and the
    solver resolves the assembly. links are pre-positioned so the (fully
    constrained) solve is stable.
    """
    sys.path.insert(0, ASSEMBLY_MOD)
    import JointObject

    doc = App.newDocument(project.name)
    # cross-document links resolve relative to the owner file, so save first.
    doc.saveAs(path)
    fcutil.add_varset(doc, project, values)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    joints = asm.newObject("Assembly::JointGroup", "Joints")

    part_docs = []
    grounded, others = [], []
    for spec in data["specs"]:
        part_path = os.path.join(parts_dir, spec.name + ".FCStd")
        if not os.path.exists(part_path):
            continue
        part_doc = App.openDocument(part_path)
        part_docs.append(part_doc)
        src = part_doc.getObject(spec.name)
        for i, pl in enumerate(spec.placements):
            link = asm.newObject("App::Link", "%s_%03d" % (spec.name, i + 1))
            link.LinkedObject = src
            link.LinkTransform = False
            link.Placement = pl
            link.Visibility = True
            # grounded parts are anchored; every other part (screws included) is
            # mated to a datum with a fixed joint.
            (grounded if getattr(spec, "grounded", False) else others).append(link)
    # a minimal project may flag nothing; anchor the first part so the assembly is
    # still fully constrained (grounded datum + fixed joints) and passes `check`.
    if not grounded and others:
        grounded.append(others.pop(0))
    doc.recompute()

    def whole(obj):
        return [asm, [obj.Name + ".", obj.Name + "."]]

    for link in grounded:
        gj = joints.newObject("App::FeaturePython", "Ground_" + link.Name)
        JointObject.GroundedJoint(gj, link)
    datum = grounded[0] if grounded else None
    if datum is not None:
        for link in others:
            j = joints.newObject("App::FeaturePython", "Fix_" + link.Name)
            JointObject.Joint(j, JOINT_FIXED)
            j.Reference1 = whole(datum)
            j.Reference2 = whole(link)
    # the links are already realized (recompute above, before the joints exist)
    # and pre-positioned at the solution, so `solve()` alone fixes the assembly.
    # we deliberately do NOT recompute again: the fixed joints reference the
    # assembly container while the container owns them, so the joint graph is a
    # non-DAG cycle a topological recompute can never settle; every post-joint
    # recompute just re-runs it, logging "still touched after recompute" once per
    # joint (and "graph must be a DAG"). purge the residual touched flags so the
    # saved doc opens clean and the gui viewer needs no recompute either.
    asm.solve()
    for o in doc.Objects:
        o.purgeTouched()
    doc.save()
    fcutil.export_gui_state(doc, path)
    App.closeDocument(doc.Name)
    for pd in part_docs:
        App.closeDocument(pd.Name)


def build(project, values, dirs, formats):
    data = project.compute(values)
    specs = data["specs"]

    # always: the 3d diff needs both revisions' placements to pair instances, and
    # it only ever sees dist/, never the project code that produced them.
    fcutil.export_placements(
        {spec.name: spec.placements for spec in specs},
        fcutil.placements_path(dirs["dist"], project.name))
    # and the two per-part flags no renderer can infer from geometry: the anchor,
    # and which parts are fasteners. the assembly animator sequences on both.
    fcutil.export_parts(specs, fcutil.parts_path(dirs["dist"], project.name))

    if "bom" in formats:
        write_bom(specs, os.path.join(dirs["dist"], project.name + "-bom.csv"))
    if "cutlist" in formats:
        write_cutlist(project, specs,
                      os.path.join(dirs["dist"], project.name + "-cutlist.csv"))

    neutral = formats & {"step", "stl", "svg", "dxf", "drawing"}
    if neutral:
        doc = App.newDocument(project.name + "_export")
        compound = Part.makeCompound(placed_shapes(project, specs))
        obj = doc.addObject("Part::Feature", project.name)
        obj.Shape = compound
        doc.recompute()
        stem = os.path.join(dirs["dist"], project.name)
        if "step" in formats:
            fcutil.export_step([obj], stem + ".step")
        if "stl" in formats:
            fcutil.export_stl([obj], stem + ".stl")
        if "svg" in formats:
            fcutil.export_svg_edges(compound, stem + ".svg")
        if "dxf" in formats:
            fcutil.export_dxf(doc, [obj], stem + ".dxf")
        if "drawing" in formats:
            fcutil.make_drawing(doc, [obj],
                                os.path.join(dirs["drawings"], "assembly.dxf"),
                                ASM_VIEWS,
                                title={"part": "assembly", "project": project.name})
        App.closeDocument(doc.Name)

    if "fcstd" in formats:
        build_jointed_doc(project, values, data, dirs["parts"],
                          os.path.join(dirs["dist"], project.name + ".FCStd"))
    return data
