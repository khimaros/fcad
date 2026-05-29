"""animate a solved FEM result (headless, plain python).

two kinds, both written as mp4 + gif like animate.py:
  - a static deformation sweep: the part flexes 0 -> peak -> 0 under the load,
    colored by the (fixed) von Mises field so the eye reads stress while it bends;
  - one mode-shape animation per eigenmode, the part oscillating in that mode,
    colored by the mode's displacement magnitude.
reads the FreeCAD/VTK-free .npz from `fcad fem`. one full sine per loop, so the
gif/mp4 loop without a visible seam.
  env: FCAD_FRAMES (default 48), FCAD_FPS (12), FCAD_DPI (90),
       FCAD_FEM_DEFORM (peak deflection as a fraction of model size, 0.08)
"""

import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter

from fcad.render import render, fem_render

FRAMES = int(os.environ.get("FCAD_FRAMES", 48))
FPS = int(os.environ.get("FCAD_FPS", 12))
DPI = int(os.environ.get("FCAD_DPI", 90))


def _save(anim, stem):
    mp4, gif = stem + ".mp4", stem + ".gif"
    anim.save(mp4, writer=FFMpegWriter(fps=FPS), dpi=DPI)
    anim.save(gif, writer=PillowWriter(fps=FPS), dpi=DPI)
    return mp4, gif


def _sweep(nodes, tris, field, mode_disp, peak_scale, title, stem, label):
    """write one flex animation: verts = nodes + peak_scale*sin(2pi f)*mode_disp."""
    fig = plt.figure(figsize=(8, 8))
    ax, coll, _ = fem_render.field_axes(fig, nodes, tris, field,
                                        disp=mode_disp, scale=0.0,
                                        vmax=float(field.max()), label=label)
    ax.set_title(title)

    def update(i):
        s = peak_scale * math.sin(2.0 * math.pi * i / FRAMES)
        coll.set_verts((nodes + s * mode_disp)[tris])
        return ()

    anim = FuncAnimation(fig, update, frames=FRAMES, interval=1000.0 / FPS)
    mp4, gif = _save(anim, stem)
    plt.close(fig)
    return mp4, gif


def animate(target="assembly", stem=None, name=None, dist=None):
    name, dist = render._resolve(name, dist)
    data = np.load(fem_render.npz_path(target, name, dist))
    nodes, tris = data["nodes"], data["tris"]
    stem = stem or os.path.join(dist, "fem_%s" % target)
    written = []

    # static deformation sweep, colored by von Mises.
    scale = fem_render.deform_scale(data)
    written.append(_sweep(nodes, tris, data["von_mises"], data["disp"], scale,
                          "%s  deformation (x%.0f)" % (target, scale),
                          stem, "von Mises (MPa)"))

    # one animation per eigenmode, colored by mode-shape magnitude.
    bbox = float(data["bbox_diag"])
    for k in range(int(data["n_modes"])):
        md = data["mode_disp"][k]
        mag = np.linalg.norm(md, axis=1).astype(np.float32)
        peak = float(mag.max()) or 1.0
        amp = fem_render.DEFORM_FRAC * bbox / peak
        written.append(_sweep(nodes, tris, mag, md, amp,
                              "%s  mode %d: %.1f Hz" %
                              (target, k + 1, float(data["mode_freqs"][k])),
                              "%s_mode%d" % (stem, k + 1), "mode disp (mm)"))

    print("animated fem %s -> %s" % (target, ", ".join(w[0] for w in written)))


if __name__ == "__main__":
    args = sys.argv[1:]
    animate(args[0] if args else "assembly",
            args[1] if len(args) > 1 else None)
