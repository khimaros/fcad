"""turntable animation: orbit the camera a full turn around a built stl.

plain python (no FreeCAD), like render.py, reusing render's mesh loading and
shaded-axes setup, spins the azimuth 360 so every side comes into view, and
writes both a video and a gif (dist/spin_<target>.{mp4,gif}).

the assembly mesh is far too dense (hundreds of thousands of triangles) for
matplotlib to redraw per frame, so it is vertex-cluster decimated first
(render.cluster); a turntable only needs the silhouette.
the camera spins a full turn (every side) while its elevation sweeps one cycle
from above to below, so the top and bottom come into view too; both motions
complete exactly once per loop, so the gif/mp4 loop without a visible seam.
  env: FCAD_FRAMES (default 72), FCAD_FPS (10), FCAD_ELEV (base 20),
       FCAD_TILT (elevation sweep amplitude, 62), FCAD_DPI (90),
       FCAD_CLUSTER (grid mm, default 4; 0 disables)
"""

import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter

from fcad.render import render

FRAMES = int(os.environ.get("FCAD_FRAMES", 72))
FPS = int(os.environ.get("FCAD_FPS", 10))
ELEV = float(os.environ.get("FCAD_ELEV", 20))   # base (mid-sweep) elevation
TILT = float(os.environ.get("FCAD_TILT", 62))   # elevation sweep amplitude
DPI = int(os.environ.get("FCAD_DPI", 90))
CLUSTER = float(os.environ.get("FCAD_CLUSTER", 4.0))


def animate(target="assembly", stem=None, name=None, dist=None):
    name, dist = render._resolve(name, dist)
    tris = render.cluster(render.load_tris(target, name, dist), CLUSTER)
    fig = plt.figure(figsize=(8, 8))
    ax = render.make_axes(fig, tris)
    ax.set_title("%s  (%d triangles)" % (target, len(tris)))

    def update(i):
        f = i / FRAMES  # one full loop: a 360 spin + one up/down elevation cycle
        ax.view_init(elev=ELEV + TILT * math.sin(2.0 * math.pi * f),
                     azim=-180.0 + 360.0 * f)
        return ()

    anim = FuncAnimation(fig, update, frames=FRAMES, interval=1000.0 / FPS)
    stem = stem or os.path.join(dist, "spin_%s" % target)
    mp4, gif = stem + ".mp4", stem + ".gif"
    anim.save(mp4, writer=FFMpegWriter(fps=FPS), dpi=DPI)
    anim.save(gif, writer=PillowWriter(fps=FPS), dpi=DPI)
    print("animated %s -> %s, %s" % (target, mp4, gif))


if __name__ == "__main__":
    args = sys.argv[1:]
    animate(args[0] if len(args) > 0 else "assembly",
            args[1] if len(args) > 1 else None)
