"""build one .FCStd document (and exports) per distinct part type.

each part is a clean stock board in its natural frame. these files are the
reusable building blocks the assembly links to.
"""

import os

import FreeCAD as App
import Part
import Sketcher

from fcad import types
from fcad.freecad import partdesign, util as fcutil

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


def is_declared(spec):
    """whether a feature tree describes this part, so FreeCAD can build it.

    a project supplying its own `solid` keeps the plain `Part::Feature`, because
    a body fcad invented would not be the shape the project meant."""
    return bool(getattr(spec, "declared", False))


def _outline_sketch(doc, spec, pts, thickness):
    """the defining sketch for a part that hands over its own solid.

    a declared part's sketches come from its feature tree; this is for the other
    kind. it draws the outline - the stock the part is cut from - plus the
    circles the part *dimensions*, and nothing else.

    that circle list is the whole difference from the sketch fcad used to draw
    here. the old one carried whatever the project put in `holes`, validated by
    nothing, which is what made it a second and disagreeable description of the
    part. `dimension_circles` is the same list the drawing dimensions and is
    asserted actually bored by `find_undrilled`, so drawing it here is a checked
    description rather than an unchecked one."""
    return partdesign.add_sketch(
        doc, doc, spec.name + "_sketch", points=pts,
        circles=list(getattr(spec, "dimension_circles", ()) or ()),
        z=-thickness / 2.0)


def build_one(project, spec, values, dirs, formats):
    doc = App.newDocument(spec.name)
    fcutil.add_varset(doc, project, values)
    if is_declared(spec):
        # the part is its feature tree: the sketches in it are the definition,
        # not a drawing of one, and the circles the drawing dimensions are read
        # back out of them rather than declared a second time.
        obj = partdesign.build_body(doc, spec)
        sketches = partdesign.sketch_objects(doc)
        holes = partdesign.circles_of(
            doc, list(getattr(spec, "dimension_sketches", ()) or ()))
    else:
        # a part that hands over a solid has no tree to read, so it lists the
        # circles it wants dimensioned itself, and its defining outline is drawn
        # from the profile the project supplies.
        prof = project.profile(spec)
        sketches = ([_outline_sketch(doc, spec, prof[0], prof[1])]
                    if prof is not None else [])
        holes = list(getattr(spec, "dimension_circles", ()) or ())
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
    # the gui state is baked here for the same reason: only these objects are in
    # the file we just wrote.
    if "fcstd" in formats:
        doc.saveAs(stem + ".FCStd")
        fcutil.export_gui_state(doc, stem + ".FCStd")
    if "dxf" in formats:
        fcutil.export_dxf(doc, [obj], stem + ".dxf")
    if "drawing" in formats:
        fcutil.make_drawing(doc, [obj],
                            os.path.join(dirs["drawings"], spec.name + ".dxf"),
                            PART_VIEW, holes=holes,
                            title={"part": spec.name, "project": project.name})
    if "sketch" in formats and sketches:
        fcutil.export_sketch(doc, sketches,
                             os.path.join(dirs["sketches"], spec.name))
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
