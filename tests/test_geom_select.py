"""topology-safe geometry predicates (`fcad.geom_select`) and curved sketches.

projects writing `PartDesign` code used to filter `shape.Edges` by hand to avoid
fragile `EdgeN` indices, and drop to raw Sketcher for arcs or slots. this pins
both extensions end to end under FreeCAD:

- `fcad.geom_select` selects edges and faces by geometric predicate, composes
  predicates (`all_of`/`any_of`/`invert`), and fails loudly on zero/unexpected
  matches rather than handing `[]` to `Fillet` or `Chamfer`.
- `partdesign.add_sketch` accepts `slots` (stadium cutouts) and per-vertex
  corner radii `(x, y, r)` on polygons, emitting fully-constrained sketches
  with real `Part.ArcOfCircle` geometry.

needs FreeCAD: run with `freecadcmd tests/test_geom_select.py`.
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import FreeCAD as App
import Part

import fcad
from fcad import geom_select as gs, testing
from fcad.freecad import partdesign


def _raised(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def _rounded_rect_area(w, h, r):
    return w * h - (4.0 - math.pi) * r * r


def _slot_area(length, width):
    r = width / 2.0
    return length * width + math.pi * r * r


def main():
    c = testing.Checks()

    # 1. pure geometry predicates on a 40x20x10 box centered at origin.
    box = Part.makeBox(40.0, 20.0, 10.0, App.Vector(-20.0, -10.0, -5.0))
    top_edges = gs.edges(box, gs.at_z(5.0))
    c("at_z(5) finds all 4 top edges (%s)" % top_edges, len(top_edges) == 4)
    vert_edges = gs.edges(box, gs.along_axis("z"))
    c("along_axis('z') finds all 4 vertical edges (%s)" % vert_edges,
      len(vert_edges) == 4)
    long_top = gs.edges(box, gs.all_of(gs.at_z(5.0), gs.along_axis("x")))
    c("all_of(at_z(5), along_axis('x')) finds 2 long top edges (%s)" % long_top,
      len(long_top) == 2)
    not_vert = gs.edges(box, gs.invert(gs.along_axis("z")))
    c("invert(along_axis('z')) finds 8 horizontal edges (%d)" % len(not_vert),
      len(not_vert) == 8)
    top_face = gs.faces(box, gs.normal_dir("+z"), exact=1)
    c("normal_dir('+z') with exact=1 finds the single top face (%s)" % top_face,
      len(top_face) == 1)
    c("zero matches raise ValueError by default",
      _raised(lambda: gs.edges(box, gs.at_z(99.0))))
    c("exact mismatch raises ValueError",
      _raised(lambda: gs.edges(box, gs.at_z(5.0), exact=2)))
    c("min_count=0 allows empty result",
      gs.edges(box, gs.at_z(99.0), min_count=0) == [])

    # 2. curved edges vs straight edges on a cylinder.
    cyl = Part.makeCylinder(8.0, 20.0)
    curved = gs.edges(cyl, gs.curved())
    straight = gs.edges(cyl, gs.straight())
    c("curved() finds cylinder's 2 circular rims (%d)" % len(curved),
      len(curved) == 2)
    c("straight() finds cylinder's seam edge (%d)" % len(straight),
      len(straight) == 1)

    # 3. PartDesign body using rounded outline, stadium slot, and gs-picked chamfer.
    w, h, t, r = 80.0, 30.0, 10.0, 6.0
    slot_l, slot_w = 30.0, 10.0
    rounded = [(-w / 2, -h / 2, r), (w / 2, -h / 2, r),
               (w / 2, h / 2, r), (-w / 2, h / 2, r)]

    def build_link(doc, body):
        sk = partdesign.add_sketch(
            doc, body, "link_sk", points=rounded,
            slots=[(-slot_l / 2.0, 0.0, slot_l / 2.0, 0.0, slot_w)],
            z=-t / 2.0)
        pad = body.newObject("PartDesign::Pad", "link_pad")
        pad.Profile = sk
        pad.Length = t
        doc.recompute()
        bevel = body.newObject("PartDesign::Chamfer", "top_bevel")
        bevel.Base = (pad, gs.edges(pad.Shape, gs.at_z(t / 2.0)))
        bevel.Size = 0.8
        doc.recompute()

    doc = App.newDocument("tgs")
    try:
        spec = fcad.PartSpec("link", placements=[App.Placement()],
                             build=build_link)
        body = partdesign.build_body(doc, spec)
        sk = doc.getObject("link_sk")
        arcs = [g for g in sk.Geometry if isinstance(g, Part.ArcOfCircle)]
        c("rounded outline + slot emit 6 true arcs (%d)" % len(arcs),
          len(arcs) == 6)
        c("curved sketch is fully constrained", sk.FullyConstrained)
        pad = doc.getObject("link_pad")
        want_vol = (_rounded_rect_area(w, h, r) - _slot_area(slot_l, slot_w)) * t
        c("padded volume matches analytic rounded rect minus slot (%.2f vs %.2f)"
          % (pad.Shape.Volume, want_vol),
          testing.near(pad.Shape.Volume, want_vol, 0.05))
        c("chamfer using geom_select reduces volume cleanly (%.2f < %.2f)"
          % (body.Shape.Volume, pad.Shape.Volume),
          body.Shape.Volume < pad.Shape.Volume and len(body.Shape.Solids) == 1)
    finally:
        App.closeDocument(doc.Name)

    # 4. diagonal stadium slot at 35 deg is also fully constrained.
    doc2 = App.newDocument("tgs_diag")
    try:
        body2 = doc2.addObject("PartDesign::Body", "plate")
        sk2 = partdesign.add_sketch(
            doc2, body2, "diag_sk",
            points=[(-40.0, -30.0), (40.0, -30.0), (40.0, 30.0), (-40.0, 30.0)],
            slots=[(-15.0, -10.0, 15.0, 10.0, 8.0)], z=0.0)
        doc2.recompute()
        c("diagonal slot sketch is fully constrained", sk2.FullyConstrained)
    finally:
        App.closeDocument(doc2.Name)

    return c.report()


testing.main(main, __file__)
