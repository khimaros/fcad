"""animate a solved FEM result (headless, plain python).

written as mp4 + gif like animate.py, over two orthogonal axes:

  camera   `orbit` (a full turn while the elevation sweeps, so every side comes
           into view including the underside), `turntable` (that turn, level) or
           `fixed` (FCAD_ELEV/FCAD_AZIM). shared with animate.py through
           render.aim, which is the whole point: the two animators used to have
           exactly what the other lacked - animate.py moved the camera and not
           the model, this moved the model and not the camera.
  subject  `flex` (the part deforms 0 -> peak -> 0 under the load), `static`
           (held at peak deflection, nothing moving but the camera), `modes`
           (one clip per eigenmode) or `all` (flex + modes, the default).

reads the FreeCAD/VTK-free .npz from `fcad fem`. every motion completes a whole
number of cycles per loop, so the gif/mp4 loop without a visible seam.
  env: FCAD_FRAMES (default 48), FCAD_FPS (12), FCAD_DPI (90),
       FCAD_FEM_DEFORM (peak deflection as a fraction of model size, 0.08),
       FCAD_FEM_CYCLES (flex cycles per orbit, 4),
       FCAD_ELEV/FCAD_AZIM/FCAD_TILT (the camera, as for every other renderer),
       FCAD_FEM_VMAX (top of the colour scale; default p99)

why the camera matters here more than it sounds: a loaded structure deflects
where it is *supported*, and the supporting members are underneath, or inside, or
otherwise not facing a camera parked above - so a fixed three-quarter view from
above is close to the worst single choice. on a planter assembly the default view
is a flat exterior slab, while looking up at it shows five floor runners bowing
between their bearings with bright spots exactly where they land.
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
# flex cycles per orbit. not 1 on purpose: with a single cycle per turn every
# side is seen at a different deflection, so two faces cannot be compared, which
# is most of what an orbit is for. an integer keeps the loop seamless.
CYCLES = int(os.environ.get("FCAD_FEM_CYCLES", 4))


def _save(anim, stem):
    mp4, gif = stem + ".mp4", stem + ".gif"
    anim.save(mp4, writer=FFMpegWriter(fps=FPS), dpi=DPI)
    anim.save(gif, writer=PillowWriter(fps=FPS), dpi=DPI)
    return mp4, gif


def _clip(nodes, tris, field, mode_disp, peak_scale, title, stem, label,
          vmax=None, camera="orbit", cycles=CYCLES):
    """write one clip: the subject flexing, the camera orbiting, or both.

    `cycles=0` holds the subject at peak deflection instead of flexing it, which
    with an orbit is the most useful single clip there is - the deformed shape,
    seen from everywhere, every face at the same deflection and so comparable.
    holding also lets the view cube be squared to the deformed geometry rather
    than the undeformed, so the flexed shape cannot walk outside the frame."""
    held = peak_scale if not cycles else 0.0
    fig = plt.figure(figsize=(8, 8))
    ax, coll, _ = fem_render.field_axes(
        fig, nodes, tris, field, disp=mode_disp, scale=held,
        vmax=vmax if vmax is not None else float(field.max()), label=label)
    render.aim(ax)
    ax.set_title(title)

    def update(i):
        f = i / FRAMES
        if cycles:
            s = peak_scale * math.sin(2.0 * math.pi * cycles * f)
            coll.set_verts((nodes + s * mode_disp)[tris])
        render.aim(ax, f, camera)
        return ()

    anim = FuncAnimation(fig, update, frames=FRAMES, interval=1000.0 / FPS)
    mp4, gif = _save(anim, stem)
    plt.close(fig)
    return mp4, gif


def animate(target="assembly", stem=None, name=None, dist=None,
            camera="orbit", subject="all"):
    name, dist = render._resolve(name, dist)
    data = np.load(fem_render.npz_path(target, name, dist))
    nodes, tris = data["nodes"], data["tris"]
    stem = stem or os.path.join(dist, "fem_%s" % target)
    written = []

    # the deformation clip, colored by von Mises. the colour scale is clamped off
    # the singular peak for the same reason the still is; a mode clip below is
    # colored by displacement magnitude, which has no singularity to clamp.
    if subject in ("all", "flex", "static"):
        scale = fem_render.deform_scale(data)
        cycles = 0 if subject == "static" else CYCLES
        vmax, note = fem_render.stress_scale(data, data["von_mises"])
        motion = "held" if not cycles else "flex"
        written.append(_clip(nodes, tris, data["von_mises"], data["disp"], scale,
                             "%s  %s (x%.0f)  %s" % (target, motion, scale, note),
                             stem, "von Mises (MPa)", vmax=vmax, camera=camera,
                             cycles=cycles))

    # one clip per eigenmode, colored by mode-shape magnitude. a held mode shape
    # is meaningless (a mode *is* the oscillation), so these always flex.
    if subject in ("all", "modes"):
        bbox = float(data["bbox_diag"])
        for k in range(int(data["n_modes"])):
            md = data["mode_disp"][k]
            mag = np.linalg.norm(md, axis=1).astype(np.float32)
            peak = float(mag.max()) or 1.0
            amp = fem_render.DEFORM_FRAC * bbox / peak
            written.append(_clip(nodes, tris, mag, md, amp,
                                 "%s  mode %d: %.1f Hz" %
                                 (target, k + 1, float(data["mode_freqs"][k])),
                                 "%s_mode%d" % (stem, k + 1), "mode disp (mm)",
                                 camera=camera))

    if not written:
        raise SystemExit("fcad fem-animate: subject %r produced no clips "
                         "(no eigenmodes in the bundle? solve with --modal)"
                         % subject)
    print("animated fem %s -> %s" % (target, ", ".join(w[0] for w in written)))


if __name__ == "__main__":
    args = sys.argv[1:]
    animate(args[0] if args else "assembly",
            args[1] if len(args) > 1 else None)
