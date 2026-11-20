"""geometry-predicate edge and face selectors for PartDesign features (stdlib).

a feature referencing a derived edge or face - a Fillet, Chamfer, Draft or
face-attached sketch - breaks whenever the topology renumbers if it writes a
raw `Edge7` or `Face3`. this module is the PartDesign sibling of `fem_select`:
predicates describe the geometry wanted, and `edges()`/`faces()` resolve them
to live FreeCAD names (`["Edge1", ...]`) right where the feature is built.

pure stdlib: never imports FreeCAD, so it stays importable from the cli and
plain-python tests. `edges()` and `faces()` duck-type any FreeCAD `Part.Shape`.
"""

from dataclasses import dataclass
import math

from fcad.fem_select import AXES, DEFAULT_ANGLE, FaceInfo, _axis_vec, _dot, _unit

EDGE_TOL = 1e-4         # mm: coordinate tolerance for exact PartDesign edges
ANGLE_TOL = 5.0         # deg: axis alignment tolerance for straight edges


@dataclass
class EdgeInfo:
    """geometry-derived facts extracted once per edge of a shape."""
    index: int          # 0-based index into shape.Edges
    start: tuple        # (x, y, z)
    end: tuple          # (x, y, z)
    center: tuple       # midpoint / CenterOfMass (x, y, z)
    length: float
    curved: bool
    radius: float       # circle/arc radius, or 0.0 when not circular
    direction: tuple    # unit tangent for straight edges, else (0, 0, 0)


def _edge_infos(shape):
    out = []
    for i, e in enumerate(shape.Edges):
        verts = e.Vertexes
        p0 = (verts[0].Point.x, verts[0].Point.y, verts[0].Point.z)
        p1 = (verts[-1].Point.x, verts[-1].Point.y, verts[-1].Point.z)
        c = e.CenterOfMass
        center = (c.x, c.y, c.z)
        curve = getattr(e, "Curve", None)
        cname = type(curve).__name__ if curve is not None else ""
        is_line = cname == "Line"
        radius = float(getattr(curve, "Radius", 0.0) or 0.0)
        if is_line:
            d = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            direction = _unit(d) if (d[0] or d[1] or d[2]) else (0.0, 0.0, 0.0)
        else:
            direction = (0.0, 0.0, 0.0)
        out.append(EdgeInfo(i, p0, p1, center, float(e.Length),
                            not is_line, radius, direction))
    return out


def _face_infos(shape):
    out = []
    for i, f in enumerate(shape.Faces):
        c = f.CenterOfMass
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        bb = f.BoundBox
        out.append(FaceInfo(
            i, (c.x, c.y, c.z), (n.x, n.y, n.z), float(f.Area),
            (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)))
    return out


def _check_count(names, kind, min_count, max_count, exact):
    n = len(names)
    if exact is not None and n != exact:
        raise ValueError("geom_select: expected %d %s(s), matched %d"
                         % (exact, kind, n))
    if n < min_count:
        raise ValueError("geom_select: expected at least %d %s(s), matched %d"
                         % (min_count, kind, n))
    if max_count is not None and n > max_count:
        raise ValueError("geom_select: expected at most %d %s(s), matched %d"
                         % (max_count, kind, n))
    return names


def edges(shape, selector, min_count=1, max_count=None, exact=None):
    """FreeCAD `EdgeN` names matching `selector` on `shape`."""
    infos = _edge_infos(shape)
    idxs = selector(infos)
    names = ["Edge%d" % (i + 1) for i in idxs]
    return _check_count(names, "edge", min_count, max_count, exact)


def faces(shape, selector, min_count=1, max_count=None, exact=None):
    """FreeCAD `FaceN` names matching `selector` on `shape`."""
    infos = _face_infos(shape)
    idxs = selector(infos)
    names = ["Face%d" % (i + 1) for i in idxs]
    return _check_count(names, "face", min_count, max_count, exact)


def _pts(item):
    """points defining an edge (endpoints + center) or face (center)."""
    if isinstance(item, EdgeInfo):
        return (item.start, item.end, item.center)
    return (item.center,)


def at_coord(axis, value, tol=EDGE_TOL):
    """edges whose endpoints+midpoint (or faces whose center) lie on `axis == value`."""
    vec = _axis_vec(axis)

    def select(items):
        return [it.index for it in items
                if all(abs(_dot(p, vec) - value) <= tol for p in _pts(it))]
    return select


def at_x(value, tol=EDGE_TOL):
    return at_coord("x", value, tol)


def at_y(value, tol=EDGE_TOL):
    return at_coord("y", value, tol)


def at_z(value, tol=EDGE_TOL):
    return at_coord("z", value, tol)


def min_along(axis, tol=EDGE_TOL):
    """edges or faces at the minimum coordinate along `axis`."""
    return _extreme(axis, tol, want_max=False)


def max_along(axis, tol=EDGE_TOL):
    """edges or faces at the maximum coordinate along `axis`."""
    return _extreme(axis, tol, want_max=True)


def _extreme(axis, tol, want_max):
    vec = _axis_vec(axis)

    def select(items):
        if not items:
            return []
        vals = [_dot(it.center, vec) for it in items]
        target = max(vals) if want_max else min(vals)
        return [it.index for it, v in zip(items, vals) if abs(v - target) <= tol]
    return select


def along_axis(axis, tol_deg=ANGLE_TOL):
    """straight edges parallel to `axis` (either sign)."""
    want = _axis_vec(axis)
    cos_tol = math.cos(math.radians(tol_deg))

    def select(items):
        return [it.index for it in items
                if isinstance(it, EdgeInfo) and not it.curved
                and abs(_dot(it.direction, want)) >= cos_tol]
    return select


def normal_dir(vec, tol_deg=DEFAULT_ANGLE):
    """faces whose outward normal points within `tol_deg` of `vec`."""
    want = _axis_vec(vec) if isinstance(vec, str) else _unit(tuple(vec))
    cos_tol = math.cos(math.radians(tol_deg))

    def select(items):
        return [it.index for it in items
                if isinstance(it, FaceInfo)
                and _dot(_unit(it.normal), want) >= cos_tol]
    return select


def curved():
    """non-linear edges (arcs, circles, splines)."""
    return lambda items: [it.index for it in items
                          if isinstance(it, EdgeInfo) and it.curved]


def straight():
    """linear edges."""
    return lambda items: [it.index for it in items
                          if isinstance(it, EdgeInfo) and not it.curved]


def radius_near(radius, tol=EDGE_TOL):
    """circular edges whose radius is within `tol` of `radius`."""
    return lambda items: [it.index for it in items
                          if isinstance(it, EdgeInfo) and it.radius > 0.0
                          and abs(it.radius - radius) <= tol]


def length_between(lo=0.0, hi=float("inf")):
    """edges whose length lies in [lo, hi]."""
    return lambda items: [it.index for it in items
                          if isinstance(it, EdgeInfo) and lo <= it.length <= hi]


def in_box(lo, hi):
    """edges or faces whose center lies inside axis-aligned box [lo, hi]."""
    return lambda items: [it.index for it in items
                          if all(lo[i] <= it.center[i] <= hi[i] for i in range(3))]


def all_of(*selectors):
    """items matched by every selector (intersection)."""
    def select(items):
        common = None
        for sel in selectors:
            got = set(sel(items))
            common = got if common is None else (common & got)
        return sorted(common or set())
    return select


def any_of(*selectors):
    """items matched by at least one selector (union)."""
    def select(items):
        out = set()
        for sel in selectors:
            out.update(sel(items))
        return sorted(out)
    return select


def invert(selector):
    """items NOT matched by `selector`."""
    def select(items):
        hit = set(selector(items))
        return [it.index for it in items if it.index not in hit]
    return select
