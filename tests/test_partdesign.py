"""the sketch-derived part: a project declares an outline and its holes, and
fcad derives both the solid and a real PartDesign body from that declaration.

this is the well-lit path. everything else fcad builds keeps the sketch and the
solid as separate artifacts that only agree because `check` asserts they do -
`find_undrilled` for a hole declared and never bored, `find_sketch_drift` for an
outline the solid does not match. a part declared as sketch + holes cannot drift,
because there is only one description: the sketch is padded and bored to make the
solid, and the same declaration builds a `PartDesign::Body` whose `Pad` and
`Hole` features carry the intent a `Part.Shape` cannot (through or blind,
counterbored, threaded).

pinned here: the derived solid matches what the equivalent hand-built shape gives
(so converting a project changes no geometry), the body is a real feature tree
with fully-constrained sketches, and a part that supplies its own `solid` still
gets today's representation - turned and swept geometry needs it.

needs FreeCAD: run with `freecadcmd tests/test_partdesign.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App
import Part

import fcad
from fcad import testing
from fcad.freecad import build_parts, partdesign

V = App.Vector

AF, T, DIA, SIDES = 75.0, 30.0, 20.0, 6


def _outline(across_flats):
    r = across_flats / math.sqrt(3.0)
    step = 2.0 * math.pi / SIDES
    return [(r * math.cos(i * step), r * math.sin(i * step)) for i in range(SIDES)]


def _by_hand(points, thickness, hole_dia):
    """the shape the example builds today, as the thing to match."""
    face = Part.Face(Part.makePolygon(
        [V(x, y, -thickness / 2.0) for x, y in points + points[:1]]))
    bore = Part.makeCylinder(hole_dia / 2.0, thickness,
                             V(0, 0, -thickness / 2.0), V(0, 0, 1))
    return face.extrude(V(0, 0, thickness)).cut(bore)


class _Project:
    """a one-part project whose part is declared, not built."""

    name = "tpd"

    def __init__(self, spec):
        self.spec = spec

    def compute(self, values):
        return {"specs": [self.spec]}

    def from_spec(self, spec):
        return spec.solid()

    def profile(self, spec):
        return spec.profile2d

    def defaults(self):
        return {}


RING_OUT, RING_IN, RING_T = 20.0, 16.0, 2.0   # a gasket: more hole than part


def _square(side):
    h = side / 2.0
    return [(-h, -h), (h, -h), (h, h), (-h, h)]


def _pocketed_ring(doc, body, reverse=False):
    """a pad with a through pocket: the tree a gasket or keeper loop has."""
    partdesign.pad_and_bore(doc, body, _square(RING_OUT), RING_T, name="ring")
    sk = partdesign.add_sketch(doc, body, "ring_window", points=_square(RING_IN),
                               z=RING_T / 2.0)
    pocket = body.newObject("PartDesign::Pocket", "ring_cut")
    pocket.Profile = sk
    pocket.Length = RING_T
    pocket.Reversed = reverse
    doc.recompute()


def _looped_ring(doc, body):
    """the same ring from one sketch: an outline with an inner loop, padded."""
    sk = partdesign.add_sketch(doc, body, "ring_sketch", points=_square(RING_OUT),
                               loops=[_square(RING_IN)], z=-RING_T / 2.0)
    pad = body.newObject("PartDesign::Pad", "ring_pad")
    pad.Profile = sk
    pad.Length = RING_T
    doc.recompute()


FOOT, FOOT_GAP = 10.0, 20.0   # two square feet, well apart


def _feet(doc, body):
    """two islands in one sketch: a part that is two pieces on purpose."""
    foot = lambda x: [(x, 0.0), (x + FOOT, 0.0), (x + FOOT, FOOT), (x, FOOT)]
    sk = partdesign.add_sketch(doc, body, "feet_sketch", points=foot(0.0),
                               loops=[foot(FOOT + FOOT_GAP)])
    pad = body.newObject("PartDesign::Pad", "feet_pad")
    pad.Profile = sk
    pad.Length = RING_T
    doc.recompute()


def _disjoint_checks():
    """a part in several pieces is a defect unless the project says it is not."""
    from fcad.freecad import build_assembly
    meant = fcad.PartSpec("feet", placements=[App.Placement()], build=_feet,
                          disjoint=True)
    got = meant.solid()
    want = 2 * FOOT * FOOT * RING_T
    split = build_assembly.find_disjoint(_Project(meant), {})
    severed = build_assembly.find_disjoint(_Project(fcad.PartSpec(
        "feet", placements=[App.Placement()], build=_feet)), {})
    return [
        ("a body of two islands builds both (%d solids, %.0f vs %.0f mm^3)"
         % (len(got.Solids), got.Volume, want),
         len(got.Solids) == 2 and testing.near(got.Volume, want, 1e-6)),
        ("a part flagged `disjoint` is not reported as severed (%s)"
         % (split or "clean",), not split),
        ("the same part without the flag still is (%s)" % (severed,),
         severed == [("feet", 2)]),
    ]


STEP, BEVEL = 12.0, 0.5       # a boss on the block, and the break on its rim


def _bevelled_step(doc, body):
    """a block, a boss on it, and the boss's top rim broken: a watch bezel."""
    partdesign.pad_and_bore(doc, body, _square(RING_OUT), RING_T, name="base",
                            holes=[(0.0, 0.0, RING_T)])
    sk = partdesign.add_sketch(doc, body, "boss_sketch", points=_square(STEP),
                               z=RING_T / 2.0)
    boss = body.newObject("PartDesign::Pad", "boss")
    boss.Profile = sk
    boss.Length = RING_T
    doc.recompute()
    top = 1.5 * RING_T
    rim = ["Edge%d" % i for i, e in enumerate(boss.Shape.Edges, 1)
           if all(testing.near(v.Point.z, top) for v in e.Vertexes)]
    bevel = body.newObject("PartDesign::Chamfer", "boss_bevel")
    bevel.Base = (boss, rim)
    bevel.Size = BEVEL
    doc.recompute()


def _dressup_checks():
    """a dress-up names edges of an earlier feature, so taking any feature
    before it out of the tree to see what it did breaks the dress-up."""
    spec = fcad.PartSpec("stepped", placements=[App.Placement()],
                         build=_bevelled_step)
    names, said = testing.capture_stderr(lambda: _in_doc(
        _bevelled_step, lambda doc, body: [
            f.Name for f in partdesign.subtractive(doc)]))
    blank, blank_said = testing.capture_stderr(lambda: partdesign.blank_of(spec))
    want = RING_OUT ** 2 * RING_T + STEP ** 2 * RING_T
    return [
        ("the cuts of a part with a dress-up are found (%s)" % names,
         names == ["base_hole1", "boss_bevel"]),
        ("... without FreeCAD reporting errors nobody can act on (%r)" % said,
         "Invalid edge link" not in said and "missing element" not in said),
        ("its blank is the part before them (%.2f vs %.2f)" % (blank.Volume, want),
         testing.near(blank.Volume, want, 1e-6)),
        ("... also quietly (%r)" % blank_said, "<" not in blank_said),
    ]


LEGEND, LEGEND_SIZE, LEGEND_DEPTH = "FCAD 593", 3.0, 0.2


def _engraved(doc, body):
    """a plate with a legend cut into its top face: a watch's case back."""
    partdesign.pad_and_bore(doc, body, _square(RING_OUT), RING_T, name="back")
    text = partdesign.add_text(
        doc, body, "legend", LEGEND, LEGEND_SIZE,
        placement=App.Placement(V(-RING_OUT / 2.0 + 1.0, 0, RING_T / 2.0),
                                App.Rotation()))
    cut = body.newObject("PartDesign::Pocket", "legend_cut")
    cut.Profile = text
    cut.Length = LEGEND_DEPTH
    doc.recompute()


def _text_checks():
    """lettering is a profile like any other, so an engraved part stays a
    declared one: no dropping to `solid=` to put words on it."""
    from fcad.freecad import build_assembly
    spec = fcad.PartSpec("back", placements=[App.Placement()], build=_engraved,
                         openings=["legend"])
    part = spec.solid()
    removed = RING_OUT ** 2 * RING_T - part.Volume
    cuts = _in_doc(_engraved, lambda doc, body: [
        partdesign.profile_name(f) for f in partdesign.subtractive(doc)])
    return [
        ("a Pocket off add_text engraves the part (%.3f mm^3 gone)" % removed,
         removed > 0.1),
        ("... to the depth asked, not through it",
         removed < RING_OUT ** 2 * LEGEND_DEPTH),
        ("... leaving one solid (%d)" % len(part.Solids), len(part.Solids) == 1),
        ("the cut is named after its text, as one is after its sketch (%s)" % cuts,
         cuts == ["legend"]),
        ("so it can be declared an opening (%s)"
         % (build_assembly.find_stray_openings(_Project(spec), {}) or "clean"),
         not build_assembly.find_stray_openings(_Project(spec), {})),
    ]


def _rounded_then_bevelled(doc, body):
    """a block with its corners rounded, then the top of *that* broken: the
    second dress-up names edges of the first, as a watch case's does."""
    partdesign.pad_and_bore(doc, body, _square(RING_OUT), RING_T, name="case")
    pad = body.getObject("case_pad")
    upright = ["Edge%d" % i for i, e in enumerate(pad.Shape.Edges, 1)
               if testing.near(abs(e.Vertexes[0].Point.z
                                   - e.Vertexes[-1].Point.z), RING_T)]
    fillet = body.newObject("PartDesign::Fillet", "corners")
    fillet.Base = (pad, upright)
    fillet.Radius = 2.0
    doc.recompute()
    top = ["Edge%d" % i for i, e in enumerate(fillet.Shape.Edges, 1)
           if all(testing.near(v.Point.z, RING_T / 2.0) for v in e.Vertexes)]
    bevel = body.newObject("PartDesign::Chamfer", "top_bevel")
    bevel.Base = (fillet, top)
    bevel.Size = BEVEL
    doc.recompute()


def _stacked_dressup_checks():
    """a dress-up on a dress-up: its blank is still found, and found quietly.

    taking the fillet out of the tree leaves the chamfer naming edges that no
    longer exist, which FreeCAD reports forty lines at a time on every build."""
    spec = fcad.PartSpec("case", placements=[App.Placement()],
                         build=_rounded_then_bevelled)
    part = spec.solid()
    want = RING_OUT ** 2 * RING_T
    blank, said = testing.capture_stderr(lambda: partdesign.blank_of(spec))
    base, base_said = testing.capture_stderr(
        lambda: partdesign.blank_of(spec, dressed=True))
    return [
        ("the blank of a part with stacked dress-ups is its block (%.2f vs %.2f)"
         % (blank.Volume, want), testing.near(blank.Volume, want, 1e-3)),
        ("... as one solid (%d)" % len(blank.Solids), len(blank.Solids) == 1),
        ("... found without a word from FreeCAD (%r)" % said[:200], not said),
        ("its void baseline is the part itself (%.2f vs %.2f)"
         % (base.Volume, part.Volume),
         testing.near(base.Volume, part.Volume, 1e-3)),
        ("... also without a word (%r)" % base_said[:200], not base_said),
    ]


def _dressed_baseline_checks():
    """a dress-up shapes a part; it is not joinery, and no opening can name it.

    the void check measures what a part gave up against a baseline, and a bevel
    counted there put every chamfer worth looking at over the 1% budget with no
    way to declare it meant. the baseline keeps the dress-ups, so with the bore
    an opening this part has given up nothing."""
    spec = fcad.PartSpec("stepped", placements=[App.Placement()],
                         build=_bevelled_step, openings=["base_bore1"])
    part = spec.solid()
    base, said = testing.capture_stderr(lambda: partdesign.blank_of(
        spec, keep={"base_bore1"}, dressed=True))
    bare = partdesign.blank_of(spec, keep={"base_bore1"})
    return [
        ("a void baseline keeps the part's dress-ups (%.3f vs %.3f)"
         % (base.Volume, part.Volume), testing.near(base.Volume, part.Volume, 1e-3)),
        ("... quietly (%r)" % said, "<" not in said),
        ("the bevel is real material, so this means something (%.3f)"
         % (bare.Volume - part.Volume), bare.Volume - part.Volume > 0.01),
    ]


LOOPS = 40
# seconds a sketch of LOOPS islands may take. it took 35: every call re-solved
# the whole sketch. solved once it is well under one.
LOOPS_BUDGET = 5.0


def _many_loop_checks():
    """islands in one padded sketch are the cheap way to a disjoint part, so
    the sketch has to be cheap too."""
    import time
    loops = [[(i * 3.0, 0.0), (i * 3.0 + 2, 0.0), (i * 3.0 + 2, 2.0),
              (i * 3.0 + 1, 3.0), (i * 3.0, 2.0)] for i in range(LOOPS)]

    def build(doc, body):
        began = time.time()
        sk = partdesign.add_sketch(doc, body, "islands", loops=loops,
                                   circles=[(-5.0, 0.0, 2.0)])
        took = time.time() - began
        pad = body.newObject("PartDesign::Pad", "pad")
        pad.Profile = sk
        pad.Length = 1.0
        doc.recompute()
        return took, sk.FullyConstrained, len(pad.Shape.Solids)

    took, pinned, solids = _in_doc(lambda doc, body: None, build)
    return [
        ("a sketch of %d loops is quick (%.2fs)" % (LOOPS, took),
         took < LOOPS_BUDGET),
        ("... still fully constrained", pinned),
        ("... and pads to every island (%d)" % solids, solids == LOOPS + 1),
    ]


def _derived_doc_checks():
    """a document made *from* a part shares its directory, and is not a part."""
    root = tempfile.mkdtemp()
    try:
        for fn in ("post.FCStd", "post.fem.FCStd"):
            doc = App.newDocument("derived")
            doc.addObject("Part::Feature", "Solid")
            doc.saveAs(os.path.join(root, fn))
            App.closeDocument(doc.Name)
        seen = build_parts._in_part_files(root, lambda doc: doc.Objects)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return [("only a part's own document is checked as a part (%s)" % seen,
             seen == ["post.FCStd/Solid"])]


def _in_doc(build, fn):
    doc = App.newDocument("tring")
    try:
        body = doc.addObject("PartDesign::Body", "ring")
        build(doc, body)
        doc.recompute()
        return fn(doc, body)
    finally:
        App.closeDocument(doc.Name)


def _ring_checks():
    """a part that is mostly hole, the two ways to make one, and a cut that
    runs away from the material."""
    from fcad.freecad import build_assembly
    want = (RING_OUT ** 2 - RING_IN ** 2) * RING_T
    out = []

    # a part that is mostly hole: the pocket's own prism is bigger than the ring
    # it leaves, which once made the pad read as the feature taking material.
    names = _in_doc(_pocketed_ring, lambda doc, body: [
        f.Name for f in partdesign.subtractive(doc)])
    out.append(("only the pocket of a pocketed ring is subtractive (%s)" % names,
                names == ["ring_cut"]))
    spec = fcad.PartSpec("ring", placements=[App.Placement()],
                         build=_pocketed_ring, openings=["ring_window"])
    voids = build_assembly.find_voids(_Project(spec), {})
    out.append(("a ring whose window is an opening has no void (%s)"
                % (voids or "clean",), not voids))
    undeclared = fcad.PartSpec("ring", placements=[App.Placement()],
                               build=_pocketed_ring)
    voids = build_assembly.find_voids(_Project(undeclared), {})
    out.append(("the same window not declared one is caught (%s)"
                % [n for n, _, _ in voids], [n for n, _, _ in voids] == ["ring"]))

    volume, loose = _in_doc(_looped_ring, lambda doc, body: (
        body.Shape.Volume,
        [s.Name for s in partdesign.sketch_objects(doc) if not s.FullyConstrained]))
    out.append(("an outline with an inner loop pads to a ring (%.1f vs %.1f)"
                % (volume, want), testing.near(volume, want, 1e-6)))
    out.append(("... from a fully constrained sketch (%s)" % (loose or "all",),
                not loose))

    # a pocket run away from the material succeeds, reports up-to-date, and
    # removes nothing. nothing else notices.
    inert = lambda build: _in_doc(build, lambda doc, body: [
        f.Name for f in partdesign.inert_features(doc)])
    out.append(("a cut that removes material is not inert (%s)"
                % (inert(_pocketed_ring) or "none",), not inert(_pocketed_ring)))
    wrong_way = lambda doc, body: _pocketed_ring(doc, body, reverse=True)
    out.append(("a cut run the wrong way is reported inert (%s)" % inert(wrong_way),
                inert(wrong_way) == ["ring_cut"]))
    return out


def main():
    c = testing.Checks()
    points = _outline(AF)
    want = _by_hand(points, T, DIA)

    # a part with no `solid`: the outline and the declared bore are the whole
    # description, and fcad derives the geometry from them.
    hex_part = fcad.PartSpec(
        "hex", placements=[App.Placement()],
        build=lambda doc, body: partdesign.pad_and_bore(
            doc, body, points, T, [(0.0, 0.0, DIA)], name="hex"),
        dimension_sketches=["hex_bore1"], openings=["hex_bore1"])
    got = hex_part.solid()
    c("a declared part builds a solid at all", got is not None)
    if got is None:
        return c.report()
    c("... matching the hand-built shape (%.1f vs %.1f)" % (got.Volume, want.Volume),
      testing.near(got.Volume, want.Volume, 1e-6))
    c("... as one solid (%d)" % len(got.Solids), len(got.Solids) == 1)
    c("... occupying the same space (%.1f)" % got.common(want).Volume,
      testing.near(got.common(want).Volume, want.Volume, 1e-6))

    # the same declaration builds a real feature tree, not a shape in a bag.
    doc = App.newDocument("tpd_body")
    try:
        body = partdesign.build_body(doc, hex_part)
        types = [o.TypeId for o in doc.Objects]
        c("the part is a PartDesign::Body (%s)" % types,
          "PartDesign::Body" in types)
        c("... padded from a sketch", "PartDesign::Pad" in types)
        c("... and bored with a Hole feature, not a boolean",
          "PartDesign::Hole" in types)
        c("... with the body owning its features (no loose objects)",
          all(o.getParent() is not None or o.TypeId == "PartDesign::Body"
              for o in doc.Objects if o.TypeId.startswith("PartDesign::")))
        c("... whose shape is the declared one (%.1f)" % body.Shape.Volume,
          testing.near(body.Shape.Volume, want.Volume, 1e-6))
        loose = [o.Name for o in partdesign.sketch_objects(doc)
                 if not o.FullyConstrained]
        c("every sketch in the body is fully constrained (%s)" % (loose or "all",),
          not loose)
    finally:
        App.closeDocument(doc.Name)

    # a part that writes its own FreeCAD code gets the whole workbench, not a
    # vocabulary fcad curated. a Pocket appears nowhere in fcad's source.
    l, w, t = 80.0, 40.0, 20.0
    rect = [(-l / 2, -w / 2), (l / 2, -w / 2), (l / 2, w / 2), (-l / 2, w / 2)]
    groove = [(-10.0, -w / 2), (10.0, -w / 2), (10.0, w / 2), (-10.0, w / 2)]

    def slot(doc, body):
        partdesign.pad_and_bore(doc, body, rect, t, name="slotted")
        sk = partdesign.add_sketch(doc, body, "groove", points=groove, z=t / 2.0)
        pocket = body.newObject("PartDesign::Pocket", "groove_cut")
        pocket.Profile = sk
        pocket.Length = 5.0
        doc.recompute()

    slotted = fcad.PartSpec("slotted", placements=[App.Placement()], build=slot)
    got = slotted.solid()
    c("a Pocket fcad never names still builds (%.1f)" % got.Volume,
      testing.near(got.Volume, l * w * t - 20.0 * w * 5.0, 1e-6))

    # and the real reason to be thin: a fillet references an *edge* of an earlier
    # feature. no fcad-side feature description could express that, and a project
    # writing FreeCAD code needs nothing from fcad to do it.
    def filleted(doc, body):
        partdesign.pad_and_bore(doc, body, rect, t, name="fil")
        fillet = body.newObject("PartDesign::Fillet", "corner")
        pad = body.getObject("fil_pad")
        # picked by geometry, not by a hardcoded EdgeN: the same rule fcad uses
        # for FEM faces, and the reason a name would be the fragile part here.
        vertical = [i for i, e in enumerate(pad.Shape.Edges, 1)
                    if testing.near(abs(e.Vertexes[0].Point.z -
                                        e.Vertexes[-1].Point.z), t, 1e-6)]
        fillet.Base = (pad, ["Edge%d" % vertical[0]])
        fillet.Radius = 4.0
        doc.recompute()

    part = fcad.PartSpec("filleted", placements=[App.Placement()], build=filleted)
    shape = part.solid()
    c("a Fillet on an edge of an earlier feature builds (%.1f < %.1f)"
      % (shape.Volume, l * w * t),
      shape.Volume < l * w * t and shape.Volume > 0.9 * l * w * t)

    c.results.extend(_ring_checks())
    c.results.extend(_disjoint_checks())
    c.results.extend(_dressup_checks())
    c.results.extend(_dressed_baseline_checks())
    c.results.extend(_stacked_dressup_checks())
    c.results.extend(_text_checks())
    c.results.extend(_many_loop_checks())
    c.results.extend(_derived_doc_checks())

    # a part that hands over a solid keeps the plain representation: some
    # geometry is not a feature tree at all.
    turned = fcad.PartSpec("pin", placements=[App.Placement()],
                           solid=lambda: Part.makeCylinder(5.0, 40.0))
    c("a part with its own solid still builds it",
      testing.near(turned.solid().Volume, math.pi * 25.0 * 40.0, 1e-6))
    c("... and is not declared", not build_parts.is_declared(turned))
    c("a declared part is declared", build_parts.is_declared(hex_part))

    # the bom must not build geometry to measure a part: an optimize sweep asks
    # hundreds of candidates for their lengths and needs none of their solids.
    # with no explicit length the bom falls back to the built shape's extent.
    # a swept model should state `length=` instead: `optimize` asks hundreds of
    # candidates for their lengths and this fallback builds geometry for each.
    c("a declared part's length matches its shape (%.1f)" % hex_part.length,
      testing.near(hex_part.length, want.BoundBox.XLength, 1e-6))
    c("... and an explicit length is taken as given",
      fcad.PartSpec("x", placements=[], build=lambda d, b: None,
                    length=123.0).length == 123.0)

    return c.report()


testing.main(main, __file__)
