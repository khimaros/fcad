"""headless dimensioned multi-view SVG drawing sheet for parts and assemblies.

produces an A4 landscape (297 x 210 mm) third-angle engineering sheet with
TOP, FRONT, RIGHT, and ISO views, hidden lines on orthographic views, overall
extent measurements, hole callouts, a filled title block, and a compact BOM
table for assemblies -- using the same projection-aligned grid math as `util._page`
and without needing a display or GUI session.
"""

from xml.sax.saxutils import escape

import FreeCAD as App
import Part

from fcad.freecad import util as fcutil

PAGE_W, PAGE_H = 297.0, 210.0
BORDER_X, BORDER_Y, BORDER_W, BORDER_H = 7.0, 7.0, 283.0, 196.0
TB_X, TB_Y, TB_W, TB_H = 180.0, 171.0, 110.0, 32.0
NOTES_X, NOTES_Y, NOTES_W, NOTES_H = 7.0, 171.0, 169.0, 32.0

SVG_SHEET_W, SVG_SHEET_H = 278.0, 160.0
SVG_SHEET_CX, SVG_SHEET_CY = 148.5, 121.0
SVG_GAP = 22.0
SVG_MARGIN = 16.0
SVG_SCALES = [10.0, 5.0, 4.0, 2.5, 2.0, 1.0, 0.5, 0.4, 0.25, 0.2, 0.125, 0.1,
              0.05, 0.025, 0.02, 0.01, 0.005]
DIM_OFFSET_H = 5.5
DIM_OFFSET_V = 6.0
CAPTION_PAD = 4.2
ARROW_LEN = 1.6
ARROW_HALF = 0.45
CURVE_MIN_SAMPLES = 6
CURVE_MAX_SAMPLES = 24
ZERO_TOL = 1e-4

COL_EDGE = "#111111"
COL_SMOOTH = "#666666"
COL_HIDDEN = "#888888"
COL_DIM = "#1d4ed8"
COL_LABEL = "#334155"


def _fmt_mm(val):
    return "%g" % round(val, 1)


def _project_view(shape, direction, xdir):
    """project `shape` into an explicit `(u, up, d)` view frame via TechDraw.

    rotating a lightweight copy into `(u, up, d)` and projecting along `+Z`
    locks the 2d axes to the exact requested `xdir` (avoiding `projectEx`'s
    arbitrary in-plane axis) so TOP/FRONT/RIGHT align cleanly in third-angle."""
    import TechDraw
    u, up = fcutil._view_axes(direction, xdir)
    d = fcutil._unit(direction)
    mat = App.Matrix(u.x, u.y, u.z, 0.0,
                     up.x, up.y, up.z, 0.0,
                     d.x, d.y, d.z, 0.0,
                     0.0, 0.0, 0.0, 1.0)
    copy = shape.copy()
    copy.Placement = App.Placement(mat).multiply(copy.Placement)
    res = TechDraw.projectEx(copy, App.Vector(0, 0, 1))
    vis = list(res[0].Edges) + list(res[3].Edges)
    smooth = list(res[1].Edges)
    hid = list(res[5].Edges) + list(res[8].Edges)
    all_vis = vis + smooth
    if all_vis:
        bb = Part.makeCompound(all_vis).BoundBox
        bounds = (bb.XMin, bb.XMax, bb.YMin, bb.YMax)
    else:
        bounds = (0.0, 0.0, 0.0, 0.0)
    return vis, smooth, hid, bounds


def _edge_pts(edge, scale):
    if isinstance(edge.Curve, Part.Line) and len(edge.Vertexes) >= 2:
        return [edge.Vertexes[0].Point, edge.Vertexes[-1].Point]
    n = max(CURVE_MIN_SAMPLES,
            min(CURVE_MAX_SAMPLES, int(edge.Length * scale * 2.0) + CURVE_MIN_SAMPLES))
    return edge.discretize(n)


def _paths_xml(edges, x0, y0, scale, cx, cy):
    out = []
    for edge in edges:
        pts = _edge_pts(edge, scale)
        if len(pts) < 2:
            continue
        cmds = []
        for i, p in enumerate(pts):
            px = cx + (p.x - x0) * scale
            py = cy - (p.y - y0) * scale
            cmds.append("%s%.2f,%.2f" % ("M" if i == 0 else "L", px, py))
        out.append('    <path d="%s"/>' % " ".join(cmds))
    return out


def _arrow(x, y, dx, dy):
    """small filled triangle pointing in unit direction `(dx, dy)` with tip `(x, y)`."""
    bx, by = x - dx * ARROW_LEN, y - dy * ARROW_LEN
    px, py = -dy * ARROW_HALF, dx * ARROW_HALF
    return ('  <polygon points="%.2f,%.2f %.2f,%.2f %.2f,%.2f" fill="%s"/>'
            % (x, y, bx + px, by + py, bx - px, by - py, COL_DIM))


def _dim_h(x1, x2, y_ref, val):
    """horizontal extent dimension centered above a view."""
    if val < ZERO_TOL:
        return []
    yd = y_ref - DIM_OFFSET_H
    lines = [
        '  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>' % (x1, y_ref - 1.2, x1, yd - 1.4),
        '  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>' % (x2, y_ref - 1.2, x2, yd - 1.4),
        '  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>' % (x1, yd, x2, yd),
    ]
    if x2 - x1 >= 2.5 * ARROW_LEN:
        lines += [_arrow(x1, yd, -1.0, 0.0), _arrow(x2, yd, 1.0, 0.0)]
    lines.append(
        '  <text x="%.2f" y="%.2f" text-anchor="middle" stroke="none" '
        'fill="%s" font-size="2.6">%s</text>'
        % ((x1 + x2) / 2.0, yd - 1.1, COL_DIM, escape(_fmt_mm(val))))
    return lines


def _dim_v(y1, y2, x_ref, val):
    """vertical extent dimension placed to the right of a view."""
    if val < ZERO_TOL:
        return []
    xd = x_ref + DIM_OFFSET_V
    lines = [
        '  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>' % (x_ref + 1.2, y1, xd + 1.4, y1),
        '  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>' % (x_ref + 1.2, y2, xd + 1.4, y2),
        '  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>' % (xd, y1, xd, y2),
    ]
    if y2 - y1 >= 2.5 * ARROW_LEN:
        lines += [_arrow(xd, y1, 0.0, -1.0), _arrow(xd, y2, 0.0, 1.0)]
    lines.append(
        '  <text x="%.2f" y="%.2f" text-anchor="start" stroke="none" '
        'fill="%s" font-size="2.6">%s</text>'
        % (xd + 1.5, (y1 + y2) / 2.0 + 0.9, COL_DIM, escape(_fmt_mm(val))))
    return lines


def _hole_marks_and_notes(holes, callouts, top_geom):
    """draw center crosshairs on TOP holes and format a concise HOLES legend."""
    if not holes:
        return []
    out = []
    if top_geom is not None:
        x0, y0, scale, cx, cy = top_geom
        for hx, hy, dia in holes:
            px, py = cx + (hx - x0) * scale, cy - (hy - y0) * scale
            r = max(1.2, min(3.5, dia * scale * 0.35))
            out.append('  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>'
                       % (px - r, py, px + r, py))
            out.append('  <line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f"/>'
                       % (px, py - r, px, py + r))

    grouped = {}
    for hx, hy, dia in holes:
        suffix = (callouts or {}).get((round(hx, 3), round(hy, 3)), "")
        key = (round(dia, 2), suffix)
        grouped.setdefault(key, []).append((hx, hy))

    entries = []
    for (dia, suffix), pts in grouped.items():
        prefix = "%dx " % len(pts) if len(pts) > 1 else ""
        pitch = ""
        if len(pts) > 1:
            xs = sorted(p[0] for p in pts)
            diffs = [round(xs[i + 1] - xs[i], 2) for i in range(len(xs) - 1)
                     if abs(xs[i + 1] - xs[i]) > 0.1]
            if diffs:
                pitch = "  (pitch %s)" % _fmt_mm(diffs[0])
        entries.append("%s&#8960;%s%s%s" % (prefix, _fmt_mm(dia), escape(suffix), pitch))

    out.append(
        '  <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
        'stroke="%s" stroke-width="0.35"/>' % (NOTES_X, NOTES_Y, NOTES_W, NOTES_H, COL_EDGE))
    out.append(
        '  <text x="%.1f" y="%.1f" stroke="none" fill="%s" font-size="2.4" '
        'font-weight="bold">HOLES</text>' % (NOTES_X + 2.5, NOTES_Y + 4.0, COL_LABEL))
    for i, entry in enumerate(entries[:12]):
        col, row = divmod(i, 6)
        tx = NOTES_X + 2.5 + col * 82.0
        ty = NOTES_Y + 8.0 + row * 3.8
        out.append('  <text x="%.1f" y="%.1f" stroke="none" fill="%s" '
                   'font-size="2.5">%s</text>' % (tx, ty, COL_DIM, entry))
    return out


def _bom_xml(specs):
    """compact multi-column BOM table in the bottom-left strip."""
    if not specs:
        return []
    rows_per_col = 7
    ncols = 1 if len(specs) <= rows_per_col else (2 if len(specs) <= 2 * rows_per_col else 3)
    col_w = (NOTES_W - 4.0) / ncols
    lines = [
        '<g id="bom-table" font-family="sans-serif">',
        '  <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
        'stroke="%s" stroke-width="0.35"/>' % (NOTES_X, NOTES_Y, NOTES_W, NOTES_H, COL_EDGE),
        '  <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.25"/>'
        % (NOTES_X, NOTES_Y + 5.0, NOTES_X + NOTES_W, NOTES_Y + 5.0, COL_EDGE),
        '  <text x="%.1f" y="%.1f" fill="%s" font-size="2.4" font-weight="bold">BOM (%d %s)</text>'
        % (NOTES_X + 2.5, NOTES_Y + 3.6, COL_LABEL, len(specs),
           "part" if len(specs) == 1 else "parts"),
    ]
    cap = rows_per_col * ncols
    for i, spec in enumerate(specs[:cap]):
        col, row = divmod(i, rows_per_col)
        tx = NOTES_X + 2.5 + col * col_w
        ty = NOTES_Y + 8.4 + row * 3.4
        prof = getattr(spec, "profile", "") or ""
        label = "%s x%d" % (spec.name, len(spec.placements))
        if prof and ncols < 3:
            label += "  (%s)" % prof
        lines.append('  <text x="%.1f" y="%.1f" fill="%s" font-size="2.2">%s</text>'
                     % (tx, ty, COL_EDGE, escape(label)))
    if len(specs) > cap:
        lines.append('  <text x="%.1f" y="%.1f" fill="%s" font-size="2.1">... +%d more</text>'
                     % (NOTES_X + NOTES_W - 22.0, NOTES_Y + 3.6, COL_LABEL, len(specs) - cap))
    lines.append('</g>')
    return lines


def _title_block_xml(title, scale, bb):
    """A4 border and bottom-right title block matching `fcad_A4_landscape.svg`."""
    t = title or {}
    size_str = "%g x %g x %g" % (round(bb.XLength, 1), round(bb.YLength, 1),
                                 round(bb.ZLength, 1))
    rows = [
        ("PART", t.get("part", ""), TB_X + 2.0, TB_Y + 3.2, TB_X + 20.0, TB_Y + 6.3),
        ("PROJECT", t.get("project", ""), TB_X + 2.0, TB_Y + 11.2, TB_X + 20.0, TB_Y + 14.3),
        ("SCALE", fcutil._scale_label(scale), TB_X + 2.0, TB_Y + 19.2, TB_X + 20.0, TB_Y + 22.3),
        ("UNITS", t.get("units", "mm"), TB_X + 57.0, TB_Y + 19.2, TB_X + 70.0, TB_Y + 22.3),
        ("SIZE", size_str, TB_X + 2.0, TB_Y + 27.2, TB_X + 20.0, TB_Y + 30.3),
    ]
    out = [
        '<g id="title-block" font-family="sans-serif">',
        '  <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
        'stroke="%s" stroke-width="0.45"/>' % (BORDER_X, BORDER_Y, BORDER_W, BORDER_H, COL_EDGE),
        '  <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
        'stroke="%s" stroke-width="0.45"/>' % (TB_X, TB_Y, TB_W, TB_H, COL_EDGE),
    ]
    for ry in (TB_Y + 8.0, TB_Y + 16.0, TB_Y + 24.0):
        out.append('  <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.28"/>'
                   % (TB_X, ry, TB_X + TB_W, ry, COL_EDGE))
    out.append('  <line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.28"/>'
               % (TB_X + 55.0, TB_Y + 16.0, TB_X + 55.0, TB_Y + 24.0, COL_EDGE))
    for lbl, val, lx, ly, vx, vy in rows:
        out.append('  <text x="%.1f" y="%.1f" fill="%s" font-size="2.1">%s</text>'
                   % (lx, ly, COL_LABEL, lbl))
        out.append('  <text x="%.1f" y="%.1f" fill="%s" font-size="3.4">%s</text>'
                   % (vx, vy, COL_EDGE, escape(str(val))))
    out.append('</g>')
    return out


def write_sheet(shape, path, directions, holes=(), callouts=None, bom=None,
                title=None):
    """write a dimensioned third-angle SVG drawing sheet for `shape`."""
    cells = fcutil._norm_cells(directions)
    bb = shape.BoundBox
    sizes, scale, colx, rowy = fcutil._grid_layout(
        bb, cells, dims=True, gap=SVG_GAP, margin=SVG_MARGIN,
        sheet_w=SVG_SHEET_W, sheet_h=SVG_SHEET_H,
        sheet_cx=SVG_SHEET_CX, sheet_cy=SVG_SHEET_CY, scales=SVG_SCALES)

    views_xml, dims_xml = [], ['<g id="dims" fill="none" stroke="%s" '
                               'stroke-width="0.22" font-family="sans-serif">' % COL_DIM]
    top_geom = None
    for label, direction, xdir, col, row in cells:
        cx, cy = colx[col], PAGE_H - rowy[row]
        vis, smooth, hid, (xmin, xmax, ymin, ymax) = _project_view(shape, direction, xdir)
        x0, y0 = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
        w_mm, h_mm = xmax - xmin, ymax - ymin
        if w_mm < ZERO_TOL and h_mm < ZERO_TOL:
            w_mm, h_mm = sizes[label]
        sw, sh = w_mm * scale, h_mm * scale
        left, right, top, bottom = cx - sw / 2.0, cx + sw / 2.0, cy - sh / 2.0, cy + sh / 2.0
        if label == "top":
            top_geom = (x0, y0, scale, cx, cy)

        block = ['<g id="view-%s">' % escape(label)]
        if label != "iso" and hid:
            block.append('  <g fill="none" stroke="%s" stroke-width="0.18" '
                         'stroke-dasharray="1.4,1.0">' % COL_HIDDEN)
            block.extend(_paths_xml(hid, x0, y0, scale, cx, cy))
            block.append('  </g>')
        if smooth:
            block.append('  <g fill="none" stroke="%s" stroke-width="0.18">' % COL_SMOOTH)
            block.extend(_paths_xml(smooth, x0, y0, scale, cx, cy))
            block.append('  </g>')
        block.append('  <g fill="none" stroke="%s" stroke-width="0.35" '
                     'stroke-linecap="round" stroke-linejoin="round">' % COL_EDGE)
        block.extend(_paths_xml(vis, x0, y0, scale, cx, cy))
        block.append('  </g>')
        block.append('  <text x="%.2f" y="%.2f" text-anchor="middle" '
                     'font-family="sans-serif" font-size="2.8" font-weight="bold" '
                     'fill="%s">%s</text>'
                     % (cx, bottom + CAPTION_PAD, COL_LABEL, escape(label.upper())))
        block.append('</g>')
        views_xml.extend(block)

        if label != "iso":
            dims_xml.extend(_dim_h(left, right, top, w_mm))
            dims_xml.extend(_dim_v(top, bottom, right, h_mm))

    dims_xml.extend(_hole_marks_and_notes(holes, callouts, top_geom))
    dims_xml.append('</g>')

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0fmm" height="%.0fmm" '
        'viewBox="0 0 %.0f %.0f">' % (PAGE_W, PAGE_H, PAGE_W, PAGE_H),
        '<rect width="%.0f" height="%.0f" fill="#ffffff"/>' % (PAGE_W, PAGE_H),
    ]
    lines.extend(_title_block_xml(title, scale, bb))
    lines.extend(_bom_xml(bom))
    lines.extend(views_xml)
    lines.extend(dims_xml)
    lines.append('</svg>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
