"""render a solved FEM result to a PNG (headless, plain python).

reads the FreeCAD/VTK-free .npz that `fcad fem` writes (boundary surface + per-node
von Mises and displacement) and draws the deformed surface shaded by stress with a
colorbar: the offscreen feedback loop for a structural result, mirroring render.py
for meshes. runs under plain python (numpy + matplotlib), so name/dist are passed
in (defaulting from the environment for standalone use).
"""

import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from fcad import config
from fcad.render import render

CMAP = "viridis"
# default deflection exaggeration: scale so the peak displacement reads as this
# fraction of the model size (real elastic displacements are invisibly small).
DEFORM_FRAC = float(os.environ.get("FCAD_FEM_DEFORM", 0.08))
VMAX_ENV = "FCAD_FEM_VMAX"
CLAMP_KEY = "von_mises_p99"
MAX_ANIM_TRIS = int(os.environ.get("FCAD_FEM_MAX_TRIS", 8000))


def _cluster_at(nodes, tris, cell):
    """quantize nodes onto a `cell`-mm grid; return (inv_map, kept_tris, n_cells)."""
    lo = nodes.min(axis=0)
    keys = np.floor((nodes - lo) / max(cell, 1e-6)).astype(np.int64)
    _, inv = np.unique(keys, axis=0, return_inverse=True)
    remapped = inv[tris]
    valid = ((remapped[:, 0] != remapped[:, 1])
             & (remapped[:, 1] != remapped[:, 2])
             & (remapped[:, 0] != remapped[:, 2]))
    return inv, remapped[valid], int(inv.max()) + 1


def decimate(nodes, tris, *fields, max_tris=MAX_ANIM_TRIS):
    """vertex-cluster a dense FEM boundary mesh while carrying per-node fields.

    returns `(nodes, tris, *fields)` unchanged when `len(tris) <= max_tris`.
    each field may have shape `(N,)`, `(N, 3)`, or `(K, N, 3)` (mode shapes)."""
    if len(tris) <= max_tris or len(nodes) == 0:
        return (nodes, tris) + fields
    span = float(np.ptp(nodes, axis=0).max()) or 1.0
    # start from an estimate proportional to sqrt(len(tris) / max_tris) and grow.
    cell = span / max(2.0, math.sqrt(max_tris))
    inv, dtris, n_cells = _cluster_at(nodes, tris, cell)
    for _ in range(12):
        if len(dtris) <= max_tris:
            break
        cell *= 1.35
        inv, dtris, n_cells = _cluster_at(nodes, tris, cell)
    counts = np.bincount(inv, minlength=n_cells).astype(np.float32)
    counts[counts == 0] = 1.0

    def avg(arr):
        if arr.ndim == 1:
            acc = np.zeros(n_cells, dtype=np.float32)
            np.add.at(acc, inv, arr)
            return acc / counts
        if arr.ndim == 2:
            acc = np.zeros((n_cells, arr.shape[1]), dtype=np.float32)
            np.add.at(acc, inv, arr)
            return acc / counts[:, None]
        # 3D mode shapes: (n_modes, n_nodes, 3)
        out = np.zeros((arr.shape[0], n_cells, arr.shape[2]), dtype=np.float32)
        for k in range(arr.shape[0]):
            np.add.at(out[k], inv, arr[k])
            out[k] /= counts[:, None]
        return out

    return (avg(nodes), dtris.astype(np.int32)) + tuple(avg(f) for f in fields)


def npz_path(target, name, dist):
    return config.artifact_stem(dist, name, target, config.FEM) + ".npz"


def deform_scale(data, frac=DEFORM_FRAC):
    """exaggeration factor mapping peak displacement to `frac` of the bbox."""
    peak = float(data["disp_mag"].max()) or 1.0
    return frac * float(data["bbox_diag"]) / peak


def stress_scale(data, field):
    """(top of the colour scale, how to describe it) for a von Mises field.

    scaling to the nodal maximum makes the picture useless, and worst exactly
    where a picture is most wanted. the peak sits on a singularity - a clamped
    face, the sharp corner of a notch - where linear elasticity has no finite
    answer, so it can be orders of magnitude above the field a reader wants to
    see: a planter assembly peaking at 3.5 MPa with a p95 of 0.50 puts the whole
    model in the bottom seventh of the colormap and renders as a flat slab. p99
    already travels in the bundle, so clamping to it saturates the top percentile
    and leaves the rest legible. what it must not do is hide the peak silently,
    hence the note - a clamped plot says so and still reports the true maximum."""
    peak = float(field.max())
    override = os.environ.get(VMAX_ENV)
    if override:
        vmax, source = float(override), VMAX_ENV
    elif CLAMP_KEY in getattr(data, "files", data):
        vmax, source = float(data[CLAMP_KEY]), "p99"
    else:
        return peak, "max=%.3g MPa" % peak
    if not 0.0 < vmax < peak:
        return peak, "max=%.3g MPa" % peak
    return vmax, "%s clamp=%.3g, max=%.3g MPa" % (source, vmax, peak)


def field_axes(fig, nodes, tris, field, disp=None, scale=0.0, vmax=None,
               cmap=CMAP, label="von Mises (MPa)"):
    """draw the (optionally deformed) surface colored per-face by `field`.

    returns (ax, collection, norm) so an animator can re-pose the same verts."""
    pts = nodes + scale * disp if disp is not None else nodes
    norm = Normalize(vmin=0.0, vmax=vmax if vmax is not None else float(field.max()))
    colors = plt.get_cmap(cmap)(norm(field[tris].mean(axis=1)))
    coll = Poly3DCollection(pts[tris], facecolors=colors,
                            edgecolor=(0, 0, 0, 0.12), linewidths=0.1)
    ax = fig.add_subplot(111, projection="3d")
    ax.add_collection3d(coll)
    render.square_axes(ax, pts[tris].reshape(-1, 3))
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.6, label=label)
    return ax, coll, norm


def render_static(target="assembly", out=None, name=None, dist=None):
    name, dist = render._resolve(name, dist)
    data = np.load(npz_path(target, name, dist))
    fig = plt.figure(figsize=(10, 8))
    scale = deform_scale(data)
    vmax, note = stress_scale(data, data["von_mises"])
    ax, _, _ = field_axes(fig, data["nodes"], data["tris"], data["von_mises"],
                          disp=data["disp"], scale=scale, vmax=vmax)
    render.aim(ax)
    ax.set_title("%s  von Mises %s  (deform x%.0f)" % (target, note, scale))
    out = out or config.artifact_stem(dist, name, target, config.FEM) + ".png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print("rendered fem %s -> %s" % (target, out))


if __name__ == "__main__":
    args = sys.argv[1:]
    render_static(args[0] if args else "assembly",
                  args[1] if len(args) > 1 else None)
