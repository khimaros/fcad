"""kinematic motion sweeps: pose sampling and trajectory export (pure stdlib).

a project declaring `MOTION` specifies one or more pose trajectories over its
own `PARAMS`. each sweep is either:
  - `{"name": "...", "keyframes": [{param: val, ...}, ...], "steps": 5}`
  - `{"name": "...", "params": {param: (start, end), ...}, "steps": 5}`

`samples()` yields discrete poses for `fcad check` to validate constraints and
clearances across the full motion envelope. `trajectory()` yields smoothly
eased poses for `fcad build` to record `<name>-motion.json`, which `fcad animate
--subject motion` renders without ever importing the project.
"""

import json
import math

DEFAULT_STEPS = 5
DEFAULT_FRAMES = 120


def normalize(motion):
    """normalize `Project.motion` to a list of sweep dicts."""
    if not motion:
        return []
    items = [motion] if isinstance(motion, dict) else list(motion)
    out = []
    for i, item in enumerate(items):
        d = dict(item)
        d.setdefault("name", "motion_%d" % (i + 1))
        d.setdefault("steps", DEFAULT_STEPS)
        d.setdefault("loop", True)
        if "keyframes" not in d and "params" in d:
            start = {k: v[0] for k, v in d["params"].items()}
            end = {k: v[1] for k, v in d["params"].items()}
            d["keyframes"] = [start, end]
        out.append(d)
    return out


def _lerp_pose(base, a, b, t):
    """linearly interpolate numeric keys between keyframe dicts `a` and `b`."""
    posed = dict(base)
    keys = set(a) | set(b)
    for k in keys:
        va = a.get(k, base.get(k))
        vb = b.get(k, base.get(k))
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) \
                and not isinstance(va, bool) and not isinstance(vb, bool):
            posed[k] = float(va) + (float(vb) - float(va)) * t
        else:
            posed[k] = vb if t >= 0.5 else va
    return posed


def _smoothstep(t):
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def samples(project, values=None):
    """yield `(label, posed_values)` across every declared motion sweep."""
    base = dict(project.defaults() if values is None else values)
    for sweep in normalize(getattr(project, "motion", None)):
        kfs = sweep.get("keyframes") or []
        if len(kfs) < 2:
            continue
        steps = max(2, int(sweep.get("steps", DEFAULT_STEPS)))
        name = sweep["name"]
        for seg in range(len(kfs) - 1):
            a, b = kfs[seg], kfs[seg + 1]
            start_i = 0 if seg == 0 else 1
            for i in range(start_i, steps):
                t = i / float(steps - 1)
                label = "%s[%d/%d]" % (name, seg * (steps - 1) + i + 1,
                                       (len(kfs) - 1) * (steps - 1) + 1)
                yield label, _lerp_pose(base, a, b, t)


def trajectory(project, values=None, frames=DEFAULT_FRAMES):
    """list of `frames` smoothly interpolated posed-value dicts for animation."""
    base = dict(project.defaults() if values is None else values)
    sweeps = normalize(getattr(project, "motion", None))
    if not sweeps:
        return []
    kfs = []
    loop = False
    for sw in sweeps:
        sk = list(sw.get("keyframes") or [])
        if sk:
            kfs.extend(sk)
            loop = loop or bool(sw.get("loop", True))
    if len(kfs) < 2:
        return []
    pts = kfs + ([kfs[0]] if loop else [])
    n_seg = len(pts) - 1
    out = []
    for i in range(max(2, int(frames))):
        u = (i / float(frames)) * n_seg if loop else (i / float(frames - 1)) * n_seg
        seg = min(int(math.floor(u)), n_seg - 1)
        t = _smoothstep(u - seg)
        out.append(_lerp_pose(base, pts[seg], pts[seg + 1], t))
    return out


def _is_loop(project):
    sweeps = normalize(getattr(project, "motion", None))
    return any(bool(sw.get("loop", True)) for sw in sweeps)


def export_motion(project, values, path, frames=DEFAULT_FRAMES):
    """evaluate `project.compute` along the motion trajectory and save JSON.

    `compute` returns lazy PartSpecs, so evaluating placements across dozens of
    frames builds zero BREP geometry and runs in milliseconds."""
    poses = trajectory(project, values, frames=frames)
    if not poses:
        return None
    recorded = []
    for posed in poses:
        specs = project.compute(posed)["specs"]
        frame = {spec.name: [[[pl.Base.x, pl.Base.y, pl.Base.z],
                              list(pl.Rotation.Q)]
                             for pl in spec.placements]
                 for spec in specs}
        recorded.append(frame)
    with open(path, "w") as f:
        json.dump({"loop": _is_loop(project), "frames": recorded}, f)
    return path
