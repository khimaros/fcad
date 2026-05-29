"""render a solved FEM result to a PNG (headless, plain python).

reads the FreeCAD/VTK-free .npz that `fcad fem` writes (boundary surface + per-node
von Mises and displacement) and draws the deformed surface shaded by stress with a
colorbar: the offscreen feedback loop for a structural result, mirroring render.py
for meshes. runs under plain python (numpy + matplotlib), so name/dist are passed
in (defaulting from the environment for standalone use).
"""

import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from fcad.render import render

CMAP = "viridis"
# default deflection exaggeration: scale so the peak displacement reads as this
# fraction of the model size (real elastic displacements are invisibly small).
DEFORM_FRAC = float(os.environ.get("FCAD_FEM_DEFORM", 0.08))


def npz_path(target, name, dist):
    return os.path.join(dist, target + ".fem.npz")


def deform_scale(data, frac=DEFORM_FRAC):
    """exaggeration factor mapping peak displacement to `frac` of the bbox."""
    peak = float(data["disp_mag"].max()) or 1.0
    return frac * float(data["bbox_diag"]) / peak


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
    ax, _, _ = field_axes(fig, data["nodes"], data["tris"], data["von_mises"],
                          disp=data["disp"], scale=scale)
    ax.view_init(elev=float(os.environ.get("FCAD_ELEV", 22)),
                 azim=float(os.environ.get("FCAD_AZIM", -58)))
    ax.set_title("%s  von Mises max=%.3g MPa  (deform x%.0f)" %
                 (target, float(data["von_mises"].max()), scale))
    out = out or os.path.join(dist, "fem_%s.png" % target)
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print("rendered fem %s -> %s" % (target, out))


if __name__ == "__main__":
    args = sys.argv[1:]
    render_static(args[0] if args else "assembly",
                  args[1] if len(args) > 1 else None)
