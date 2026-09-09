"""regression test for the checks that look for the *absence* of geometry.

everything `check` asserted before these was positive space - solids that
overlap, components that float free, sketches that are loose. an assembly fails
the other way round just as easily, and those failures are invisible to an
overlap test by construction. every one of them renders correctly and exports
correctly:

- `find_unseated` (R3.1.1) - a fastener that fits no hole. `embeds` drops a part
  from the interference test precisely because it sinks into others, which
  leaves the parts fcad stops checking as exactly the ones whose whole job is to
  fit a cavity.
- `find_unsupported` - a part with nothing under it.
- `find_voids` - a cut larger than the joint it relieves. two members that cross
  need one of them to give way over exactly what they share, and no more.
- `find_disjoint` - a part severed by its own joinery, which is still a
  perfectly valid shape.

each is pinned against a fixture rigged to fail it and one rigged not to.

needs FreeCAD: run with `freecadcmd tests/test_checks.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App
import Part

from fcad.freecad import build_assembly

V = App.Vector

PLATE = (60.0, 60.0, 20.0)   # a block with one bore through its thickness
BORE_R = 5.0
PIN_R = 5.0
PIN_LEN = 40.0               # longer than the plate, so it stands proud


class _Spec:
    def __init__(self, name, placements, embeds=False):
        self.name = name
        self.placements = placements
        self.embeds = embeds


class _Project:
    """a plate bored through its thickness, and a pin placed somewhere."""

    name = "tseat"

    def __init__(self, pin_placement):
        self._pin = pin_placement

    def compute(self, values):
        return {"specs": [_Spec("plate", [App.Placement()]),
                          _Spec("pin", [self._pin], embeds=True)]}

    def from_spec(self, spec):
        if spec.name == "plate":
            l, w, t = PLATE
            block = Part.makeBox(l, w, t, V(-l / 2.0, -w / 2.0, -t / 2.0))
            bore = Part.makeCylinder(BORE_R, t + 2.0, V(0, 0, -t / 2.0 - 1.0))
            return block.cut(bore)
        return Part.makeCylinder(PIN_R, PIN_LEN, V(0, 0, -PIN_LEN / 2.0))

    def defaults(self):
        return {}


def _seated():
    """the pin standing in the bore: axes aligned, nothing displaced."""
    return App.Placement(V(0, 0, 0), App.Rotation())


def _crossways():
    """the same pin turned 90 degrees -- lying across a vertical bore, so it is
    driven straight through the plate. this is the failure the check exists for,
    and it is invisible to every other check fcad has."""
    return App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), 90))


def _floating():
    """the pin parked in mid-air beside the plate, holding nothing."""
    return App.Placement(V(200, 0, 0), App.Rotation())


def main():
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    # a pin standing in its own bore is seated: it displaces no material, and
    # the bore wall catches it from the four directions across the axis.
    ok = build_assembly.find_unseated(_Project(_seated()), {})
    check("a pin in its bore is seated (%s)" % (ok or "clean",), not ok)

    # turned across the bore it ploughs through the plate. `embeds` hides that
    # from find_overlaps, so this is the whole reason the check exists.
    bad = build_assembly.find_unseated(_Project(_crossways()), {})
    check("a pin across its bore is caught (%d)" % len(bad), len(bad) == 1)
    if bad:
        check("... and is reported as interference, not as loose",
              bad[0][1] == "interferes")
        check("... naming the part it is driven into (%s)" % bad[0][2],
              "plate" in bad[0][2])

    # and one floating in mid air is held by nothing at all.
    bad = build_assembly.find_unseated(_Project(_floating()), {})
    check("a pin floating in space is caught (%d)" % len(bad), len(bad) == 1)
    if bad:
        check("... and is reported as unseated, not as interference",
              bad[0][1] == "unseated")
        check("... noting nothing captures it (%s)" % bad[0][2],
              "0 of 6" in bad[0][2])

    # the check must stay silent on a model with no embedded parts at all,
    # rather than inventing work for every project that has no fasteners.
    class _NoFasteners(_Project):
        def compute(self, values):
            return {"specs": [_Spec("plate", [App.Placement()])]}

    check("a model with no embedded parts is not flagged",
          not build_assembly.find_unseated(_NoFasteners(_seated()), {}))

    checks.extend(_support_checks())
    checks.extend(_void_checks())
    checks.extend(_disjoint_checks())
    return report(checks)


# --- the other absence-of-geometry checks --------------------------------

class _StackSpec:
    def __init__(self, name, placements, grounded=False, embeds=False):
        self.name = name
        self.placements = placements
        self.grounded = grounded
        self.embeds = embeds


class _Stack:
    """a grounded base and a block placed at some height above it."""

    name = "tstack"

    def __init__(self, block_z, pin=None):
        self._z = block_z
        self._pin = pin

    def compute(self, values):
        specs = [_StackSpec("base", [App.Placement()], grounded=True),
                 _StackSpec("block", [App.Placement(V(0, 0, self._z),
                                                    App.Rotation())])]
        if self._pin is not None:
            specs.append(_StackSpec("pin", [self._pin], embeds=True))
        return {"specs": specs}

    def from_spec(self, spec):
        if spec.name == "pin":
            return Part.makeCylinder(3.0, 60.0, V(0, 0, -30.0))
        return Part.makeBox(40, 40, 20, V(-20, -20, -10))

    def profile(self, spec):
        return None

    def defaults(self):
        return {}


def _support_checks():
    out = []
    # a block resting on the base is held; the same block 30 mm up is not.
    resting = build_assembly.find_unsupported(_Stack(20.0), {})
    floating = build_assembly.find_unsupported(_Stack(50.0), {})
    out.append(("a block resting on the base is supported (%s)"
                % (resting or "clean",), not resting))
    out.append(("a block hanging in the air is caught (%s)" % (floating,),
                floating == ["block_001"]))
    # a fastener through it makes it fastened rather than falling: the pin is
    # tested against the part's *blank*, since a drilled part shares no volume
    # with the fastener sitting in its hole.
    pinned = build_assembly.find_unsupported(
        _Stack(50.0, pin=App.Placement(V(0, 0, 50), App.Rotation())), {})
    out.append(("a fastened block is not reported as falling (%s)"
                % (pinned or "clean",), not pinned))

    # and a model that never says what stands on the ground is left alone -
    # "held up by something" is a claim about a physical stack, and plenty of
    # models are not one.
    class _Ungrounded(_Stack):
        def compute(self, values):
            return {"specs": [_StackSpec("a", [App.Placement()]),
                              _StackSpec("b", [App.Placement(V(0, 0, 90),
                                                             App.Rotation())])]}

    out.append(("a model with nothing grounded is not judged",
                not build_assembly.find_unsupported(_Ungrounded(0), {})))
    return out


class _Relieved:
    """a bar relieved over `cut` of its length where a post crosses it.

    the post is `overlap` wide, so anything relieved past that is a void that
    nothing fills."""

    name = "tvoid"
    BAR = (200.0, 40.0, 20.0)
    OVERLAP = 20.0

    def __init__(self, cut):
        self._cut = cut

    def compute(self, values):
        return {"specs": [_StackSpec("bar", [App.Placement()], grounded=True),
                          _StackSpec("post", [App.Placement()])]}

    def from_spec(self, spec):
        l, w, t = self.BAR
        if spec.name == "post":
            return Part.makeBox(self.OVERLAP, w + 2, t / 2.0,
                                V(l / 2.0 - self.OVERLAP, -w / 2.0 - 1, 0))
        bar = Part.makeBox(l, w, t, V(-l / 2.0, -w / 2.0, -t / 2.0))
        return bar.cut(Part.makeBox(self._cut, w + 2, t / 2.0,
                                    V(l / 2.0 - self._cut, -w / 2.0 - 1, 0)))

    def profile(self, spec):
        if spec.name == "bar":
            l, w, t = self.BAR
            return ([(-l / 2, -w / 2), (l / 2, -w / 2), (l / 2, w / 2),
                     (-l / 2, w / 2)], t)
        return None

    def defaults(self):
        return {}


def _void_checks():
    out = []
    tight = build_assembly.find_voids(_Relieved(_Relieved.OVERLAP), {})
    out.append(("a bar relieved exactly where the post crosses is clean (%s)"
                % (tight or "clean",), not tight))
    loose = build_assembly.find_voids(_Relieved(120.0), {})
    out.append(("a bar relieved far past the crossing is caught (%s)"
                % ([n for n, _, _ in loose],),
                [n for n, _, _ in loose] == ["bar"]))
    return out


class _Severed:
    """a bar cut clean through the middle by its own joinery."""

    name = "tsplit"

    def __init__(self, cut_w):
        self._w = cut_w

    def compute(self, values):
        return {"specs": [_StackSpec("bar", [App.Placement()], grounded=True)]}

    def from_spec(self, spec):
        bar = Part.makeBox(100, 20, 20, V(-50, -10, -10))
        return bar.cut(Part.makeBox(self._w, 22, 12, V(-self._w / 2.0, -11, -1)))

    def profile(self, spec):
        return None

    def defaults(self):
        return {}


def _disjoint_checks():
    out = []
    notched = build_assembly.find_disjoint(_Severed(10.0), {})
    out.append(("a notched bar is still one solid (%s)" % (notched or "clean",),
                not notched))
    # widen the notch through the full depth and the bar falls in two.
    class _Cut(_Severed):
        def from_spec(self, spec):
            bar = Part.makeBox(100, 20, 20, V(-50, -10, -10))
            return bar.cut(Part.makeBox(10, 22, 22, V(-5, -11, -11)))

    split = build_assembly.find_disjoint(_Cut(10.0), {})
    out.append(("a bar severed by its own joinery is caught (%s)" % (split,),
                split == [("bar", 2)]))
    return out


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


if any(a.endswith("test_checks.py") for a in sys.argv):
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        raise SystemExit(report([(traceback.format_exc(), False)]))
