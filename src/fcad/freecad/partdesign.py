"""the PartDesign plumbing fcad owns, and the helpers it does not insist on.

fcad models nothing here. a part's `build(doc, body)` is called with a live
document and an empty body and makes real FreeCAD calls, so everything FreeCAD
can do is available by construction - arcs, attachment, fillets, patterns, the
features that do not exist yet. an fcad-side feature vocabulary would only ever
be a smaller, lossier copy of the one FreeCAD already has.

two different things live here, and it is worth knowing which is which:

- **plumbing, which fcad has to own.** `build_body` creates the body and invokes
  the project's code; `shape_of` gets a shape out of a part without a document
  already being open, which the checks, the exports and the FEM all need. that
  scratch-document dance is fcad's problem, not a project's.
- **helpers, which a project may ignore.** `add_sketch` constrains a sketch
  fully (which `check` requires, so fcad owes projects a way that is not
  tedious), `add_text` is lettering as a profile, and `pad_and_bore` is the
  `profile2d` + `holes` shorthand's own implementation. call them, or write the Sketcher calls yourself, or crib from
  them - nothing routes through them.

`shape_of` reads the shape back from a real recompute rather than deriving it a
second way, because a second derivation is a second thing that can be wrong. it
costs about 17 ms a part against 3 ms for hand-rolled booleans, which `check`
pays once per part and the bom, cut list and `optimize` never pay at all (they
read metadata).
"""

import os

import FreeCAD as App
import Part
import Sketcher

from fcad import types

V = App.Vector
# the base class of every feature that produces a solid (Pad, Pocket, Hole,
# Fillet, ...), as against the datums and binders that share the namespace.
FEATURE_TYPE = "PartDesign::Feature"
# the base class of the features that only shape edges and faces of the solid
# before them: Fillet, Chamfer, Draft, Thickness.
DRESSUP_TYPE = "PartDesign::DressUp"
# the observer freecadcmd reports through, and what it reports that a scratch
# recompute can say nothing useful with.
TEXT_TYPE = "Part::Part2DObjectPython"
# a font that is wherever FreeCAD is: TechDraw's own drawing face.
FONT = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources",
                    "fonts", "osifont-lgpl3fe.ttf")
CONSOLE = "Console"
CONSOLE_KINDS = ("Wrn", "Err")
# a feature changing its part's volume by less than this fraction did nothing.
INERT_TOL = 1e-9


def _loop(points, first):
    """(segments, constraints) of one closed polygon, every vertex pinned in x
    and y. `first` is the sketch index its first segment will have."""
    n = len(points)
    segments = [Part.LineSegment(V(points[i][0], points[i][1], 0),
                                 V(points[(i + 1) % n][0], points[(i + 1) % n][1], 0))
                for i in range(n)]
    pins = [Sketcher.Constraint("Coincident", first + i, 2,
                                first + (i + 1) % n, 1) for i in range(n)]
    for i in range(n):
        pins.append(Sketcher.Constraint("DistanceX", first + i, 1,
                                        float(points[i][0])))
        pins.append(Sketcher.Constraint("DistanceY", first + i, 1,
                                        float(points[i][1])))
    return segments, pins


def add_sketch(doc, owner, name, points=(), circles=(), z=0.0, placement=None,
               loops=()):
    """a fully-constrained sketch: parallel to XY at `z`, or wherever `placement`
    puts it (an axial profile for a revolution wants XZ). `placement` wins when
    both are given.

    `points` is the outline, `loops` further closed polygons and `circles`
    `(cx, cy, dia)` triples, all in the sketch's own xy. they combine: a loop or
    circle inside the outline is a hole in the face a Pad extrudes, so a window,
    a ring or a frame is one sketch and one feature, with no Pocket to aim.

    `owner` is a `PartDesign::Body` for a declared part, or the document itself
    for the outline sketch of a part that hands over its own solid - the only
    difference is which call makes the object.

    every vertex is pinned with DistanceX/Y and every circle by its centre and a
    diameter, leaving zero degrees of freedom: `check` fails a part file whose
    sketches are loose, and a sketch nobody constrained is a drawing rather than
    a definition. a project wanting arcs, splines or constraints between elements
    writes Sketcher calls directly - this covers the common case, not the API.

    the geometry and the constraints each go in as one list. a sketch re-solves
    on every `addConstraint`, so adding them singly is quadratic in the sketch:
    forty islands took 35 seconds that way and take a fraction of one this way."""
    make = owner.newObject if hasattr(owner, "newObject") else owner.addObject
    sketch = make(types.Sketcher.SketchObject, name)
    sketch.Placement = placement or App.Placement(V(0, 0, z), App.Rotation())
    geometry, pins = [], []
    for loop in ([points] if len(points) else []) + list(loops):
        segments, held = _loop(loop, len(geometry))
        geometry += segments
        pins += held
    for cx, cy, dia in circles:
        gi = len(geometry)
        geometry.append(Part.Circle(V(cx, cy, 0), V(0, 0, 1), dia / 2.0))
        pins += [Sketcher.Constraint("DistanceX", gi, 3, float(cx)),
                 Sketcher.Constraint("DistanceY", gi, 3, float(cy)),
                 Sketcher.Constraint("Diameter", gi, float(dia))]
    if geometry:
        sketch.addGeometry(geometry, False)
        sketch.addConstraint(pins)
    sketch.Visibility = False
    return sketch


def add_text(doc, body, name, text, size, font=None, placement=None):
    """lettering as a profile: a Pocket off it engraves, a Pad raises it.

    a Draft ShapeString in the body, lying in XY from the origin with the
    baseline along x, or wherever `placement` puts it. `size` is the height of
    the letters in mm and `font` a .ttf path, defaulting to the single-stroke-
    weight engineering face FreeCAD ships, so a build does not depend on what
    the machine has installed. the cut it makes is named after `name`, the way
    one off a sketch is, which is what `openings` wants.

    it is made here rather than with `Draft.make_shapestring`, which writes
    into the *active* document: fcad builds a part in scratch documents that
    are not the active one."""
    from draftobjects.shapestring import ShapeString
    obj = body.newObject(TEXT_TYPE, name)
    ShapeString(obj)
    obj.String, obj.FontFile, obj.Size = text, font or FONT, size
    obj.Placement = placement or App.Placement()
    obj.Visibility = False
    doc.recompute()
    return obj


def pad_and_bore(doc, body, points, thickness, holes=(), name="part"):
    """the shape most parts are: an outline padded, with through bores.

    the bores are `PartDesign::Hole` features rather than pockets or booleans,
    because a Hole is the only one that records that it *is* a hole - through or
    blind, counterbored, threaded to a standard - which is the intent a finished
    shape throws away.

    two orderings here are not stylistic. a feature's properties can only be set
    once it has a base ("No base set, no sketch support either"), and its base
    must be recomputed before it is added ("Base feature's TopoShape is
    invalid"). and the bores are `Reversed` because their sketch sits on the -Z
    face: unreversed the cut runs away from the material, succeeds, reports
    up-to-date, and removes nothing at all."""
    outline = add_sketch(doc, body, name + "_sketch", points=points,
                         z=-thickness / 2.0)
    pad = body.newObject(types.PartDesign.Pad, name + "_pad")
    pad.Profile = outline
    pad.Length = thickness
    doc.recompute()
    for i, (cx, cy, dia) in enumerate(holes):
        sk = add_sketch(doc, body, "%s_bore%d" % (name, i + 1),
                        circles=[(cx, cy, dia)], z=-thickness / 2.0)
        hole = body.newObject(types.PartDesign.Hole, "%s_hole%d" % (name, i + 1))
        hole.Profile = sk
        hole.Diameter = dia
        hole.DepthType = "ThroughAll"
        hole.Threaded = False
        hole.Reversed = True
        doc.recompute()
    return body


def build_body(doc, spec, name=None):
    """the part as a `PartDesign::Body` in `doc`, built by the project's own code."""
    body = doc.addObject(types.PartDesign.Body, name or spec.name)
    spec.build_into(doc, body)
    doc.recompute()
    return body


def sketch_objects(doc):
    """the sketches in a document, which for a built part are its definition."""
    return [o for o in doc.Objects if o.TypeId == types.Sketcher.SketchObject]


def features_of(doc):
    """the body's own features, in tree order (its origin planes are not ones)."""
    return [o for o in doc.Objects if o.TypeId.startswith("PartDesign::")
            and o.TypeId != types.PartDesign.Body]


def solid_features(doc):
    """the features that each leave a solid behind, in tree order.

    datum planes and shape binders live in a body too and are `PartDesign::`
    types, but they shape nothing."""
    return [o for o in features_of(doc) if o.isDerivedFrom(FEATURE_TYPE)]


def inert_features(doc, tol=INERT_TOL):
    """features that leave their part exactly as they found it.

    a pocket whose sketch sits on the far face and is not `Reversed` runs away
    from the material: it succeeds, reports up-to-date, and removes nothing.
    FreeCAD says nothing, the part still builds, and the hole is simply not
    there. each feature's `Shape` is the body as it stood after that step, so a
    feature whose shape matches the one before it did no work - which needs no
    knowledge of what kind of feature it was."""
    found, before = [], None
    for feature in solid_features(doc):
        after = feature.Shape.Volume
        if before is not None and abs(after - before) <= tol * max(before, 1.0):
            found.append(feature)
        before = after
    return found


def circles_of(doc, names=()):
    """(cx, cy, dia) for every circle in the named sketches, in part coordinates.

    the drawing dimensions what a part declares, and a part built from a feature
    tree has already said it: the bore is a circle in a sketch. reading them here
    means the dimensions cannot disagree with the geometry, because they are the
    same object - which is what the old `holes` list could not promise."""
    out = []
    for sketch in sketch_objects(doc):
        if names and sketch.Name not in names and sketch.Label not in names:
            continue
        for geom in sketch.Geometry:
            if not isinstance(geom, Part.Circle):
                continue
            centre = sketch.Placement.multVec(geom.Center)
            out.append((centre.x, centre.y, 2.0 * geom.Radius))
    return out


def profile_name(feature):
    """the name of the sketch a feature was built from, or None.

    `Profile` is an `App::PropertyLinkSub`, so it reads back as
    `(object, [subelements])` rather than the object that was assigned to it -
    asking the tuple for a `.Name` silently gets None, and every comparison
    against it silently fails."""
    prof = getattr(feature, "Profile", None)
    if isinstance(prof, (tuple, list)):
        prof = prof[0] if prof else None
    return getattr(prof, "Name", None)


def subtractive(doc, tol=1e-6):
    """the features that take material away: each one that left less than it found.

    FreeCAD does not say. `AddSubType` is not exposed to python, and the class
    hierarchy does not discriminate - Pad, Pocket, Hole and Fillet all derive
    from `PartDesign::FeatureAddSub`. so ask the geometry instead: a feature's
    `Shape` is the body as it stood after that step, and the volume either went
    down or it did not. that needs no vocabulary at all, which is the point - it
    is right about a Pocket, a Groove and a dressup Fillet without fcad knowing
    what any of them are, and stays right about features that do not exist yet.

    nothing is taken out of the tree to find out. suppressing a feature
    renumbers the edges of everything after it, so a chamfer further down lost
    the edges it names and FreeCAD reported an invalid link for every one of
    them, on every build, about a document nobody will ever open. the base
    feature has nothing before it to compare with, and is never a cut."""
    steps = solid_features(doc)
    return [after for before, after in zip(steps, steps[1:])
            if after.Shape.Volume < before.Shape.Volume - tol]


def _in_scratch(spec, fn):
    """run `fn(doc, body)` over a freshly built body in a throwaway document.

    hidden, closed again, and the active document restored by name: this runs
    inside builds that have a document of their own open, and silently changing
    which one is active would be a fine way to write a part into the wrong
    file."""
    prior = App.ActiveDocument.Name if App.ActiveDocument else None
    doc = App.newDocument(spec.name + "_scratch", hidden=True)
    try:
        return fn(doc, build_body(doc, spec))
    finally:
        App.closeDocument(doc.Name)
        if prior and prior in App.listDocuments():
            App.setActiveDocument(prior)


def _solid(shape):
    """a body's shape as the solid it is, when it is exactly one.

    some features (a Hole, for one) leave the body's shape as a compound around
    its single solid, and a compound answers `Volume` and `BoundBox` but has no
    `CenterOfMass` or principal axes. which kind came back depended on the last
    feature in the tree, so a test that worked on a turned pin failed on a
    drilled plate. a part severed into several solids is left as the compound,
    which is what the disjoint check counts."""
    solids = shape.Solids
    return solids[0].copy() if len(solids) == 1 else shape.copy()


def shape_of(spec):
    """the shape of a declared part, built in a scratch document."""
    return _in_scratch(spec, lambda doc, body: _solid(body.Shape))


def _recompute_quietly(doc):
    """recompute a scratch document whose cuts were just suppressed, unheard.

    a dress-up names edges, and a suppressed feature before it takes them
    away: a chamfer on a fillet loses every one, and FreeCAD says so forty
    lines at a time, on every build, about a document nobody will ever open.
    the dress-up is suppressed too, so its broken link changes nothing. the
    tree was already built once unsuppressed, so a real error has been heard."""
    console = App.Console
    heard = {kind: console.GetStatus(CONSOLE, kind) for kind in CONSOLE_KINDS}
    for kind in heard:
        console.SetStatus(CONSOLE, kind, False)
    try:
        doc.recompute()
    finally:
        for kind, on in heard.items():
            console.SetStatus(CONSOLE, kind, on)


def blank_of(spec, keep=(), dressed=False):
    """the part before its joinery: every subtractive feature suppressed.

    this is what "the blank" means once a part is a feature tree, and it is the
    general form of what `profile2d` used to approximate. the outline-and-
    thickness version could only ever express a hole; a groove down one face, a
    housing across another or a lap taking half the thickness came out as
    material the part had lost and nothing had accounted for. now the part says
    which cuts are joinery by having them as features, so the FEM gets its
    unmeshably-undrilled solid.

    `keep` names sketches whose cuts are *openings* rather than joinery and are
    left in place. that distinction is not in the geometry: a nut's bore and a
    lap relieved twice as wide as it should be are both a feature that removed
    material, and only the project knows which is meant to stay empty. the void
    check starts from this shape, so what it measures is the joinery alone.

    `dressed` keeps the dress-ups too, for the same check: a chamfer or a fillet
    shapes a part and is never joinery, and here the type does say so. it cannot
    simply stay in the tree, because it names edges that suppressing an earlier
    cut renumbers; what it took is cut back out of the blank instead."""
    def build(doc, body):
        steps = solid_features(doc)
        cuts = [f for f in subtractive(doc)
                if profile_name(f) not in keep]
        trims = [before.Shape.cut(after.Shape)
                 for before, after in zip(steps, steps[1:])
                 if dressed and after in cuts
                 and after.isDerivedFrom(DRESSUP_TYPE)]
        for feature in cuts:
            feature.Suppressed = True
        if cuts:
            _recompute_quietly(doc)
        blank = body.Shape
        for trim in trims:
            blank = blank.cut(trim)
        return _solid(blank)

    return _in_scratch(spec, build)


def stray_openings(spec):
    """the openings a part declares that are not the sketch of any of its cuts."""
    def build(doc, body):
        cuts = {profile_name(f) for f in subtractive(doc)}
        return [name for name in spec.openings if name not in cuts]

    return _in_scratch(spec, build)


def dimensions_of(spec):
    """the circles a part wants dimensioned on its drawing, read off its sketches."""
    names = list(getattr(spec, "dimension_sketches", ()) or ())
    if not names:
        return []
    return _in_scratch(spec, lambda doc, body: circles_of(doc, names))
