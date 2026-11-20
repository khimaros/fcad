# -*- mode: python -*- vim: ft=python
"""end-to-end geometry check for `hexnut.fcad`."""

import math

from FreeCAD import Vector

from fcad import testing


def main():
    c = testing.Checks()
    mod = testing.load(testing.find())
    p = mod.PARAMS
    parts = testing.specs(mod)
    hex_solid = testing.solids(mod, parts["hex"])[0]
    bb = hex_solid.BoundBox

    af, t, d = p["across_flats"], p["thickness"], p["hole_dia"]
    across_corners = 2.0 * af / math.sqrt(3.0)
    want_vol = (math.sqrt(3.0) / 2.0 * af * af - math.pi * (d / 2.0) ** 2) * t

    c("across-flats matches PARAMS (%.2f vs %.2f)" % (bb.YLength, af),
      testing.near(bb.YLength, af, 1e-3))
    c("across-corners matches 2*af/sqrt(3) (%.2f vs %.2f)"
      % (bb.XLength, across_corners),
      testing.near(bb.XLength, across_corners, 1e-3))
    c("thickness matches PARAMS (%.2f)" % bb.ZLength,
      testing.near(bb.ZLength, t, 1e-4))
    c("center bore is open through the solid",
      not hex_solid.isInside(Vector(0.0, 0.0, 0.0), 1e-4, True))
    c("volume matches analytic hex prism minus bore (%.1f vs %.1f mm^3)"
      % (hex_solid.Volume, want_vol),
      testing.near(hex_solid.Volume, want_vol, 1.0))

    return c.report()


testing.main(main, __file__)
