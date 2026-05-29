"""geometry-predicate face selectors for FEM constraints (pure stdlib).

a project declares which faces are fixed or loaded without naming fragile
FreeCAD face indices ("Face6" reorders whenever the geometry changes). instead a
*selector* is a predicate over the built shape's faces, resolved to live face
indices in the same process that builds the analysis, so an index never crosses
a geometry or process boundary.

a selector is a callable `list[FaceInfo] -> list[int]` returning indices into
`shape.Faces`. `fem.py` populates the `FaceInfo` list from FreeCAD (this module
imports nothing from FreeCAD, so it stays importable everywhere and unit-testable)
and calls `resolve(sel)` to normalize the shorthands a project may write.
"""

from dataclasses import dataclass

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
DEFAULT_TOL = 1.0       # mm: a face center within this of the extreme is "at" it
DEFAULT_ANGLE = 15.0    # deg: normal cone half-angle for normal_dir


@dataclass
class FaceInfo:
    """the geometry-derived facts fem.py extracts once per face of the shape."""
    index: int          # index into shape.Faces
    center: tuple       # CenterOfMass (x, y, z)
    normal: tuple       # unit normal at the face center
    area: float
    bbox: tuple         # (xmin, ymin, zmin, xmax, ymax, zmax)


def _axis_vec(axis):
    """a unit vector from an axis name (optionally signed, '-z'/'+x') or triple."""
    if isinstance(axis, str):
        s = axis.lower().strip()
        sign = -1.0 if s[0] == "-" else 1.0
        base = AXES[s.lstrip("+-")]
        return (sign * base[0], sign * base[1], sign * base[2])
    return _unit(tuple(axis))


def _unit(v):
    m = (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5 or 1.0
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def min_along(axis, tol=DEFAULT_TOL):
    """faces whose center is at the global minimum coordinate along `axis`."""
    return _extreme(axis, tol, want_max=False)


def max_along(axis, tol=DEFAULT_TOL):
    """faces whose center is at the global maximum coordinate along `axis`."""
    return _extreme(axis, tol, want_max=True)


def _extreme(axis, tol, want_max):
    vec = _axis_vec(axis)

    def select(faces):
        proj = [_dot(f.center, vec) for f in faces]
        target = max(proj) if want_max else min(proj)
        return [f.index for f, p in zip(faces, proj) if abs(p - target) <= tol]
    return select


def normal_dir(vec, tol_deg=DEFAULT_ANGLE):
    """faces whose outward normal points within `tol_deg` of `vec`."""
    want = _axis_vec(vec) if isinstance(vec, str) else _unit(tuple(vec))
    cos_tol = _cos_deg(tol_deg)

    def select(faces):
        return [f.index for f in faces if _dot(_unit(f.normal), want) >= cos_tol]
    return select


def _cos_deg(deg):
    # tiny stdlib-only cosine so we avoid importing math just for this.
    import math
    return math.cos(math.radians(deg))


def in_box(lo, hi):
    """faces whose center lies inside the axis-aligned box [lo, hi]."""
    def select(faces):
        return [f.index for f in faces
                if all(lo[i] <= f.center[i] <= hi[i] for i in range(3))]
    return select


def largest(n=1):
    """the `n` faces of greatest area (the broad 'show' faces)."""
    def select(faces):
        ranked = sorted(faces, key=lambda f: f.area, reverse=True)
        return [f.index for f in ranked[:n]]
    return select


def all_of(*selectors):
    """faces matched by every selector (set intersection)."""
    sels = [resolve(s) for s in selectors]

    def select(faces):
        common = None
        for s in sels:
            got = set(s(faces))
            common = got if common is None else (common & got)
        return sorted(common or set())
    return select


def any_of(*selectors):
    """faces matched by at least one selector (set union)."""
    sels = [resolve(s) for s in selectors]

    def select(faces):
        out = set()
        for s in sels:
            out.update(s(faces))
        return sorted(out)
    return select


def invert(selector):
    """faces NOT matched by `selector`."""
    sel = resolve(selector)

    def select(faces):
        hit = set(sel(faces))
        return [f.index for f in faces if f.index not in hit]
    return select


def resolve(sel):
    """normalize the shorthands a project may write into a selector callable.

    accepts a selector callable as-is, the tuple shorthand ("z", "min")/("x",
    "max"), or a raw "FaceN" string (a literal index escape hatch)."""
    if callable(sel):
        return sel
    if isinstance(sel, str) and sel.lower().startswith("face"):
        idx = int(sel[4:]) - 1   # FreeCAD face names are 1-based
        return lambda faces: [idx]
    if isinstance(sel, (tuple, list)) and len(sel) == 2 and sel[1] in ("min", "max"):
        return min_along(sel[0]) if sel[1] == "min" else max_along(sel[0])
    raise ValueError("unrecognized face selector: %r" % (sel,))
