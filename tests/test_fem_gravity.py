"""regression test for the FEM gravity orientation warning (fem.check_gravity).

fem solves in the part's own stock frame, so `gravity=(0,0,-1)` means whatever
that frame currently means. a frame gets rotated for reasons that have nothing
to do with the load case -- to bring an end shoulder into the defining sketch,
say -- and the case goes on declaring the same vector, now aimed along an axis
that is no longer down. the solve does not fail; it loads the member across its
weak axis with its own weight pulling sideways and returns a plausible number.
one such case read 13.8 MPa against a true 3.3.

fcad owns both the case format and the placements, so it can hold them against
each other with no project knowledge at all. this pins that: same declared
vector, three different placements.

needs FreeCAD: run with `freecadcmd tests/test_fem_gravity.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App

from fcad.freecad import fem

V = App.Vector


class _Spec:
    def __init__(self, name, placement):
        self.name = name
        self.placements = [placement]
        self.embeds = False


class _Project:
    name = "tgrav"

    def __init__(self, placement):
        self._spec = _Spec("beam", placement)

    def compute(self, values):
        return {"specs": [self._spec]}

    def defaults(self):
        return {}


def _case(gravity=(0, 0, -1), self_weight=True):
    return SimpleNamespace(gravity=gravity, self_weight=self_weight)


def main():
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    # a part placed upright: its own -Z is the world's, so nothing to say.
    upright = _Project(App.Placement(V(0, 0, 100), App.Rotation()))
    check("an upright part draws no complaint",
          fem.check_gravity(upright, {}, "beam", _case()) is None)

    # the same declaration on a part turned on its side aims gravity along a
    # horizontal axis. this is the failure the check exists for.
    onside = _Project(App.Placement(V(0, 0, 100), App.Rotation(V(0, 1, 0), 90)))
    msg = fem.check_gravity(onside, {}, "beam", _case())
    check("a part turned on its side is caught (%s)" % (msg is not None,),
          msg is not None)
    if msg:
        check("... and the message names the target", "beam" in msg)
        check("... and says which way it actually points",
              "not down" in msg)

    # declaring the vector that *is* down in the rotated frame is correct, and
    # has to stay silent or the check just trains people to ignore it. the part
    # is turned R(y, 90), which carries its local +X onto world -Z.
    check("the corrected vector on the same part is accepted",
          fem.check_gravity(onside, {}, "beam", _case(gravity=(1, 0, 0)))
          is None)

    # a case with self weight off is not making a claim about gravity at all.
    check("a case without self weight is not judged",
          fem.check_gravity(onside, {}, "beam",
                            _case(self_weight=False)) is None)

    # neither is one that never mentioned gravity, nor a target that is not a
    # part (the fused assembly has no single placement to speak of).
    check("a case with no gravity declared is not judged",
          fem.check_gravity(onside, {}, "beam",
                            SimpleNamespace(self_weight=True)) is None)
    check("a target that is not a part is not judged",
          fem.check_gravity(onside, {}, "assembly", _case()) is None)

    return report(checks)


def report(checks):
    failed = [name for name, ok in checks if not ok]
    lines = ["%s %s" % ("ok  " if ok else "FAIL", name) for name, ok in checks]
    lines.append("RESULT %s" % ("PASS" if not failed else "FAIL"))
    text = "\n".join(lines) + "\n"
    rf = os.environ.get("RESULT_FILE")
    if rf:
        with open(rf, "w") as f:
            f.write(text)
    print(text)
    return 0 if not failed else 1


if any(a.endswith("test_fem_gravity.py") for a in sys.argv):
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        raise SystemExit(report([(traceback.format_exc(), False)]))
