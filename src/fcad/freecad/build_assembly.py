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

# how far an `embeds` part is shifted to ask what is holding it, and how many of
# the six directions have to answer. a through bore captures four, a blind hole
# five, a part merely lying on a face one. the nudge has to clear the fit
# allowance a real hole is bored with (a millimetre of clearance on a pilot is
# usual) or a properly seated fastener reads as loose.
NUDGE = 1.5
MIN_CAPTURE = 3
# how far a part is dropped to ask what is under it.
DROP = 2.0
# a void is judged as a fraction of the part's own blank, because real
# clearances scale with the part and a mistake does not: the planter's grooved
# corner post carries 0.2% in deliberate fit allowance, while a corner relieved
# across the full width of members that only crossed over a small square carried
# 7%. the same absolute number could not separate those.
VOID_BUDGET = 0.01
# how much of its own defining outline a part may lose to joinery before the
# sketch stops describing it. drilled features run to a fraction of a percent.
SKETCH_DRIFT = 0.05
# tolerance for "is this point still solid": a bored hole leaves its centre in
# free space by at least its own radius, so this only has to beat rounding.
HOLE_TOL = 0.01

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


class Model:
    """every instance placed once, so the checks below share the boolean work.

    each of them is O(instances^2) in shape booleans, and a fastened assembly
    runs to a couple of hundred instances, so building the solids four times
    over is the difference between a check that is run and one that is not."""

    def __init__(self, project, values):
        self.project = project
        self.specs = project.compute(values)["specs"]
        self.structural, self.embedded = [], []
        self._blank = {}
        for spec in self.specs:
            base = project.from_spec(spec)
            into = self.embedded if getattr(spec, "embeds", False) \
                else self.structural
            for i, pl in enumerate(spec.placements):
                s = base.copy()
                s.Placement = pl
                into.append(("%s_%03d" % (spec.name, i + 1), spec, s))

    def blank(self, spec, placement):
        """the part before its drilled features, placed. cached per part."""
        if spec.name not in self._blank:
            self._blank[spec.name] = blank_shape(self.project, spec)
        s = self._blank[spec.name].copy()
        s.Placement = placement
        return s

    def hit(self, shape, skip=None, tol=1.0):
        """(name, volume) of the worst structural solid `shape` runs into."""
        worst, who = 0.0, None
        for name, _, other in self.structural:
            if name == skip or not shape.BoundBox.intersect(other.BoundBox):
                continue
            try:
                vol = shape.common(other).Volume
            except Exception:
                vol = 0.0
            if vol > worst:
                worst, who = vol, name
        return who, worst


def find_overlaps(project, values, tol=1.0, model=None):
    """structural solids whose volumes interpenetrate (touching faces give zero
    common volume, so only real interference is reported). parts flagged
    `embeds` (e.g. screws, which intentionally sink into the wood) are excluded.
    returns (name_a, name_b, mm^3)."""
    model = model or Model(project, values)
    solids = [(n, s) for n, _, s in model.structural]
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


def find_unseated(project, values, tol=1.0, nudge=NUDGE, model=None):
    """`embeds` parts that are not sitting in a cavity cut for them.

    `embeds` drops a part from `find_overlaps` precisely because it is meant to
    sink into other solids -- so the parts fcad stops checking are exactly the
    ones whose whole job is to fit a hole. this is the compensating check, and
    it asserts the two things the flag itself cannot:

    - **it must not plough through material.** a fastener seated in its bore
      displaces nothing; one turned at right angles to that bore, or driven into
      a slot that was never cut, interpenetrates the receiver just as surely as
      any interference -- and `embeds` is what stops anything noticing. this is
      the failure worth having the check for: it renders and exports perfectly.
    - **it must be held by something.** nudged along each axis it has to meet a
      structural solid from at least `MIN_CAPTURE` directions. a through bore
      captures four, a blind hole five, a part lying on a face one, and a part
      floating in mid-air none.

    the second is a heuristic and the first is not, so they are reported apart.
    returns (instance, reason, detail)."""
    model = model or Model(project, values)
    if not model.embedded or not model.structural:
        return []
    hit = model.hit

    bad = []
    for name, _, s in model.embedded:
        who, vol = hit(s)
        if vol > tol:
            bad.append((name, "interferes",
                        "%.1f mm^3 into %s" % (vol, who)))
            continue
        held = 0
        for axis in (V(nudge, 0, 0), V(-nudge, 0, 0), V(0, nudge, 0),
                     V(0, -nudge, 0), V(0, 0, nudge), V(0, 0, -nudge)):
            moved = s.copy()
            moved.Placement = App.Placement(s.Placement.Base + axis,
                                            s.Placement.Rotation)
            if hit(moved)[1] > tol:
                held += 1
        if held < MIN_CAPTURE:
            bad.append((name, "unseated",
                        "captured from %d of 6 directions" % held))
    return bad


def find_disjoint(project, values, model=None):
    """parts whose own joinery has cut them into more than one piece.

    a mortise from one direction meeting a housing from another inside the same
    post severs it, and the result is still a valid shape, still exports, still
    renders as two pieces sitting exactly where one used to be. returns
    (part, n_solids)."""
    model = model or Model(project, values)
    seen, bad = set(), []
    for _, spec, solid in model.structural + model.embedded:
        if spec.name in seen:
            continue
        seen.add(spec.name)
        n = len(solid.Solids)
        if n != 1:
            bad.append((spec.name, n))
    return bad


def find_voids(project, values, budget=VOID_BUDGET, model=None):
    """parts that give up material nothing else occupies.

    two members that cross need exactly one of them to give way, over exactly
    the volume they share; relieve more than that and the surplus is an empty
    pocket inside the assembly. it passes every other check -- a void is the
    opposite of an overlap -- and shows up in a render only as a shadow.

    a part is compared against its own `profile2d` blank, so only projects that
    declare one are examined; the tolerance is a fraction of that blank, because
    real clearances (a groove ploughed wide, a blind mortise cut deep) scale
    with the part while a mistake does not. returns (part, void_mm3, fraction)."""
    model = model or Model(project, values)
    seen, bad = set(), []
    for name, spec, solid in model.structural:
        if spec.name in seen:
            continue
        seen.add(spec.name)
        blank = model.blank(spec, solid.Placement)
        removed = blank.Volume - solid.Volume
        if removed <= 0:
            continue                      # nothing cut, or no distinct blank
        filled = 0.0
        for other_name, _, other in model.structural + model.embedded:
            if other_name == name or not blank.BoundBox.intersect(other.BoundBox):
                continue
            try:
                filled += blank.common(other).Volume
            except Exception:
                pass
        void = removed - filled
        frac = void / blank.Volume if blank.Volume else 0.0
        if frac > budget:
            bad.append((spec.name, void, frac))
    return bad


def find_unsupported(project, values, drop=DROP, tol=1.0, model=None):
    """parts that nothing holds up.

    every piece in an assembly is carried by something: it is `grounded`, it
    rests on another part, or a fastener passes through it. drop it and see. the
    failure this exists for is a housing cut to the full depth of the member it
    receives, which comes out as a slot open at the bottom -- the part fills it
    exactly, overlaps nothing, and rests on nothing.

    a part with an `embeds` part through it is taken as fastened and exempt,
    which is what keeps this meaningful for screwed models as well as for
    gravity-stacked ones. that test is against the part's *blank*: a correctly
    drilled part shares no volume at all with the fastener in its hole, so
    comparing against the drilled solid would find every properly made joint
    unfastened and every botched one fine.

    the check only applies to a model that says which parts stand on the ground.
    "everything is held up by something" is a claim about a physical assembly,
    and plenty of models are not one - a mechanism, an exploded layout, a pair of
    blocks in a test fixture. a project that flags `grounded` has told fcad it is
    modelling a stack; one that leaves it to the auto-ground default has not, and
    is left alone. returns (instance,)."""
    model = model or Model(project, values)
    if not any(getattr(s, "grounded", False) for s in model.specs):
        return []
    bad = []
    for name, spec, s in model.structural:
        if getattr(spec, "grounded", False):
            continue
        blank = model.blank(spec, s.Placement)
        if any(blank.BoundBox.intersect(e.BoundBox)
               and blank.common(e).Volume > tol
               for _, _, e in model.embedded):
            continue                      # fastened, so gravity is not the story
        moved = s.copy()
        moved.Placement = App.Placement(s.Placement.Base + V(0, 0, -drop),
                                        s.Placement.Rotation)
        if model.hit(moved, skip=name)[1] <= tol:
            bad.append(name)
    return bad


def find_undrilled(project, values, model=None):
    """parts declaring `holes` that are not bored in the solid.

    `holes` is what fcad dimensions on the drawing and draws on the sketch;
    cutting them is the project's job in `from_spec`. that split is easy to
    half-implement, and the result is a part carrying its drainage on paper and
    none in the wood -- which no other check can see, because a solid with one
    fewer hole is a perfectly good solid.

    a hole's centre lies in the part's own XY plane at mid-thickness, so testing
    whether that point is still inside the solid answers it exactly. returns
    (part, n_undrilled, n_declared)."""
    model = model or Model(project, values)
    seen, bad = set(), []
    for _, spec, solid in model.structural + model.embedded:
        holes = getattr(spec, "holes", ()) or ()
        if spec.name in seen or not holes:
            continue
        seen.add(spec.name)
        # the solid is placed; test in its own frame instead.
        base = project.from_spec(spec)
        missing = sum(1 for cx, cy, _ in holes
                      if base.isInside(App.Vector(cx, cy, 0.0), HOLE_TOL, True))
        if missing:
            bad.append((spec.name, missing, len(holes)))
    return bad


def find_sketch_drift(project, values, budget=SKETCH_DRIFT, model=None):
    """parts whose defining sketch is a poor description of the part.

    the sketch is what lands in `dist/sketches` and on the drawing, so a part
    built as a blank minus joinery on other planes -- a groove down one face, a
    housing across another, a lap taking half the thickness -- is documented by
    an outline it does not match. drilled features are expected to be missing
    and are small; structural ones are not. advisory, not a failure: what counts
    as "the defining outline" is the project's call. returns (part, fraction)."""
    model = model or Model(project, values)
    seen, out = set(), []
    for _, spec, solid in model.structural:
        if spec.name in seen or project.profile(spec) is None:
            continue
        seen.add(spec.name)
        blank = model.blank(spec, solid.Placement)
        if blank.Volume <= 0:
            continue
        frac = (blank.Volume - solid.Volume) / blank.Volume
        if frac > budget:
            out.append((spec.name, frac))
    return out


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
    # per-profile lines answer "how do I cut the 2x6"; nobody's shopping list is
    # one profile, so say what the whole model costs to buy.
    prices = {prof: cutlist.prices_for(project.stock, prof) for prof, _ in out}
    for line in cutlist.totals(dict(out), prices):
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
