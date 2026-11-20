# -*- mode: python -*- vim: ft=python
"""end-to-end check of the lap `fastenplates.fcad` computes.

this drives the project's real `compute` under the FreeCAD kernel, so the solids
it measures are the ones the build writes -- and it asserts the things no
`fcad check` assertion can, because they are claims about this joint rather than
about assemblies in general: that it reaches as far as it is asked to, and that
the bores line up with the screw standing in them.

run it with `fcad test`, which judges each test by the verdict `Checks.report()`
writes rather than by an exit status freecadcmd does not report honestly.
"""

import FreeCAD as App

from fcad import testing

V = App.Vector


def main():
    c = testing.Checks()
    mod = testing.load(testing.find())        # a .fcad is not importable
    p = mod.PARAMS
    parts = testing.specs(mod)
    lower, upper = testing.solids(mod, parts["plate"])
    screw = testing.solids(mod, parts["screw"])[0]
    t = p["plate_thk"]

    # the reach is the CONSTRAINT `optimize` is held to, measured on the
    # geometry rather than recomputed from the parameters it was derived from.
    box = testing.extent(mod, parts["plate"])
    c("the joint reaches the %.0f mm asked of it (%.0f)" % (p["reach"],
                                                            box.XLength),
      testing.near(box.XLength, p["reach"]))
    c("the lap is two plates thick (%.0f)" % box.ZLength,
      testing.near(box.ZLength, 2 * t))

    # lapped face to face: the plates share a face and no volume.
    c("the plates touch without interfering (%.3f mm^3)"
      % testing.overlap(lower, upper),
      testing.overlap(lower, upper) < testing.TOUCH_VOL)

    # and the screw stands in a bore drilled through both, not in the plate.
    c("the screw displaces no plate (%.3f mm^3)" % testing.overlap(upper, screw),
      testing.overlap(upper, screw) < testing.TOUCH_VOL)
    c("both bores sit on the screw's axis",
      not lower.isInside(V(0, 0, -t / 2.0), 1e-6, True)
      and not upper.isInside(V(0, 0, t / 2.0), 1e-6, True))
    c("the screw reaches the far face of the lap (%.1f)" % screw.BoundBox.ZMin,
      testing.near(screw.BoundBox.ZMin, -t))

    return c.report()


testing.main(main, __file__)
