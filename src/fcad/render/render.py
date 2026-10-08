"""what the plain-python animators share: mesh loading, the camera, clip
length and the mp4/gif writer.

these run with no FreeCAD, so the model name + dist dir are passed in (the cli
supplies them; they default from the environment). a built model is not drawn
here any more, still or moving: `fcad render` and `fcad animate` both use the
FreeCAD gui offscreen (`fcad.freecad.render_view`), which can colour a part and
see through one. what is left for matplotlib is the stress plot.
"""

import math
import os
import subprocess

from stl import mesh as stl_mesh
import matplotlib
matplotlib.use("Agg")
from matplotlib.animation import FFMpegWriter

from fcad import config

MP4_EXT = ".mp4"
GIF_EXT = ".gif"
FFMPEG = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
# yuv420p halves the chroma planes, so it refuses an odd width or height.
EVEN = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
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
SPEED = float(os.environ.get("FCAD_SPEED", 1.0))   # playback multiplier


# a clip's length is derived from what it shows, so an enormous one is easy to
# ask for without meaning to: a 224-instance assembly arriving a part at a time
# is 269 seconds, and at 10 fps that is 2698 full redraws. this is a patience
# ceiling, checked before any of them happen.
MAX_FRAMES = int(os.environ.get("FCAD_MAX_FRAMES", 2000))


def plan(frames, fps, label):
    """announce the clip about to be drawn, and refuse an unreasonable one."""
    if frames > MAX_FRAMES:
        raise SystemExit(
            "fcad: %s would be %d frames (%.0fs at %d fps), past the %d-frame "
            "ceiling, and every frame is a full redraw. raise --speed, raise "
            "FCAD_AT_ONCE so more parts arrive together, set --seconds to fix "
            "the length outright, lower --fps, or raise FCAD_MAX_FRAMES."
            % (label, frames, frames / float(fps), fps, MAX_FRAMES))
    # flushed, so it precedes whatever a child process drawing the frames says.
    print("%s: %d frames, %.1fs at %d fps" % (label, frames,
                                              frames / float(fps), fps),
          flush=True)


def _gif(stem):
    """transcode a written mp4 to the gif beside it; returns (mp4, gif).

    the palette gets its own pass rather than the usual single-pass
    split/palettegen/paletteuse filter, because that form has to hold the video
    branch while the palette is computed off the other one, which buffers the
    whole clip."""
    mp4, gif = stem + MP4_EXT, stem + GIF_EXT
    palette = stem + ".palette.png"
    try:
        subprocess.run(FFMPEG + ["-i", mp4, "-vf", "palettegen", palette],
                       check=True)
        subprocess.run(FFMPEG + ["-i", mp4, "-i", palette, "-lavfi",
                                 "paletteuse", "-loop", "0", gif], check=True)
    finally:
        if os.path.exists(palette):
            os.remove(palette)
    return mp4, gif


def save(anim, stem, fps, dpi):
    """write a matplotlib clip as mp4, then transcode it to gif. both stream.

    matplotlib's PillowWriter holds every frame in memory until the end, so a
    long clip buffers gigabytes rather than bytes: the planter's 3854-frame
    assembly animation wanted 8 GB of frame buffer for a model whose geometry is
    0.7 MB, and took the machine down with it. ffmpeg writes as it goes."""
    anim.save(stem + MP4_EXT, writer=FFMpegWriter(fps=fps), dpi=dpi)
    return _gif(stem)


def encode(shots, stem, fps):
    """the mp4 + gif of frames already drawn to disk, one numbered png each.

    `shots` is the printf pattern they were written to, counting from 0."""
    subprocess.run(FFMPEG + ["-framerate", str(fps), "-i", shots, "-vf", EVEN,
                             "-pix_fmt", "yuv420p", stem + MP4_EXT], check=True)
    return _gif(stem)


def length(natural, seconds=None, speed=None):
    """the clip's length: its natural one, scaled by speed, or overridden.

    a clip has a length its content implies - an assembly of twenty parts
    arriving one at a time is a longer film than one of three - so speed is a
    multiplier over that rather than a duration anyone has to work out. `seconds`
    is still there for when a slot has to be filled exactly, and it wins."""
    if seconds:
        return max(1e-3, float(seconds))
    return max(1e-3, float(natural) / max(1e-6, float(speed or SPEED)))


def frames_for(seconds, fps):
    """how many frames a clip of `seconds` at `fps` needs.

    length and frame rate are what a caller thinks in; the frame count is their
    product rather than a third setting, which is why there is no frame knob -
    of the three only two are independent, and the one nobody wants to specify
    is the count. at least two frames, so that even a degenerate request writes
    a playable file instead of an empty one."""
    return max(2, int(round(float(seconds) * int(fps))))


def camera_at(frac=None, camera="orbit"):
    """(elevation, azimuth) of a still viewpoint, or one frame of a moving one.

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
        return ELEV, AZIM
    tilt = TILT if camera == "orbit" else 0.0
    return (ELEV + tilt * math.sin(2.0 * math.pi * frac), AZIM + 360.0 * frac)


def aim(ax, frac=None, camera="orbit"):
    """point a matplotlib axes' camera where `camera_at` says."""
    elev, azim = camera_at(frac, camera)
    ax.view_init(elev=elev, azim=azim)


def _resolve(name, dist):
    name = name or os.environ.get("FCAD_NAME", "assembly")
    dist = dist or os.environ.get("FCAD_DIST", os.path.join(os.getcwd(), "dist"))
    return name, dist


def stl_path(target, name, dist):
    return config.artifact_stem(dist, name, target) + ".stl"


def load_tris(target, name, dist):
    """the (n, 3, 3) triangle array of a built stl."""
    return stl_mesh.Mesh.from_file(stl_path(target, name, dist)).vectors


def square_axes(ax, pts):
    """square the view cube to the point cloud and label the axes, so equal model
    distances look equal on screen."""
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    ctr, span = (lo + hi) / 2.0, (hi - lo).max() / 2.0
    for setlim, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), ctr):
        setlim(c - span, c + span)
    ax.set_box_aspect((1, 1, 1))
    ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")
