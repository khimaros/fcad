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
  need one of them to give way over exactly what they share, and no more. a cut
  the project calls an `opening` is not one of these: a nut's bore is the point
  of the part, and no geometry distinguishes it from a lap cut too wide.
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
    checks.extend(_opening_checks())
    checks.extend(_undrilled_checks())
    checks.extend(_disjoint_checks())
    return report(checks)


# --- the other absence-of-geometry checks --------------------------------

class _StackSpec:
    def __init__(self, name, placements, grounded=False, embeds=False):
        self.name = name
        self.placements = placements
        self.grounded = grounded
        self.embeds = embeds


class _DeclaredSpec:
    """a spec whose geometry is a real feature tree, as a project's would be."""

    def __init__(self, name, placements, points, thickness, holes=(),
                 grounded=False, embeds=False, openings=()):
        self.name = name
        self.placements = placements
        self.grounded = grounded
        self.embeds = embeds
        self.openings = list(openings)
        self.declared = True
        self._geom = (points, thickness, list(holes))

    def build_into(self, doc, body):
        from fcad.freecad.partdesign import pad_and_bore
        points, thickness, holes = self._geom
        return pad_and_bore(doc, body, points, thickness, holes, name=self.name)

    def solid(self):
        from fcad.freecad.partdesign import shape_of
        return shape_of(self)


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


class _Bored:
    """a plate with one big bore through it: a nut, not a joint.

    the bore is a real `PartDesign::Hole` in the part's own tree, so nothing has
    to be told it exists. what fcad cannot see is whether it is *meant* to stay
    empty - a nut's bore and a lap relieved twice as wide as its crossing member
    are both a feature that removed material - so the project says so with
    `openings`. the bore is far too big to hide under the budget either way:
    5.6% of the blank against a 1% void allowance."""

    name = "tbore"
    PLATE = (75.0, 75.0, 30.0)
    BORE = 20.0

    def __init__(self, opening=True):
        self._opening = opening

    def compute(self, values):
        l, w, t = self.PLATE
        pts = [(-l / 2, -w / 2), (l / 2, -w / 2), (l / 2, w / 2), (-l / 2, w / 2)]
        return {"specs": [_DeclaredSpec(
            "hex", [App.Placement()], pts, t, [(0.0, 0.0, self.BORE)],
            grounded=True, openings=["hex_bore1"] if self._opening else [])]}

    def from_spec(self, spec):
        return spec.solid()

    def profile(self, spec):
        return None

    def defaults(self):
        return {}


class _Dimensioned:
    """a part that hands over a solid and dimensions circles on its drawing.

    the declared path cannot get this wrong - the dimension is read back out of
    the bore, so there is nothing to disagree with. a part supplying its own
    solid still names circles separately from cutting them, which is the gap
    R3.1.2 is about: drainage on the drawing and none in the wood."""

    name = "tdim"
    PLATE = (60.0, 60.0, 20.0)
    BORE = 10.0

    def __init__(self, bored=True):
        self._bored = bored

    def compute(self, values):
        spec = _StackSpec("plate", [App.Placement()], grounded=True)
        spec.dimension_circles = [(0.0, 0.0, self.BORE)]
        return {"specs": [spec]}

    def from_spec(self, spec):
        l, w, t = self.PLATE
        plate = Part.makeBox(l, w, t, V(-l / 2.0, -w / 2.0, -t / 2.0))
        if not self._bored:
            return plate
        return plate.cut(Part.makeCylinder(self.BORE / 2.0, t + 2.0,
                                           V(0, 0, -t / 2.0 - 1.0)))

    def profile(self, spec):
        return None

    def defaults(self):
        return {}


def _undrilled_checks():
    """R3.1.2: a circle on the drawing that is not in the solid.

    only a part that names its circles apart from cutting them can fail this,
    which since the geometry moved into the feature tree means a part supplying
    its own `solid`. it is still exactly the planter's case."""
    out = []
    clean = build_assembly.find_undrilled(_Dimensioned(), {})
    out.append(("a dimensioned circle that is bored is clean (%s)"
                % (clean or "clean",), not clean))
    bad = build_assembly.find_undrilled(_Dimensioned(bored=False), {})
    out.append(("one dimensioned and never bored is caught (%s)" % (bad,),
                [n for n, _, _ in bad] == ["plate"]))
    return out


def _opening_checks():
    """a cut the project calls an opening is not material gone astray.

    the void check asks whether a part gave up material nothing fills, which is
    exactly right for joinery and exactly wrong for a bore that is the point of
    the part. the geometry cannot tell them apart - both are a subtractive
    feature - so `openings` is the project saying which. the same bore, not
    declared an opening, is still caught."""
    out = []
    void = build_assembly.find_voids(_Bored(), {})
    out.append(("a bore declared an opening is not an unfilled void (%s)"
                % (void or "clean",), not void))
    undeclared = build_assembly.find_voids(_Bored(opening=False), {})
    out.append(("the same bore not declared one is still caught (%s)"
                % ([n for n, _, _ in undeclared],),
                [n for n, _, _ in undeclared] == ["hex"]))
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
