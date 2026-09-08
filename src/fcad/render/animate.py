"""animate a built model: the camera moving around it, or it assembling itself.

plain python (no FreeCAD), like render.py, reusing render's mesh loading and
shaded-axes setup, and writing both a video and a gif.

two subjects, the same axis fem-animate has:
  assemble  the parts flying in one at a time to build it; the default for the
            assembly, because a model building itself says more in ten seconds
            than a spin does (dist/assemble_<target>.{mp4,gif})
  static    the model whole, the camera doing the work. the default for a single
            part, which has nothing to assemble (dist/spin_<target>.{mp4,gif})

`assemble` needs the part stls and the placements a build records, not the
assembly stl, which is one welded lump with no part boundaries left in it. see
assemble.py.

the assembly mesh is far too dense (hundreds of thousands of triangles) for
matplotlib to redraw per frame, so it is vertex-cluster decimated first
(render.cluster); a turntable only needs the silhouette.

the camera is `render.aim`, shared with the still renderers and with
fem_animate: `orbit` spins a full turn while the elevation sweeps one cycle so
every side including top and underside comes into view, `turntable` spins level,
and `fixed` holds FCAD_ELEV/FCAD_AZIM. an stl has nothing to animate, so this is
the camera half of the same vocabulary fem-animate uses for both halves.
the clip's length is derived, not set: an assembly of twenty parts arriving one
at a time is a longer film than one of three, so FCAD_AT_ONCE and the part count
move the duration and FCAD_SPEED scales whatever results.
  env: FCAD_SPEED (playback multiplier, default 1), FCAD_FPS (10), FCAD_DPI (90),
       FCAD_SECONDS (force an exact length, overriding speed; a spin's natural
       length is 7.2),
       FCAD_ELEV/FCAD_AZIM (viewpoint, or an orbit's centre/start),
       FCAD_TILT (elevation sweep amplitude, 62; 0 = no sweep),
       FCAD_CLUSTER (grid mm, default 4; 0 disables),
       FCAD_AT_ONCE (parts in the air at once while assembling, default 3;
       1 is strictly sequential, and lengthens the clip accordingly),
       FCAD_EXPLODE/FCAD_FLIGHT_SECONDS/FCAD_SETTLE_SECONDS/FCAD_CONTACT
       (see assemble.py)
"""

import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter

from fcad.render import MESH_SECONDS, assemble, render

SECONDS = float(os.environ.get("FCAD_SECONDS", MESH_SECONDS))
FPS = int(os.environ.get("FCAD_FPS", 10))
DPI = int(os.environ.get("FCAD_DPI", 90))
CLUSTER = float(os.environ.get("FCAD_CLUSTER", 4.0))


def _static(fig, target, name, dist):
    """the whole model, still: the camera is the only thing that moves.

    returns (axes, poser, natural length) like _assemble; one orbit has nothing
    in it to make longer or shorter, so its length is simply the default."""
    tris = render.cluster(render.load_tris(target, name, dist), CLUSTER)
    ax = render.make_axes(fig, tris)
    ax.set_title("%s  (%d triangles)" % (target, len(tris)))
    return ax, lambda frac: None, SECONDS


def _assemble(fig, target, name, dist, how):
    """the model building itself, one instance at a time.

    the assembly stl cannot be used for this - it is a single welded lump with no
    part boundaries left in it - so the pieces come from the part stls plus the
    placements the build recorded, which together are the model with its seams
    still in. the axes are squared to the *assembled* model rather than the
    exploded one, so the finished state fills the frame and the parts fly in from
    outside it, which is the right way round: the last second is what a viewer
    actually looks at.

    it always builds the whole model, so a part target is refused rather than
    quietly animating something the caller did not ask for."""
    if target not in ("assembly", name):
        raise SystemExit(
            "fcad animate: --subject assemble builds the whole assembly, but "
            "TARGET names the part %r. drop the target, or use --subject static "
            "to spin that part on its own." % target)
    parts = assemble.order(assemble.instances(name, dist, CLUSTER), how,
                           assemble.flags(name, dist))
    if not parts:
        raise SystemExit("fcad animate: no part stls + placements to assemble "
                         "for %r - run `fcad build parts assembly` first" % target)
    home = [tris for _, tris in parts]
    offs = assemble.offsets(parts)
    ax = render.make_axes(fig, np.concatenate(home))
    colls = ax.collections[0]
    ax.set_title("%s  assembling %d parts" % (target, len(parts)))

    def pose(frac):
        placed = [h + (1.0 - assemble.arrival(frac, i, len(parts))) * o
                  for i, (h, o) in enumerate(zip(home, offs))]
        colls.set_verts(np.concatenate(placed))

    pose(0.0)
    return ax, pose, assemble.duration(len(parts))


def animate(target="assembly", stem=None, name=None, dist=None, camera="orbit",
            subject="assemble", order="grounded", seconds=None, fps=None,
            speed=None):
    name, dist = render._resolve(name, dist)
    fps = max(1, int(fps or FPS))
    fig = plt.figure(figsize=(8, 8))
    if subject == "assemble":
        ax, pose, natural = _assemble(fig, target, name, dist, order)
    else:
        ax, pose, natural = _static(fig, target, name, dist)
    frames = render.frames_for(render.length(natural, seconds, speed), fps)
    render.aim(ax)

    def update(i):
        frac = i / frames
        pose(frac)
        render.aim(ax, frac, camera)
        return ()

    anim = FuncAnimation(fig, update, frames=frames, interval=1000.0 / fps)
    stem = stem or os.path.join(
        dist, ("assemble_%s" if subject == "assemble" else "spin_%s") % target)
    mp4, gif = stem + ".mp4", stem + ".gif"
    anim.save(mp4, writer=FFMpegWriter(fps=fps), dpi=DPI)
    anim.save(gif, writer=PillowWriter(fps=fps), dpi=DPI)
    print("animated %s -> %s, %s (%.1fs at %d fps)"
          % (target, mp4, gif, frames / float(fps), fps))


if __name__ == "__main__":
    args = sys.argv[1:]
    animate(args[0] if len(args) > 0 else "assembly",
            args[1] if len(args) > 1 else None,
            camera=args[2] if len(args) > 2 else "orbit",
            subject=args[3] if len(args) > 3 else "assemble")
