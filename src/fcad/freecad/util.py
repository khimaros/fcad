"""shared FreeCAD helpers: VarSet parameter panel and the export formats.

centralizes the headless gotchas we confirmed on FreeCAD 1.1.1:
- DXF shape export needs the legacy exporter preference enabled.
- TechDraw pages must export via TechDraw.writeDXFPage (importDXF.exportPage
  raises NameError headless).
"""

import json
import math
import os
import zipfile

import FreeCAD as App
import Part
import Mesh

from fcad.config import parts_path, placements_path  # noqa: F401  (re-exported)

# our own A4 landscape template: a border plus a fillable title block. the
# bundled default is blank (no border, no block), so we ship one as package data.
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "resources", "templates", "fcad_A4_landscape.svg")


def add_varset(doc, project, values):
    """add the parameter panel (VarSet) to a document and set its values.

    enumeration fields are populated from the project's enum_choices (e.g. the
    lumber names), so the panel offers a dropdown like openscad's customizer."""
    vs = doc.addObject("App::VarSet", "Parameters")
    for name, ptype, group, lo, hi in project.schema:
        vs.addProperty(ptype, name, group, "")
        if ptype == "App::PropertyEnumeration":
            setattr(vs, name, list(project.enum_choices.get(name, [])))
        setattr(vs, name, values[name])
    return vs


def _feature(doc, shape, name="Shape"):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    return obj


def export_step(objs, path):
    Part.export(objs, path)


def export_stl(objs, path):
    Mesh.export(objs, path)


def export_svg(objs, path):
    import importSVG
    importSVG.export(objs, path)


# what a viewer should show: the solids of a part file, and the links plus the
# container of an assembly. sketches, varsets, origins and joints stay hidden -
# they are inputs and decoration, not the model.
# what a viewer should show when the file opens. `PartDesign::Body` is the one
# you see for a declared part - its own features carry shapes too, and showing
# those as well would draw the part once per feature.
VISIBLE_TYPES = ("App::Link", "Assembly::AssemblyObject", "Part::Extrusion",
                 "Part::Feature", "Part::FeaturePython", "PartDesign::Body")

# freecad's isometric orientation (rotation axis then angle); it describes a
# direction, so it is the same for every model whatever its size.
ISO_ORIENTATION = "0.74290609 0.30772209 0.59447283  1.2171158"
ISO_DIR = (1.0, -1.0, 1.0)  # the camera sits along this ray from the centre

_GUI_DOC = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1">
    <ViewProviderData Count="%d">
%s    </ViewProviderData>
%s</Document>
"""
_GUI_VIEWPROVIDER = """        <ViewProvider name="%s">
            <Properties Count="1" TransientCount="0">
                <Property name="Visibility" type="App::PropertyBool" status="1">
                    <Bool value="%s"/>
                </Property>
            </Properties>
        </ViewProvider>
"""
# coin serializes the camera as an inventor node in one attribute, newlines and
# all; freecad writes it exactly this way.
_GUI_CAMERA = (
    '    <Camera settings="OrthographicCamera {&#10;'
    '  viewportMapping ADJUST_CAMERA&#10;'
    '  position %.6f %.6f %.6f&#10;'
    '  orientation %s&#10;'
    '  nearDistance %.6f&#10;'
    '  farDistance %.6f&#10;'
    '  aspectRatio 1&#10;'
    '  focalDistance %.6f&#10;'
    '  height %.6f&#10;&#10;}&#10;"/>\n')


def _visible_bbox(doc):
    """bounding box of everything a viewer will show, or None."""
    bb = None
    for o in doc.Objects:
        if o.TypeId not in VISIBLE_TYPES:
            continue
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull():
            continue
        bb = shape.BoundBox if bb is None else bb.united(shape.BoundBox)
    return bb


def _camera(bb):
    """an isometric fit-all orthographic camera for a bounding box.

    matched against what the gui's own viewIsometric + ViewFit produces: the
    ortho height is exactly the box's diagonal, the camera sits half a diagonal
    from the centre along (1,-1,1), and focalDistance is that same half diagonal.
    the diagonal is the largest extent the model can project to at any
    orientation, so it fits whatever the view angle; near/far are opened a
    further diagonal either way so nothing clips."""
    d = bb.DiagonalLength if bb is not None else 0.0
    if d <= 0:
        return ""
    off = d / (2.0 * math.sqrt(3.0))
    c, focal = bb.Center, d / 2.0
    return _GUI_CAMERA % (c.x + ISO_DIR[0] * off, c.y + ISO_DIR[1] * off,
                          c.z + ISO_DIR[2] * off, ISO_ORIENTATION,
                          focal - d, focal + d, focal, d)


def export_gui_state(doc, path):
    """bake view state into a saved .FCStd so it opens showing the fitted model.

    visibility and camera are gui state and live in the zip's GuiDocument.xml,
    which freecadcmd cannot write because it has no ViewObject - so a headless
    build's artifact opens with every object switched off and the 3d view looks
    empty (for an assembly the only things left on are the joints, which draw
    nothing). the file is plain xml and freecad restores a partial one happily,
    defaulting whatever we leave out, so the Visibility flags plus a camera are
    enough and the build stays headless. call it right after saving, before
    adding anything the saved file does not contain."""
    entries = "".join(
        _GUI_VIEWPROVIDER % (o.Name, "true" if o.TypeId in VISIBLE_TYPES else "false")
        for o in doc.Objects)
    xml = _GUI_DOC % (len(doc.Objects), entries, _camera(_visible_bbox(doc)))
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("GuiDocument.xml", xml)


def export_placements(placements, path):
    """write {part name: [Placement]} as json.

    the 3d diff pairs instances across two revisions of a design, so it needs
    both revisions' placements; it cannot get them by calling `compute` twice
    because the two answers come from two different revisions of the project's
    own code. the builder already knows them, so it records them beside the
    other neutral exports and the diff reads both sides back."""
    data = {name: [[[p.Base.x, p.Base.y, p.Base.z], list(p.Rotation.Q)] for p in pls]
            for name, pls in placements.items()}
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def export_parts(specs, path):
    """write {part name: {grounded, embeds}} as json.

    the two flags a renderer cannot recover from geometry: which part anchors the
    model, and which parts are fasteners rather than structure. the assembly
    animator needs both to arrive in a sensible order - build outward from the
    anchor, drive the screws last - and it runs under plain python with no access
    to the project that declared them."""
    data = {spec.name: {"grounded": bool(getattr(spec, "grounded", False)),
                        "embeds": bool(getattr(spec, "embeds", False))}
            for spec in specs}
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def read_placements(path):
    with open(path) as f:
        data = json.load(f)
    return {name: [App.Placement(App.Vector(*base), App.Rotation(*quat))
                   for base, quat in pls]
            for name, pls in data.items()}


def export_svg_edges(shape, path, direction=App.Vector(0, 0, 1)):
    """write a flat svg of a shape's visible silhouette as polylines.

    draft's solid-to-svg path chokes on drilled solids (it cannot invert some
    projected face wires), so for the whole assembly we instead take techdraw's
    hidden-line projection (visible edges only) and emit the discretized edges
    directly. this is headless-safe and depends on no face inversion."""
    import TechDraw
    visible = TechDraw.project(shape, direction)[0]
    polylines, lo, hi = [], [1e18, 1e18], [-1e18, -1e18]
    for edge in visible.Edges:
        pts = [(p.x, p.y) for p in edge.discretize(24)]
        polylines.append(pts)
        for x, y in pts:
            lo = [min(lo[0], x), min(lo[1], y)]
            hi = [max(hi[0], x), max(hi[1], y)]
    w, h = hi[0] - lo[0], hi[1] - lo[1]
    body = []
    for pts in polylines:
        # svg y grows downward, so flip about the shape's top edge
        d = " ".join("%s%.3f,%.3f" % ("M" if i == 0 else "L", x - lo[0], hi[1] - y)
                     for i, (x, y) in enumerate(pts))
        body.append('<path d="%s"/>' % d)
    with open(path, "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" '
                'width="%.3fmm" height="%.3fmm" viewBox="0 0 %.3f %.3f">\n'
                '<g fill="none" stroke="black" stroke-width="0.5">\n%s\n</g>\n</svg>\n'
                % (w, h, w, h, "\n".join(body)))


# a top-down single view, used for plain outline dxf. multiview drawing sheets
# instead use a grid layout: each view carries its (col, row) cell so the views
# line up on shared projection lines (third-angle: top above front, right beside
# it), the one thing that makes an orthographic drawing readable. an isometric
# pictorial fills the free cell. cell entries are (label, direction, xdir, col,
# row); row 0 is the top of the sheet.
TOP_VIEW = [("top", App.Vector(0, 0, 1), None)]

# A4 landscape drawable band (mm) and its center. the band sits above the title
# block (bottom-right) with room for the border; layout reserves a gap between
# cells for the extent dims and an outer margin for the hole callouts.
SHEET_W, SHEET_H = 270.0, 150.0
SHEET_CX, SHEET_CY = 148.5, 122.0

# standard drawing ratios (geometry multiplier), largest first. the fit scale is
# snapped down to one of these so the print can be measured off with a ruler.
PREFERRED_SCALES = [10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005]


def _bbox(sources):
    bb = sources[0].Shape.BoundBox
    for o in sources[1:]:
        bb.add(o.Shape.BoundBox)
    return bb


def _unit(v):
    return v / (v.Length or 1.0)


def _view_axes(direction, xdir):
    """page (x, up) unit axes for a view looking along `direction`."""
    d = _unit(direction)
    if xdir is None:
        xdir = App.Vector(0, 1, 0) if abs(d.x) >= max(abs(d.y), abs(d.z)) \
            else App.Vector(1, 0, 0)
    u = _unit(xdir - d * d.dot(xdir))
    return u, d.cross(u)


def _proj_size(bb, direction, xdir=None):
    """2d (w, h) of a bbox projected onto a view's page plane; iso-safe."""
    u, up = _view_axes(direction, xdir)
    corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax)
               for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
    us = [c.dot(u) for c in corners]
    vs = [c.dot(up) for c in corners]
    return max(us) - min(us), max(vs) - min(vs)


def _snap_scale(fit):
    """largest standard ratio that still fits (so the sheet never overflows)."""
    for s in PREFERRED_SCALES:
        if s <= fit:
            return s
    return PREFERRED_SCALES[-1]


def _scale_label(s):
    return "%g:1" % s if s >= 1.0 else "1:%g" % round(1.0 / s, 4)


# the width dimension sits above the view and the height dimension to its right,
# leaving the space below the view for techdraw's auto caption (the view label).
EXTENT_PAD = 11.0
# the ISO diameter sign, drawn into the dimension text. the one deliberate
# non-ASCII value in the package: it is a glyph in the artifact, not source prose.
DIA_SIGN = u"⌀"


def _add_extent_dims(page, view, sw, sh):
    """overall horizontal + vertical extent dimension on one view (scaled w, h)."""
    import TechDraw
    for direction in (0, 1):  # 0 horizontal, 1 vertical
        dim = TechDraw.makeExtentDim(view, [], direction)
        if direction == 0:
            dim.Y = sh / 2.0 + EXTENT_PAD
        else:
            dim.X = sw / 2.0 + EXTENT_PAD
        page.addView(dim)


def _cluster_rows(holes, tol=1.0):
    """group holes (cx, cy, dia) into rows of equal cy (within tol)."""
    rows = []
    for h in sorted(holes, key=lambda h: h[1]):
        if rows and abs(h[1] - rows[-1][0][1]) <= tol:
            rows[-1].append(h)
        else:
            rows.append([h])
    return rows


def add_hole_dims(view, holes):
    """diameter, pitch and edge-offset callouts for the drilled holes.

    holes are given in the part's local frame as (cx, cy, dia). the top view
    looks along Z with the model's X/Y as the view's X/Y, so a hole's unscaled
    2d view point is simply (cx, cy). holes are clustered into rows by cy and
    each row gets a diameter + pitch callout, the rows stacked above the view
    (clear of the width extent dim, which also sits above). uses makeDistanceDim
    (2d points) not makeDistanceDim3d (the 3d variant segfaults adding a cosmetic
    vertex).

    must be called only once the view's projected geometry exists: headless
    that is right after recompute, but in the gui the geometry is realized
    lazily on paint, so the caller has to paint the page first or makeDistanceDim
    segfaults."""
    import TechDraw
    if not holes:
        return
    doc, pad, s = view.Document, 9.0, view.Scale
    # stack the callouts above the view, starting clear of the width extent dim.
    base_y = view.Source[0].Shape.BoundBox.YLength * s / 2.0 + EXTENT_PAD + 16.0

    # makeDistanceDim takes scaled 2d view points and divides by view.Scale to
    # report the true model value, so the reference points are scaled here.
    def dim(xa, xb, cy, fmt, y):
        before = set(o.Name for o in doc.Objects)
        TechDraw.makeDistanceDim(view, "DistanceX",
                                 App.Vector(xa * s, cy * s, 0),
                                 App.Vector(xb * s, cy * s, 0))
        d = next(o for o in doc.Objects if o.Name not in before)
        d.FormatSpec, d.Y = fmt, y
        return d

    y = base_y
    for row in _cluster_rows(holes):
        row = sorted(row, key=lambda h: h[0])
        cx, cy, dia = row[len(row) // 2]
        dim(cx - dia / 2.0, cx + dia / 2.0, cy, DIA_SIGN + "%.1f", y)
        y += pad
        if len(row) > 1:
            dim(row[0][0], row[1][0], cy, "%.1f", y)   # pitch
            y += pad
    # the first hole's offset from the part center, once for the whole pattern.
    first = min(holes, key=lambda h: h[0])
    dim(0.0, first[0], first[1], "%.1f", y)


def _view_by_caption(page, caption):
    for v in page.Views:
        if getattr(v, "Caption", None) == caption:
            return v
    return None


def _fill_title(tmpl, title, scale, bb):
    """populate the title block's editable fields (renders in the pdf path).

    size is the overall bounding box, the single most useful number for someone
    reverse-engineering the part off the print."""
    tmpl.EditableTexts = {
        "Part": title.get("part", ""),
        "Project": title.get("project", ""),
        "Scale": _scale_label(scale),
        "Units": title.get("units", "mm"),
        "Size": "%g x %g x %g" % (round(bb.XLength, 1), round(bb.YLength, 1),
                                  round(bb.ZLength, 1)),
    }


def _norm_cells(directions):
    """normalize view entries to (label, direction, xdir, col, row).

    a 3-tuple (single-view exports) lands in cell (0, 0); a 5-tuple already
    carries its grid cell."""
    out = []
    for i, e in enumerate(directions):
        out.append(e if len(e) == 5 else (e[0], e[1], e[2], 0, 0))
    return out


def _page(doc, sources, directions, tag="", dims=False, title=None):
    """build a TechDraw page laying the views out on a projection-aligned grid.

    directions are cells (label, direction, xdir, col, row); a single shared
    scale (snapped to a standard ratio) fits the whole grid on the sheet. when
    `dims` is set every orthographic view gets overall extent dimensions; the
    isometric pictorial gets none. raw shape->dxf export needs an addon we don't
    have offline, so all dxf goes through TechDraw, which writes dxf natively."""
    cells = _norm_cells(directions)
    bb = _bbox(sources)
    sizes = {label: _proj_size(bb, d, x) for label, d, x, _c, _r in cells}
    ncols = max(c for *_, c, _r in cells) + 1
    nrows = max(r for *_, _c, r in cells) + 1
    colw = [max([sizes[c[0]][0] for c in cells if c[3] == i]) for i in range(ncols)]
    rowh = [max([sizes[c[0]][1] for c in cells if c[4] == i]) for i in range(nrows)]

    gap = 34.0 if dims else 16.0
    margin = 30.0 if dims else 10.0  # outer room for extent dims + hole callouts
    fit = min((SHEET_W - gap * (ncols - 1) - margin) / max(sum(colw), 1e-6),
              (SHEET_H - gap * (nrows - 1) - margin) / max(sum(rowh), 1e-6))
    scale = _snap_scale(min(fit, 10.0))

    page = doc.addObject("TechDraw::DrawPage", "Page" + tag)
    tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template" + tag)
    tmpl.Template = TEMPLATE
    page.Template = tmpl
    if title is not None:
        _fill_title(tmpl, title, scale, bb)

    views = {}
    for label, direction, xdir, col, row in cells:
        view = doc.addObject("TechDraw::DrawViewPart", "View_%s%s" % (label, tag))
        view.Source = sources
        view.Direction = direction
        if xdir is not None:
            view.XDirection = xdir
        view.ScaleType = "Custom"
        view.Scale = scale
        view.Caption = label.upper()
        page.addView(view)
        views[label] = (view, col, row)
    doc.recompute()

    # cell centers: columns left to right, rows top down (techdraw y grows up).
    block_w = sum(colw) * scale + gap * (ncols - 1)
    block_h = sum(rowh) * scale + gap * (nrows - 1)
    colx, acc = [], SHEET_CX - block_w / 2.0
    for w in colw:
        colx.append(acc + w * scale / 2.0)
        acc += w * scale + gap
    rowy, acc = [], SHEET_CY + block_h / 2.0
    for h in rowh:
        rowy.append(acc - h * scale / 2.0)
        acc -= h * scale + gap
    for view, col, row in views.values():
        view.X, view.Y = colx[col], rowy[row]
    doc.recompute()

    if dims:
        for label, (view, col, row) in views.items():
            if label == "iso":  # the pictorial carries no extent dimensions
                continue
            w, h = sizes[label]
            _add_extent_dims(page, view, w * scale, h * scale)
        doc.recompute()
    return page


def export_dxf(doc, sources, path):
    import TechDraw
    page = _page(doc, sources, TOP_VIEW, tag="_dxf")
    TechDraw.writeDXFPage(page, path)


def export_sketch(doc, sketches, stem):
    """export the defining sketch(es) as svg and dxf (the part's 2d profile).

    a sketch-derived part is described by more than one - the padded outline and
    a bore sketch per hole feature - and they are exported together because
    together they are the profile. these are the body's own sketches, not a
    drawing of it, so what ships cannot disagree with what was built.

    svg comes straight from the sketch (headless ok); dxf goes through TechDraw
    like every other dxf, since the offline importDXF exporter is unavailable."""
    objs = list(sketches) if isinstance(sketches, (list, tuple)) else [sketches]
    import importSVG
    importSVG.export(objs, stem + ".svg")
    import TechDraw
    page = _page(doc, objs, TOP_VIEW, tag="_sk")
    TechDraw.writeDXFPage(page, stem + ".dxf")


def make_drawing(doc, sources, path, directions, holes=(), title=None):
    import TechDraw
    page = _page(doc, sources, directions, tag="_dwg", dims=True, title=title)
    if holes:
        # holes are drilled through the thickness, so they read off the top view.
        view = _view_by_caption(page, "TOP")
        if view is not None:
            add_hole_dims(view, holes)
            doc.recompute()
    TechDraw.writeDXFPage(page, path)
    return page
