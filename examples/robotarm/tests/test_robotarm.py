# -*- mode: python -*- vim: ft=python
"""end-to-end geometry, articulation, and FEM checks for `robotarm.fcad`."""

import math
import os

from fcad import loader, testing
from fcad.freecad import build_assembly

AL6061_YIELD_MPA = 276.0


def _posed(mod, **overrides):
    vals = dict(mod.PARAMS)
    vals.update(overrides)
    return vals, mod.compute(vals)


def _geometry_checks(c, mod):
    p = mod.PARAMS
    data = mod.compute(p)
    parts = {s.name: s for s in data["specs"]}
    sol = lambda k: testing.solids(mod, parts[k])

    feet, base, shell, cover = sol("feet")[0], sol("base_plate")[0], sol("base_shell")[0], sol("base_cover")[0]
    motor, turret, upper = sol("base_motor")[0], sol("turret")[0], sol("upper_arm")[0]
    fa_l, fa_r, jaws = sol("forearm_left")[0], sol("forearm_right")[0], sol("jaw")

    c("feet sit on z=0 (%.2f)" % feet.BoundBox.ZMin,
      testing.near(feet.BoundBox.ZMin, 0.0, 1e-4))
    c("base plate bears on feet",
      testing.near(base.BoundBox.ZMin, feet.BoundBox.ZMax, 1e-4))
    c("shell bears on base plate",
      testing.near(shell.BoundBox.ZMin, base.BoundBox.ZMax, 1e-4))
    c("cover bears on shell",
      testing.near(cover.BoundBox.ZMin, shell.BoundBox.ZMax, 1e-4))
    c("COTS motor sits on base plate and clears cover & turret",
      testing.near(motor.BoundBox.ZMin, base.BoundBox.ZMax, 1e-4)
      and motor.BoundBox.ZMax < cover.BoundBox.ZMin
      and testing.overlap(motor, turret) < 1e-3)
    c("turret clears polycarbonate cover", testing.overlap(turret, cover) < 1e-3)
    c("upper arm clears turret clevis", testing.overlap(upper, turret) < 1e-3)
    c("handed forearm twins straddle upper arm cleanly",
      testing.overlap(upper, fa_l) < 1e-3 and testing.overlap(upper, fa_r) < 1e-3)
    c("handed forearm_left carries cable-clip bore (smaller volume than right)",
      fa_l.Volume < fa_r.Volume - 50.0)
    c("joint_kit sub-assembly merged 2 bushings, 2 pins, 2 caps, 4 m3 bolts",
      len(parts["bushing"].placements) == 2
      and len(parts["pivot_pin"].placements) == 2
      and len(parts["retainer_cap"].placements) == 2
      and len(parts["bolt_m3"].placements) == 4)
    c("gripper jaw gap matches jaw_open (%.2f)" % jaws[0].distToShape(jaws[1])[0],
      testing.near(jaws[0].distToShape(jaws[1])[0], p["jaw_open"], 1e-2))
    c("shoulder-to-elbow equals upper_len",
      testing.near(data["shoulder"].distanceToPoint(data["elbow"]),
                   p["upper_len"], 1e-4))
    c("elbow-to-wrist equals forearm_len",
      testing.near(data["elbow"].distanceToPoint(data["wrist"]),
                   p["forearm_len"], 1e-4))


def _articulation_checks(c, mod, path):
    p = mod.PARAMS

    # base yaw sweep (-90..+135 deg) with invariant radial reach.
    radii, yaw_ok = [], True
    for deg in (-90.0, 0.0, 45.0, 135.0):
        _, data = _posed(mod, theta_base=deg)
        wr = data["wrist"]
        radii.append(math.hypot(wr.x, wr.y))
        parts = {s.name: s for s in data["specs"]}
        turret = testing.solids(mod, parts["turret"])[0]
        cover = testing.solids(mod, parts["base_cover"])[0]
        motor = testing.solids(mod, parts["base_motor"])[0]
        if (not testing.near(math.degrees(math.atan2(wr.y, wr.x)), deg, 1e-3)
                or testing.overlap(turret, cover) >= 1e-3
                or testing.overlap(turret, motor) >= 1e-3):
            yaw_ok = False
    c("base yaw sweeps -90..+135 deg with constant reach and zero collision",
      yaw_ok and all(testing.near(r, radii[0], 1e-4) for r in radii))

    # shoulder pitch sweep (-30..+60 deg).
    sh_ok = True
    for ts in (-30.0, 0.0, 30.0, 60.0):
        _, data = _posed(mod, theta_base=0.0, theta_shoulder=ts, theta_elbow=20.0)
        v = data["elbow"] - data["shoulder"]
        parts = {s.name: s for s in data["specs"]}
        if (not testing.near(v.Length, p["upper_len"], 1e-4)
                or not testing.near(math.degrees(math.atan2(v.x, v.z)), ts, 1e-3)
                or testing.overlap(testing.solids(mod, parts["upper_arm"])[0],
                                   testing.solids(mod, parts["turret"])[0]) >= 1e-3):
            sh_ok = False
    c("shoulder pitch sweeps -30..+60 deg with exact geometry and zero collision",
      sh_ok)

    # elbow pitch sweep (-45..+90 deg).
    el_ok = True
    for te in (-45.0, 0.0, 45.0, 90.0):
        _, data = _posed(mod, theta_base=0.0, theta_shoulder=20.0, theta_elbow=te)
        u1 = (data["elbow"] - data["shoulder"]).normalize()
        u2 = (data["wrist"] - data["elbow"]).normalize()
        got_te = math.degrees(math.atan2(u1.z * u2.x - u1.x * u2.z, u1.dot(u2)))
        parts = {s.name: s for s in data["specs"]}
        upper = testing.solids(mod, parts["upper_arm"])[0]
        fa_l = testing.solids(mod, parts["forearm_left"])[0]
        fa_r = testing.solids(mod, parts["forearm_right"])[0]
        if (not testing.near(data["elbow"].distanceToPoint(data["wrist"]),
                             p["forearm_len"], 1e-4)
                or not testing.near(got_te, te, 1e-3)
                or testing.overlap(upper, fa_l) >= 1e-3
                or testing.overlap(upper, fa_r) >= 1e-3):
            el_ok = False
    c("elbow pitch sweeps -45..+90 deg with exact bend angle and clear cheeks",
      el_ok)

    # parallel gripper jaw stroke (8..42 mm).
    jaw_ok = True
    for gap_want in (8.0, 24.0, 42.0):
        _, data = _posed(mod, jaw_open=gap_want)
        parts = {s.name: s for s in data["specs"]}
        jaws = testing.solids(mod, parts["jaw"])
        wrist = testing.solids(mod, parts["wrist_block"])[0]
        if (not testing.near(jaws[0].distToShape(jaws[1])[0], gap_want, 1e-2)
                or testing.overlap(jaws[0], jaws[1]) >= 1e-3
                or testing.overlap(jaws[0], wrist) >= 1e-3
                or testing.overlap(jaws[1], wrist) >= 1e-3):
            jaw_ok = False
    c("gripper jaws track jaw_open across 8..42 mm cleanly", jaw_ok)

    # full MOTION envelope + ground-collision rejection + saved Assembly joints.
    os.environ["FCAD_PROJECT"] = os.path.dirname(path)
    os.environ["FCAD_ENTRY"] = path
    project = loader.load_project()
    n_poses, issues = build_assembly.find_motion_issues(project, p)
    c("MOTION sweep is collision- and violation-free across %d poses" % n_poses,
      n_poses >= 5 and not issues)
    viol = project.violations(dict(p, theta_shoulder=75.0, theta_elbow=120.0))
    c("extreme downward pose rejected by ground-clearance constraint",
      any("clears desk" in v for v in viol))

    asm_path = os.path.join(project.dist, project.name + ".FCStd")
    if os.path.exists(asm_path):
        import FreeCAD as App
        doc = App.openDocument(asm_path)
        try:
            jmap = {o.Name: getattr(o, "JointType", None) for o in doc.Objects
                    if hasattr(o, "JointType")}
        finally:
            App.closeDocument(doc.Name)
        want = {"Rev_base": "Revolute", "Rev_shoulder": "Revolute",
                "Rev_elbow_L": "Revolute", "Rev_elbow_R": "Revolute",
                "Slide_jaw_1": "Slider", "Slide_jaw_2": "Slider"}
        c("assembly document carries all 6 kinematic joints",
          all(jmap.get(k) == v for k, v in want.items()))
        c("every component in saved assembly is constrained",
          not build_assembly.find_unconstrained(asm_path))


def _fem_checks(c, mod):
    ext = testing.fem_solve("assembly")
    c("extended assembly solves with positive nodes (%d)" % ext.nodes,
      ext.nodes > 500)
    c("extended tip deflection within 0.5 mm budget (%.4f mm)" % ext.max_disp,
      0.01 < ext.max_disp < 0.5)
    c("extended safety factor >= 10 on 6061-T6 (SF=%.1f)"
      % ext.safety_factor(AL6061_YIELD_MPA),
      ext.safety_factor(AL6061_YIELD_MPA) >= 10.0)
    c("extended assembly yields 3 ascending eigenmodes (%s Hz)"
      % [round(f, 1) for f in ext.mode_freqs],
      len(ext.mode_freqs) == 3
      and 5.0 < ext.mode_freqs[0] < ext.mode_freqs[1] < ext.mode_freqs[2])

    folded = testing.fem_solve("assembly_folded")
    c("retracted pose deflects less than extended (%.4f < %.4f mm)"
      % (folded.max_disp, ext.max_disp),
      0.005 < folded.max_disp < ext.max_disp)
    c("retracted pose has lower p95 stress than extended (%.2f < %.2f MPa)"
      % (folded.vm_p95, ext.vm_p95),
      0.0 < folded.vm_p95 < ext.vm_p95)

    link1 = testing.fem_solve("upper_arm")
    link2 = testing.fem_solve("upper_arm",
                              values={"payload_n": 2.0 * mod.PARAMS["payload_n"]})
    ratio = link2.max_disp / link1.max_disp if link1.max_disp else 0.0
    c("upper_arm deflection scales linearly with payload (2x -> %.3fx)" % ratio,
      testing.near(ratio, 2.0, 0.02))
    c("upper_arm safety factor >= 10 at rated payload (SF=%.1f)"
      % link1.safety_factor(AL6061_YIELD_MPA),
      link1.safety_factor(AL6061_YIELD_MPA) >= 10.0)


def main():
    c = testing.Checks()
    path = testing.find()
    mod = testing.load(path)
    _geometry_checks(c, mod)
    _articulation_checks(c, mod, path)
    _fem_checks(c, mod)
    return c.report()


testing.main(main, __file__)
