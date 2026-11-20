"""kinematic motion sweeps: dynamic check and motion animation (`MOTION`).

a static check at one pose cannot see a linkage that collides mid-swing or a
gripper that crashes into its own base at full reach. a project declaring
`MOTION` defines kinematic trajectories across its own `PARAMS`, and fcad:

1. checks `CONSTRAINTS` and structural/embedded interference at every sampled
   pose during `fcad check`, naming the pose and colliding pair on failure.
2. records per-frame placements in `dist/<name>-motion.json` during `build`
   (without building geometry per frame, since `compute` is lazy).
3. renders `fcad animate --subject motion` smoothly through the trajectory.

needs FreeCAD + xvfb-run + ffmpeg: run with `freecadcmd tests/test_motion.py`.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fcad import testing

_PROJECT = """\
from FreeCAD import Placement, Rotation, Vector
import fcad
from fcad.freecad import partdesign

PARAMS = {"angle": 0.0, "bad_limit": %r}

CONSTRAINTS = [
    lambda p, d: p["angle"] <= 150.0 or "angle exceeds 150 deg",
]

MOTION = [{
    "name": "swing",
    "keyframes": [
        {"angle": 0.0},
        {"angle": %g},
    ],
    "steps": 5,
}]

def _rect(w, h):
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

def _base(doc, body):
    partdesign.pad_and_bore(doc, body, _rect(70.0, 70.0), 10.0, name="base")

def _stop(doc, body):
    partdesign.pad_and_bore(doc, body, _rect(12.0, 12.0), 20.0, name="stop")

def _arm(doc, body):
    partdesign.pad_and_bore(doc, body, _rect(50.0, 10.0), 8.0, name="arm")

def compute(p):
    r = Rotation(Vector(0, 0, 1), p["angle"])
    arm_pos = r.multVec(Vector(25.0, 0.0, 0.0)) + Vector(0.0, 0.0, 9.0)
    return [
        fcad.PartSpec("base", placements=[Placement()], grounded=True,
                      build=_base, color=(0.5, 0.5, 0.5)),
        fcad.PartSpec("stop",
                      placements=[Placement(Vector(0.0, 25.0, 15.0), Rotation())],
                      build=_stop, color=(0.8, 0.2, 0.2)),
        fcad.PartSpec("arm",
                      placements=[Placement(arm_pos, r)],
                      build=_arm, color=(0.2, 0.5, 0.8)),
    ]
"""


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def main():
    c = testing.Checks()
    tmp = tempfile.mkdtemp(prefix="fcad_motion_")
    try:
        # 1. clean swing (0 -> 40 deg clears the stop at +Y=25).
        ok_dir = os.path.join(tmp, "ok")
        os.makedirs(ok_dir)
        with open(os.path.join(ok_dir, "swing.fcad"), "w") as f:
            f.write(_PROJECT % (False, 40.0))

        r = _run(["fcad", "-p", ok_dir, "precommit"], ROOT)
        c("clean motion sweep passes precommit (rc=%d)" % r.returncode,
          r.returncode == 0)
        c("check reports motion envelope ok",
          "motion" in r.stdout.lower() and "ok" in r.stdout.lower())

        motion_json = os.path.join(ok_dir, "dist", "swing-motion.json")
        c("build writes <name>-motion.json", os.path.exists(motion_json))
        if os.path.exists(motion_json):
            with open(motion_json) as f:
                mdata = json.load(f)
            c("motion json records multiple frames (%d)"
              % len(mdata.get("frames", [])),
              len(mdata.get("frames", [])) >= 10)

        # animate --subject motion produces mp4 and gif.
        ra = _run(["fcad", "-p", ok_dir, "animate", "--subject", "motion",
                   "--seconds", "1.0", "--fps", "4", "--size", "320x240"], ROOT)
        mp4 = os.path.join(ok_dir, "dist", "swing.motion.mp4")
        gif = os.path.join(ok_dir, "dist", "swing.motion.gif")
        c("animate --subject motion succeeds (rc=%d)" % ra.returncode,
          ra.returncode == 0 and os.path.exists(mp4) and os.path.exists(gif))

        # 2. colliding swing (0 -> 90 deg passes static check at 0 deg, hits stop at 90 deg).
        hit_dir = os.path.join(tmp, "hit")
        os.makedirs(hit_dir)
        with open(os.path.join(hit_dir, "swing.fcad"), "w") as f:
            f.write(_PROJECT % (True, 90.0))

        rh = _run(["fcad", "-p", hit_dir, "check"], ROOT)
        c("mid-swing collision fails check (rc=%d)" % rh.returncode,
          rh.returncode != 0)
        c("failure names the colliding motion pose and parts",
          "swing" in rh.stdout and "arm" in rh.stdout and "stop" in rh.stdout)

        # 3. constraint violation mid-motion (0 -> 180 deg breaks angle <= 150).
        bad_dir = os.path.join(tmp, "bad")
        os.makedirs(bad_dir)
        with open(os.path.join(bad_dir, "swing.fcad"), "w") as f:
            f.write(_PROJECT % (True, 180.0))

        rb = _run(["fcad", "-p", bad_dir, "check"], ROOT)
        c("mid-motion constraint violation fails check (rc=%d)" % rb.returncode,
          rb.returncode != 0 and "150 deg" in rb.stdout)

        # 4. smooth interpolation on resampling (no nearest-neighbor frame judder).
        from fcad.render import animate
        f0 = {"arm": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]]}
        s45 = math.sin(math.radians(45.0))
        c45 = math.cos(math.radians(45.0))
        f1 = {"arm": [[[100.0, 0.0, 0.0], [0.0, 0.0, s45, c45]]]}  # 90 deg about Z
        up = animate._resample_motion([f0, f1], 5, loop=False)
        mid_pos, mid_q = up[2]["arm"][0]
        want_s = math.sin(math.radians(22.5))
        want_c = math.cos(math.radians(22.5))
        c("upsampling interpolates position smoothly (x=%.2f vs 50.0)" % mid_pos[0],
          testing.near(mid_pos[0], 50.0, 1e-4))
        c("upsampling SLERPs quaternion to exact 45-deg midpoint (%.4f vs %.4f)"
          % (mid_q[2], want_s),
          testing.near(mid_q[2], want_s, 1e-4) and testing.near(mid_q[3], want_c, 1e-4))
        # upsampling 10 recorded frames to 25 produces zero frozen duplicates.
        seq = [{"arm": [[[float(i * 10), 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]]}
               for i in range(10)]
        up25 = animate._resample_motion(seq, 25, loop=False)
        dups = sum(1 for a, b in zip(up25, up25[1:])
                   if abs(a["arm"][0][0][0] - b["arm"][0][0][0]) < 1e-6)
        c("upsampling 10 -> 25 frames produces zero duplicate poses (%d)" % dups,
          dups == 0)
        # default motion fps is smooth (>= 20) and divides 100 cleanly for GIF.
        mfps = getattr(animate, "MOTION_FPS", 0)
        c("default motion fps is smooth and GIF-clean (%d)" % mfps,
          mfps >= 20 and 100 % mfps == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
