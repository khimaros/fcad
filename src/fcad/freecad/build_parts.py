"""build one .FCStd document (and exports) per distinct part type.

each part is a clean stock board in its natural frame. these files are the
reusable building blocks the assembly links to.
"""

import os

import FreeCAD as App
import Part
import Sketcher

from fcad.freecad import util as fcutil

V = App.Vector
# a four-cell third-angle sheet for a board lying flat: top (down +Z, length x
# width) above the front (along -Y, adds thickness) so they share a vertical
# projection line; the right side view beside the front; and an isometric
# pictorial in the free upper-right cell. (label, direction, xdir, col, row).
PART_VIEW = [
    ("top",   V(0, 0, 1),  V(1, 0, 0),  0, 0),
    ("iso",   V(1, -1, 1), V(1, 1, 0),  1, 0),
    ("front", V(0, -1, 0), V(1, 0, 0),  0, 1),
    ("right", V(1, 0, 0),  V(0, 1, 0),  1, 1),
]


def _defining_sketch(doc, name, pts, thickness, holes=()):
    """a fully-constrained profile sketch (outline + in-plane hole circles).

    the outline is closed with coincidences and every vertex pinned with
    DistanceX/Y; each in-plane hole is a circle with its center pinned and a
    diameter constraint. that leaves zero degrees of freedom, so the sketch is
    fully constrained. it documents the part's profile and through-thickness
    holes; the solid itself is built by project.from_spec (which also cuts the
    holes on other faces that a single sketch cannot show)."""
    sketch = doc.addObject("Sketcher::SketchObject", name + "_sketch")
    sketch.Placement = App.Placement(V(0, 0, -thickness / 2.0), App.Rotation())
    n = len(pts)
    for i in range(n):
        a = V(pts[i][0], pts[i][1], 0)
        b = V(pts[(i + 1) % n][0], pts[(i + 1) % n][1], 0)
        sketch.addGeometry(Part.LineSegment(a, b), False)
    for i in range(n):
        sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % n, 1))
    for i in range(n):
        sketch.addConstraint(Sketcher.Constraint("DistanceX", i, 1, float(pts[i][0])))
        sketch.addConstraint(Sketcher.Constraint("DistanceY", i, 1, float(pts[i][1])))
    for cx, cy, dia in holes:
        gi = sketch.addGeometry(Part.Circle(V(cx, cy, 0), V(0, 0, 1), dia / 2.0), False)
        sketch.addConstraint(Sketcher.Constraint("DistanceX", gi, 3, float(cx)))
        sketch.addConstraint(Sketcher.Constraint("DistanceY", gi, 3, float(cy)))
        sketch.addConstraint(Sketcher.Constraint("Diameter", gi, float(dia)))
    sketch.Visibility = False
    return sketch


def build_one(project, spec, values, dirs, formats):
    doc = App.newDocument(spec.name)
    fcutil.add_varset(doc, project, values)
    prof = project.profile(spec)
    sketch = None
    if prof is not None:
        # the sketch shows every in-plane circle (drainage + fastener); only the
        # drainage holes are dimensioned on the drawing below.
        all_holes = list(spec.holes) + list(getattr(spec, "fastener_holes", ()))
        sketch = _defining_sketch(doc, spec.name, prof[0], prof[1], all_holes)
    obj = doc.addObject("Part::Feature", spec.name)
    obj.Shape = project.from_spec(spec)
    obj.Visibility = True
    doc.recompute()

    stem = os.path.join(dirs["parts"], spec.name)
    if "step" in formats:
        fcutil.export_step([obj], stem + ".step")
    if "stl" in formats:
        fcutil.export_stl([obj], stem + ".stl")
    if "svg" in formats:
        fcutil.export_svg_edges(obj.Shape, stem + ".svg")
    # save the part file before the drawing exports: dxf/drawing/sketch all add
    # TechDraw pages to the document, and we want the saved .FCStd to contain
    # only the part (varset + sketch + solid) so it opens straight to the model.
    if "fcstd" in formats:
        doc.saveAs(stem + ".FCStd")
    if "dxf" in formats:
        fcutil.export_dxf(doc, [obj], stem + ".dxf")
    if "drawing" in formats:
        fcutil.make_drawing(doc, [obj],
                            os.path.join(dirs["drawings"], spec.name + ".dxf"),
                            PART_VIEW, holes=spec.holes,
                            title={"part": spec.name, "project": project.name})
    if "sketch" in formats and sketch is not None:
        fcutil.export_sketch(doc, sketch, os.path.join(dirs["sketches"], spec.name))
    App.closeDocument(doc.Name)


def build(project, values, dirs, formats):
    data = project.compute(values)
    for spec in data["specs"]:
        build_one(project, spec, values, dirs, formats)
    return data


def find_loose_sketches(parts_dir):
    """defining sketches in the built part files that are not fully constrained
    (have leftover degrees of freedom). returns '<file>/<sketch>' names."""
    loose = []
    for fn in sorted(os.listdir(parts_dir)):
        if not fn.endswith(".FCStd"):
            continue
        doc = App.openDocument(os.path.join(parts_dir, fn))
        try:
            for o in doc.Objects:
                if (o.TypeId == "Sketcher::SketchObject"
                        and not getattr(o, "FullyConstrained", False)):
                    loose.append("%s/%s" % (fn, o.Name))
        finally:
            App.closeDocument(doc.Name)
    return loose
