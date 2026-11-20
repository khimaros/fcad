# -*- mode: python -*- vim: ft=python
"""end-to-end geometry, kinematic, COTS, and handed-variant checks for `robotarm.fcad`."""

from fcad import testing


def main():
    c = testing.Checks()
    mod = testing.load(testing.find())
    p = mod.PARAMS
    data = mod.compute(p)
    parts = {s.name: s for s in data["specs"]}

    feet = testing.solids(mod, parts["feet"])[0]
    base = testing.solids(mod, parts["base_plate"])[0]
    shell = testing.solids(mod, parts["base_shell"])[0]
    cover = testing.solids(mod, parts["base_cover"])[0]
    motor = testing.solids(mod, parts["base_motor"])[0]
    turret = testing.solids(mod, parts["turret"])[0]
    upper = testing.solids(mod, parts["upper_arm"])[0]
    fa_l = testing.solids(mod, parts["forearm_left"])[0]
    fa_r = testing.solids(mod, parts["forearm_right"])[0]
    jaws = testing.solids(mod, parts["jaw"])

    c("feet sit on z=0 (%.2f)" % feet.BoundBox.ZMin,
      testing.near(feet.BoundBox.ZMin, 0.0, 1e-4))
    c("base plate bears on feet (%.2f vs %.2f)"
      % (base.BoundBox.ZMin, feet.BoundBox.ZMax),
      testing.near(base.BoundBox.ZMin, feet.BoundBox.ZMax, 1e-4))
    c("shell bears on base plate",
      testing.near(shell.BoundBox.ZMin, base.BoundBox.ZMax, 1e-4))
    c("cover bears on shell",
      testing.near(cover.BoundBox.ZMin, shell.BoundBox.ZMax, 1e-4))
    c("COTS motor sits on base plate and clears cover & turret",
      testing.near(motor.BoundBox.ZMin, base.BoundBox.ZMax, 1e-4)
      and motor.BoundBox.ZMax < cover.BoundBox.ZMin
      and testing.overlap(motor, turret) < 1e-3)
    c("turret clears polycarbonate cover (%.4f mm^3)"
      % testing.overlap(turret, cover),
      testing.overlap(turret, cover) < 1e-3)
    c("upper arm clears turret clevis (%.4f mm^3)"
      % testing.overlap(upper, turret),
      testing.overlap(upper, turret) < 1e-3)
    c("handed forearm twins straddle upper arm without interference",
      testing.overlap(upper, fa_l) < 1e-3 and testing.overlap(upper, fa_r) < 1e-3)
    c("handed forearm_left has cable-clip bore (smaller volume than right)",
      fa_l.Volume < fa_r.Volume - 50.0)
    c("joint_kit sub-assembly merged 2 bushings, 2 pins, 2 caps, 4 m3 bolts",
      len(parts["bushing"].placements) == 2
      and len(parts["pivot_pin"].placements) == 2
      and len(parts["retainer_cap"].placements) == 2
      and len(parts["bolt_m3"].placements) == 4)

    gap = jaws[0].distToShape(jaws[1])[0]
    c("gripper jaw gap matches jaw_open (%.2f vs %.2f)" % (gap, p["jaw_open"]),
      testing.near(gap, p["jaw_open"], 1e-2))

    sh, el, wr = data["shoulder"], data["elbow"], data["wrist"]
    c("shoulder-to-elbow equals upper_len (%.2f)" % sh.distanceToPoint(el),
      testing.near(sh.distanceToPoint(el), p["upper_len"], 1e-4))
    c("elbow-to-wrist equals forearm_len (%.2f)" % el.distanceToPoint(wr),
      testing.near(el.distanceToPoint(wr), p["forearm_len"], 1e-4))

    return c.report()


testing.main(main, __file__)
