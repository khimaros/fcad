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
import sys

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
