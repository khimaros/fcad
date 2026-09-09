"""the PartDesign plumbing fcad owns, and two helpers it does not insist on.

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
  tedious), and `pad_and_bore` is the `profile2d` + `holes` shorthand's own
  implementation. call them, or write the Sketcher calls yourself, or crib from
  them - nothing routes through them.

`shape_of` reads the shape back from a real recompute rather than deriving it a
second way, because a second derivation is a second thing that can be wrong. it
costs about 17 ms a part against 3 ms for hand-rolled booleans, which `check`
pays once per part and the bom, cut list and `optimize` never pay at all (they
read metadata).
"""

import FreeCAD as App
import Part
import Sketcher

from fcad import types

V = App.Vector


def add_sketch(doc, owner, name, points=(), circles=(), z=0.0, placement=None):
    """a fully-constrained sketch: parallel to XY at `z`, or wherever `placement`
    puts it (an axial profile for a revolution wants XZ).

    `owner` is a `PartDesign::Body` for a declared part, or the document itself
    for the outline sketch of a part that hands over its own solid - the only
    difference is which call makes the object.

    every vertex is pinned with DistanceX/Y and every circle by its centre and a
    diameter, leaving zero degrees of freedom: `check` fails a part file whose
    sketches are loose, and a sketch nobody constrained is a drawing rather than
    a definition. a project wanting arcs, splines or constraints between elements
    writes Sketcher calls directly - this covers the common case, not the API."""
    make = owner.newObject if hasattr(owner, "newObject") else owner.addObject
    sketch = make(types.Sketcher.SketchObject, name)
    sketch.Placement = placement or App.Placement(V(0, 0, z), App.Rotation())
    n = len(points)
    for i in range(n):
        a = V(points[i][0], points[i][1], 0)
        b = V(points[(i + 1) % n][0], points[(i + 1) % n][1], 0)
        sketch.addGeometry(Part.LineSegment(a, b), False)
    for i in range(n):
        sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % n, 1))
    for i in range(n):
        sketch.addConstraint(Sketcher.Constraint("DistanceX", i, 1,
                                                 float(points[i][0])))
        sketch.addConstraint(Sketcher.Constraint("DistanceY", i, 1,
                                                 float(points[i][1])))
    for cx, cy, dia in circles:
        gi = sketch.addGeometry(Part.Circle(V(cx, cy, 0), V(0, 0, 1), dia / 2.0),
                                False)
        sketch.addConstraint(Sketcher.Constraint("DistanceX", gi, 3, float(cx)))
        sketch.addConstraint(Sketcher.Constraint("DistanceY", gi, 3, float(cy)))
        sketch.addConstraint(Sketcher.Constraint("Diameter", gi, float(dia)))
    sketch.Visibility = False
    return sketch


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


def subtractive(doc, body, tol=1e-6):
    """the features that take material away, found by taking them away.

    FreeCAD does not say. `AddSubType` is not exposed to python, and the class
    hierarchy does not discriminate - Pad, Pocket, Hole and Fillet all derive
    from `PartDesign::FeatureAddSub`. so ask the geometry instead: suppress a
    feature, recompute, and see which way the volume moved. that needs no
    vocabulary at all, which is the point - it is right about a Pocket, a Groove
    and a dressup Fillet without fcad knowing what any of them are, and stays
    right about features that do not exist yet.

    suppressing the base feature of a body changes nothing (there is no earlier
    shape to fall back to), which reads as "not subtractive" and is the answer
    we want."""
    full = body.Shape.Volume
    found = []
    for feature in features_of(doc):
        try:
            feature.Suppressed = True
            doc.recompute()
            if body.Shape.Volume > full + tol:
                found.append(feature)
        except Exception:
            pass
        finally:
            feature.Suppressed = False
            doc.recompute()
    return found


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


def shape_of(spec):
    """the shape of a declared part, built in a scratch document."""
    return _in_scratch(spec, lambda doc, body: body.Shape.copy())


def blank_of(spec, keep=()):
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
    check starts from this shape, so what it measures is the joinery alone."""
    def build(doc, body):
        cuts = [f for f in subtractive(doc, body)
                if profile_name(f) not in keep]
        if not cuts:
            return body.Shape.copy()
        for feature in cuts:
            feature.Suppressed = True
        doc.recompute()
        return body.Shape.copy()

    return _in_scratch(spec, build)


def dimensions_of(spec):
    """the circles a part wants dimensioned on its drawing, read off its sketches."""
    names = list(getattr(spec, "dimension_sketches", ()) or ())
    if not names:
        return []
    return _in_scratch(spec, lambda doc, body: circles_of(doc, names))
