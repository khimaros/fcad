"""the helpers a project's own tests are written against, run on the examples.

`fcad.testing` is the half of `fcad test` that projects import, so it is held to
the same contract the loader is: a project may return a bare list of specs or a
dict carrying derived values alongside `"specs"`, and both have to load. the
shipped examples are the fixtures, which keeps them honest as documentation too.

needs FreeCAD: run with `freecadcmd tests/test_testing.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import contextlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from fcad import cli, testing

EXAMPLES = os.path.join(ROOT, "examples")


def _example(name):
    return testing.load(testing.find(os.path.join(EXAMPLES, name)))


def main():
    c = testing.Checks()

    # every shipped example loads by path and yields named specs, whether its
    # compute returns the bare list or the dict form.
    for name in sorted(os.listdir(EXAMPLES)):
        d = os.path.join(EXAMPLES, name)
        if not os.path.isdir(d):
            continue
        parts = testing.specs(_example(name))
        c("%s loads and computes its parts (%s)" % (name, sorted(parts)), parts)

    mod = _example("fastenplates")
    parts = testing.specs(mod)
    p = mod.PARAMS
    plate, screw = parts["plate"], parts["screw"]

    c("both plates are the one part placed twice",
      len(testing.solids(mod, plate)) == 2)

    # the lap: two plates thick, and reaching two plate lengths less the overlap.
    box = testing.extent(mod, plate)
    c("the lap is two plates thick (%.1f)" % box.ZLength,
      testing.near(box.ZLength, 2 * p["plate_thk"]))
    c("the lap reaches 2 x length - overlap (%.1f)" % box.XLength,
      testing.near(box.XLength, 2 * p["plate_len"] - p["overlap"]))

    # the plates are lapped face to face, so they share a face and no volume.
    a, b = testing.solids(mod, plate)
    c("the plates touch without interfering (%.3f mm^3)" % testing.overlap(a, b),
      testing.overlap(a, b) < testing.TOUCH_VOL)

    # and the screw sits in the bore drilled for it rather than in the plate.
    s = testing.solids(mod, screw)[0]
    c("the screw displaces no plate (%.3f mm^3)" % testing.overlap(a, s),
      testing.overlap(a, s) < testing.TOUCH_VOL)

    # the whole path end to end: `fcad test` on a project whose tests import
    # `fcad.testing` exactly as the docs write it. freecadcmd's python knows
    # nothing about the installed fcad, so the runner has to say where it is --
    # every project otherwise hand-rolls a sys.path preamble to find it.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = cli.main(["-p", os.path.join(EXAMPLES, "fastenplates"), "test"])
    c("fcad test runs an example's own tests (rc=%s)" % rc, rc == 0)
    c("... and reports their verdict", "RESULT PASS" in buf.getvalue())

    return c.report()


testing.main(main, __file__)
