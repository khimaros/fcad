"""end-to-end test for CONSTRAINTS and the parameter sweep (fcad.optimize).

a parametric model's cut list is a step function of its dimensions, so the
cheaper size is found by trying sizes, not by reasoning about geometry. the
danger is what an optimiser does when left to itself: it will find the corner
where the model silently stops meaning what it said -- a dimension clamped
against the stack beneath it, a member collapsing to nothing -- and report it as
a saving, because from the outside there genuinely are fewer boards. a
hand-rolled version of this sweep once "saved" three boards by quietly shrinking
a planter's soil bed by 50 mm.

so the two are one feature and are tested as one: the sweep must find the
cheaper size, and must refuse the candidates a project's own predicates reject.

`fcad.optimize` is pure stdlib and never touches the kernel, but the suite runs
under freecadcmd like everything else. results go to $RESULT_FILE.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import optimize
from fcad.project import Project

FT = 304.8
STOCK = {"bar": [8 * FT]}          # one buyable length, 2438.4 mm


class _Spec:
    """a bar whose cut length follows `size`, four of them."""

    def __init__(self, length, qty=4):
        self.name = "bar"
        self.profile = "bar"
        self.length = length
        self.placements = [None] * qty
        self.embeds = False


def _compute(values):
    # `size` is the cut length; `waste` is a decoy parameter that changes
    # nothing, so a sweep over it must find nothing to prefer.
    return {"specs": [_Spec(values["size"])],
            "fits": values["size"] <= 800.0}


def _project(constraints=()):
    return Project(name="topt", params={"size": 800.0, "spare": 0.0},
                   compute=_compute, stock=STOCK, constraints=list(constraints))


def main():
    checks = []

    def check(name, ok):
        checks.append((name, bool(ok)))

    # three 800 mm bars fit one 2438.4 board; four need two. at 600 all four fit
    # one, so the sweep should prefer it and say so.
    p = _project()
    check("the shipped size costs 2 boards",
          optimize.cost(p, {"size": 800.0})[0] == 2)
    check("a shorter bar costs 1", optimize.cost(p, {"size": 600.0})[0] == 1)

    results, skipped, warning = optimize.sweep(p, ["size"], 200.0, 100.0,
                                               objective="boards")
    best = results[0]
    check("the sweep prefers the size that packs (%s)" % best["label"],
          best["boards"] == 1 and best["values"]["size"] < 800.0)
    check("the baseline is still in the results",
          any(r["label"] == optimize.BASELINE for r in results))
    check("a project with no constraints is warned about", warning is not None)
    check("... and the warning says why", "clamping" in (warning or "")
          or "CONSTRAINTS" in (warning or ""))

    # now the same sweep on a project that says what "still valid" means. the
    # cheap sizes are exactly the ones it rejects, so the sweep must come back
    # with nothing better than the baseline rather than a saving it cannot have.
    strict = _project([lambda v, d: (d["fits"] and v["size"] >= 800.0)
                       or "the bar no longer reaches"])
    results, skipped, warning = optimize.sweep(strict, ["size"], 200.0, 100.0,
                                               objective="boards")
    # the grid is 600..1000 in five steps; the predicate wants exactly 800, so
    # the two cheap sizes below it and the two that no longer fit are all out.
    check("constraints are enforced during the sweep (%d skipped)" % skipped,
          skipped == 4 and len(results) == 1)
    check("no rejected candidate survives into the results",
          all(r["values"]["size"] >= 800.0 for r in results))
    check("a constrained project draws no warning", warning is None)

    # and the predicates report themselves at the shipped values too.
    check("violations names what broke",
          strict.violations({"size": 500.0}) == ["the bar no longer reaches"])
    check("a satisfied model reports nothing",
          strict.violations({"size": 800.0}) == [])

    # a predicate that raises is a failure, not a pass: a broken rule must never
    # read as a satisfied one.
    def _boom(v, d):
        raise ValueError("bad rule")

    check("a raising predicate is reported, not swallowed",
          len(_project([_boom]).violations({"size": 800.0})) == 1)

    # ranking follows the objective, because board count and purchased length
    # genuinely disagree and neither is "the" answer.
    check("an unknown parameter is refused", _raises(
        lambda: optimize.sweep(p, ["nope"], 10.0, 10.0), KeyError))
    check("a non-numeric parameter is refused", _raises(
        lambda: optimize.sweep(_strings(), ["mode"], 10.0, 10.0), TypeError))

    # the report always shows the baseline, so "nothing beat it" is legible.
    lines = optimize.report(*optimize.sweep(strict, ["size"], 100.0, 100.0,
                                            objective="boards"))
    check("the report names the baseline",
          any(optimize.BASELINE in l for l in lines))

    return report(checks)


def _strings():
    return Project(name="tstr", params={"mode": "wide"}, compute=_compute,
                   stock=STOCK)


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


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


if any(a.endswith("test_optimize.py") for a in sys.argv):
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        raise SystemExit(report([(traceback.format_exc(), False)]))
