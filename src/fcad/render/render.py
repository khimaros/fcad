"""render an exported STL to a PNG for visual inspection (headless).

the freecad gui needs a display, so this is the offscreen feedback loop: it
loads a built .stl mesh and draws a shaded isometric view with matplotlib. it
runs under plain python (no FreeCAD), so the model name + dist dir are passed in
(the cli supplies them; they default from the environment for standalone use).
"""

import math
import os
import sys

import numpy as np
from stl import mesh as stl_mesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

WOOD = (0.82, 0.71, 0.55)
# the camera, shared by every renderer and animator, because it used to be three
# private copies and FCAD_ELEV meant two different things depending on which
# command read it - a fixed elevation here, the centre of a sweep in animate.py.
# it now means one thing: the elevation of a still and the centre of a sweep,
# with FCAD_AZIM the still's (or the orbit's starting) azimuth and FCAD_TILT the
# sweep amplitude. TILT=0 holds the elevation, which makes a fixed camera a
# special case of an orbit rather than a second code path.
ELEV = float(os.environ.get("FCAD_ELEV", 22))
AZIM = float(os.environ.get("FCAD_AZIM", -58))
TILT = float(os.environ.get("FCAD_TILT", 62))


def aim(ax, frac=None, camera="orbit"):
    """point the camera: a still viewpoint, or one frame of a moving one.

    `frac` is the position in the loop, 0..1, or None for a still. the three
    cameras differ only in how much of that they use - `fixed` none of it,
    `turntable` the azimuth, `orbit` the azimuth and an elevation sweep - so this
    is one expression at two amplitudes rather than three code paths, and every
    motion completes exactly once per loop, leaving the gif without a seam.

    a turntable is the level spin a product shot wants, and keeps a part's
    proportions readable because the eye is never above or below it. an orbit
    additionally rises and dives, which is what brings the top and the underside
    into view - and a loaded structure is supported from underneath, so the
    underside is usually where the answer is."""
    if frac is None or camera == "fixed":
        return ax.view_init(elev=ELEV, azim=AZIM)
    tilt = TILT if camera == "orbit" else 0.0
    ax.view_init(elev=ELEV + tilt * math.sin(2.0 * math.pi * frac),
                 azim=AZIM + 360.0 * frac)


def _resolve(name, dist):
    name = name or os.environ.get("FCAD_NAME", "assembly")
    dist = dist or os.environ.get("FCAD_DIST", os.path.join(os.getcwd(), "dist"))
    return name, dist


def stl_path(target, name, dist):
    if target in ("assembly", name):
        return os.path.join(dist, name + ".stl")
    return os.path.join(dist, "parts", target + ".stl")


def load_tris(target, name, dist):
    """the (n, 3, 3) triangle array of a built stl. shared with animate.py."""
    return stl_mesh.Mesh.from_file(stl_path(target, name, dist)).vectors


def cluster(tris, grid):
    """vertex-cluster decimation, shared with animate.py.

    snaps every triangle corner to a `grid`-mm lattice, drops the triangles that
    collapse to a point/edge, and de-duplicates. a drilled assembly is hundreds
    of thousands of triangles (dense facets around every hole and screw), far too
    many for matplotlib to redraw per animation frame; this cuts it to a few
    thousand while keeping the silhouette. grid <= 0 returns the mesh unchanged."""
    if grid <= 0:
        return tris
    s = np.round(tris / grid).astype(np.int64)
    a, b, c = s[:, 0], s[:, 1], s[:, 2]
    keep = ~((a == b).all(1) | (b == c).all(1) | (a == c).all(1))
    q = (s[keep] * float(grid)).astype(np.float32)
    return np.unique(q.reshape(len(q), 9), axis=0).reshape(-1, 3, 3)


def square_axes(ax, pts):
    """square the view cube to the point cloud and label the axes, so equal model
    distances look equal on screen. shared by the mesh and fem renderers."""
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    ctr, span = (lo + hi) / 2.0, (hi - lo).max() / 2.0
    for setlim, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), ctr):
        setlim(c - span, c + span)
    ax.set_box_aspect((1, 1, 1))
    ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")


def make_axes(fig, tris):
    """a 3d axes with the mesh drawn shaded and the view cube squared up (no
    camera angle or title set, so callers can pose it). shared with animate.py."""
    ax = fig.add_subplot(111, projection="3d")
    ax.add_collection3d(Poly3DCollection(tris, facecolor=WOOD,
                                         edgecolor=(0, 0, 0, 0.25), linewidths=0.2))
    square_axes(ax, tris.reshape(-1, 3))
    return ax


def render(target="assembly", out=None, name=None, dist=None):
    name, dist = _resolve(name, dist)
    tris = load_tris(target, name, dist)
    fig = plt.figure(figsize=(10, 8))
    ax = make_axes(fig, tris)
    aim(ax)
    ax.set_title("%s  (%d triangles)" % (target, len(tris)))

    out = out or os.path.join(dist, "render_%s.png" % target)
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print("rendered %s -> %s" % (stl_path(target, name, dist), out))


if __name__ == "__main__":
    args = sys.argv[1:]
    render(args[0] if len(args) > 0 else "assembly",
           args[1] if len(args) > 1 else None)
