"""animate a built model: the camera moving around it, or it assembling itself.

this plans the clip and encodes it; the frames in between are drawn by the same
FreeCAD viewer `fcad render` uses (`fcad.freecad.render_view.film`), so a clip
has the stills' look: each part in its declared colour and transparency, shaded
and outlined, on a plain background. the plan is plain data - where the camera
is and how far out each instance still is, per frame - written to a json file,
because nothing else crosses the process boundary.

two subjects, the same axis fem-animate has:
  assemble  the parts flying in one at a time to build it; the default for the
            assembly, because a model building itself says more in ten seconds
            than a spin does (dist/<name>.assemble.{mp4,gif})
  static    the model whole, the camera doing the work. the default for a single
            part, which has nothing to assemble (dist/parts/<part>.spin.{mp4,gif})

`assemble` is sequenced from the part stls and the placements a build records;
see assemble.py.

the camera is `render.camera_at`, shared with fem_animate: `orbit` spins a full
turn while the elevation sweeps one cycle so every side including top and
underside comes into view, `turntable` spins level, and `fixed` holds
FCAD_ELEV/FCAD_AZIM.
the clip's length is derived, not set: an assembly of twenty parts arriving one
at a time is a longer film than one of three, so FCAD_AT_ONCE and the part count
move the duration and FCAD_SPEED scales whatever results.
  env: FCAD_SPEED (playback multiplier, default 1), FCAD_FPS (10),
       FCAD_SECONDS (force an exact length, overriding speed; a spin's natural
       length is 7.2),
       FCAD_ELEV/FCAD_AZIM (viewpoint, or an orbit's centre/start),
       FCAD_TILT (elevation sweep amplitude, 62; 0 = no sweep),
       FCAD_AT_ONCE (parts in the air at once while assembling, default 3;
       1 is strictly sequential, and lengthens the clip accordingly),
       FCAD_EXPLODE/FCAD_FLIGHT_SECONDS/FCAD_SETTLE_SECONDS/FCAD_CONTACT
       (see assemble.py)
"""

import json
import os
import tempfile

from fcad import config
from fcad.render import MESH_SECONDS, assemble, render

SECONDS = float(os.environ.get("FCAD_SECONDS", MESH_SECONDS))
FPS = int(os.environ.get("FCAD_FPS", 10))
PLAN_NAME = "plan.json"
SHOT_NAME = "%05d.png"
# decimal places an instance's remaining distance is planned to: a part count
# times a frame count of these go into the plan, and a 1e-4 of an offset is far
# under a pixel.
AWAY_DIGITS = 4


def _static():
    """the whole model, still: the camera is the only thing that moves.

    returns (labels, offsets, away, natural length) like _assemble; one orbit
    has nothing in it to make longer or shorter, so its length is the default."""
    return [], [], lambda frac: [], SECONDS


def _assemble(target, name, dist, how):
    """the model building itself, one instance at a time.

    returns the instances that move, the vector each starts displaced by, and
    `away(frac)`: how much of that vector each still has to cover at a point in
    the clip, 1 (not yet placed) .. 0 (home).

    it always builds the whole model, so a part target is refused rather than
    quietly animating something the caller did not ask for."""
    if not config.is_assembly(name, target):
        raise SystemExit(
            "fcad animate: --subject assemble builds the whole assembly, but "
            "TARGET names the part %r. drop the target, or use --subject static "
            "to spin that part on its own." % target)
    marks = assemble.flags(name, dist)
    parts = assemble.order(assemble.instances(name, dist), how, marks)
    if not parts:
        raise SystemExit("fcad animate: no part stls + placements to assemble "
                         "for %r - run `fcad build parts assembly` first" % target)
    slot, count = assemble.slots(parts, marks)

    def away(frac):
        return [round(1.0 - assemble.arrival(frac, s, count), AWAY_DIGITS)
                for s in slot]

    return ([label for label, _ in parts],
            [off.tolist() for off in assemble.offsets(parts)],
            away, assemble.duration(count))


def animate(draw, target="assembly", stem=None, name=None, dist=None,
            camera="orbit", subject="assemble", order="grounded", seconds=None,
            fps=None, speed=None, size=None):
    """plan a clip, have `draw` render its frames, and encode them.

    `draw(plan path)` runs the FreeCAD viewer over the plan and returns its exit
    status; the cli supplies it, since it is the cli that knows the binaries."""
    name, dist = render._resolve(name, dist)
    fps = max(1, int(fps or FPS))
    labels, offsets, away, natural = (
        _assemble(target, name, dist, order) if subject == "assemble"
        else _static())
    frames = render.frames_for(render.length(natural, seconds, speed), fps)
    render.plan(frames, fps, "%s %s" % (target, subject))
    stem = stem or config.artifact_stem(
        dist, name, target,
        config.ASSEMBLE if subject == "assemble" else config.SPIN)
    with tempfile.TemporaryDirectory(prefix="fcad_animate_") as tmp:
        shots, path = os.path.join(tmp, SHOT_NAME), os.path.join(tmp, PLAN_NAME)
        with open(path, "w") as f:
            json.dump({"target": target, "size": size, "shots": shots,
                       "labels": labels, "offsets": offsets,
                       "frames": [[*render.camera_at(i / frames, camera),
                                   away(i / frames)] for i in range(frames)]}, f)
        if draw(path):
            raise SystemExit("fcad animate: the frames could not be drawn")
        mp4, gif = render.encode(shots, stem, fps)
    print("animated %s -> %s, %s" % (target, mp4, gif))
