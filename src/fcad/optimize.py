"""search nearby parameter values for a model that takes less lumber.

a parametric model's cut list is a step function of its dimensions: a member
that grows 10 mm can drop a whole board out of the plan, or add one, and nothing
about staring at the geometry tells you which. this sweeps the parameters you
name over a grid, packs the real bom for each, and ranks what it finds.

**it is only safe because of `CONSTRAINTS`.** an optimiser over a parametric
model will always find the corner where the model silently stops meaning what it
said -- a depth clamped against the stack beneath it, a count collapsing to
zero -- and report it as a saving, because from the outside it *is* fewer
boards. the first hand-rolled version of this sweep "saved" three boards by
quietly shrinking a planter's soil bed from 400 mm to 350. so a project that
declares no constraints gets a warning, and the ones it does declare are checked
for every candidate before its plan is costed.

pure stdlib: it drives `project.compute` and `fcad.cutlist`, and never touches
the kernel, so the whole sweep runs in one process.
"""

from fcad import cutlist

# a candidate is described by the parameters that moved, so a sweep that changes
# nothing still names itself.
BASELINE = "baseline"


def demand(project, values):
    """{(profile, length): qty} for one parameter set, stock profiles only.

    a profile absent from `STOCK` is not cut from stock at all (fasteners,
    bought parts), and counting it would make every plan look worse than it is."""
    out = {}
    for spec in project.compute(values)["specs"]:
        if not project.stock_for(spec.profile):
            continue
        key = (spec.profile, round(float(spec.length), 3))
        out[key] = out.get(key, 0) + len(spec.placements)
    return out


def cost(project, values, kerf=None, trim=None, objective=None):
    """(boards, bought_mm, waste_mm, exact) for one parameter set."""
    want = demand(project, values)
    opts = {}
    for name, val in (("kerf", kerf), ("trim", trim), ("objective", objective)):
        if val is not None:
            opts[name] = val
    boards, bought, waste, exact = 0, 0.0, 0.0, True
    for profile in sorted({p for p, _ in want}):
        lengths = {l: q for (p, l), q in want.items() if p == profile}
        plan = cutlist.plan(lengths, project.stock_for(profile), **opts)
        boards += plan.board_count
        bought += plan.bought
        waste += plan.waste
        exact = exact and plan.exact
    return boards, bought, waste, exact


def grid(base, names, span, step):
    """every parameter set within +-`span` of `base` on a `step` grid.

    yields (values, label). the baseline comes first so a caller can report it
    even when nothing beats it."""
    n = int(round(span / step))
    offsets = [i * step for i in range(-n, n + 1)]

    def walk(i, values, moved):
        if i == len(names):
            yield dict(values), ", ".join(moved) if moved else BASELINE
            return
        name = names[i]
        for d in offsets:
            values[name] = base[name] + d
            label = "%s=%g" % (name, values[name])
            yield from walk(i + 1, values, moved + ([label] if d else []))

    yield from walk(0, dict(base), [])


def sweep(project, names, span, step, values=None, kerf=None, trim=None,
          objective=None):
    """rank nearby parameter sets by what they cost in stock.

    returns (results, skipped, warning). a result is a dict with the parameter
    values, the label of what moved, and the plan's boards/bought/waste.

    ranked by `objective`, which is the same choice the cut list itself offers
    and matters more here than it does there: total purchased length and board
    count genuinely disagree. on one real model the fewest-boards answer (16)
    buys 3.6 m *more* timber than a 17-board plan, because a board is not a unit
    of cost -- a 16 ft length is not one 8 ft length. length is the default for
    that reason.

    `skipped` counts candidates a constraint rejected, and `warning` is set when
    the project declares no constraints at all -- in which case every number
    here is suspect, because nothing stopped the search degrading the model."""
    base = dict(values or project.defaults())
    for name in names:
        if name not in base:
            raise KeyError("no such parameter: %s" % name)
        if not isinstance(base[name], (int, float)) or isinstance(base[name], bool):
            raise TypeError("%s is not numeric, so it cannot be swept" % name)

    warning = None
    if not project.constraints:
        warning = ("this project declares no CONSTRAINTS, so nothing here "
                   "checked that a cheaper model still means what it said. a "
                   "sweep will happily buy a saving by clamping a dimension or "
                   "dropping a member.")

    results, skipped = [], 0
    for candidate, label in grid(base, names, span, step):
        try:
            data = project.compute(candidate)
        except Exception:
            skipped += 1
            continue
        if project.violations(candidate, data):
            skipped += 1
            continue
        try:
            boards, bought, waste, exact = cost(project, candidate, kerf, trim,
                                                objective)
        except Exception:
            skipped += 1
            continue
        results.append({"values": {n: candidate[n] for n in names},
                        "label": label, "boards": boards, "bought": bought,
                        "waste": waste, "exact": exact})
    if objective == "boards":
        results.sort(key=lambda r: (r["boards"], r["bought"]))
    else:
        results.sort(key=lambda r: (r["bought"], r["boards"]))
    return results, skipped, warning


def report(results, skipped, warning, base_label=BASELINE, limit=10):
    """the printable sweep, cheapest first, with the baseline always shown."""
    lines = []
    if warning:
        lines.append("fcad optimize: WARNING " + warning)
    if not results:
        lines.append("fcad optimize: no candidate satisfied the constraints")
        return lines
    base = next((r for r in results if r["label"] == base_label), None)
    lines.append("%-34s %7s %11s %11s" % ("moved", "boards", "bought_mm",
                                          "waste_mm"))
    shown = results[:limit]
    if base is not None and base not in shown:
        shown = shown + [base]
    for r in shown:
        delta = ""
        if base is not None and r is not base:
            delta = "  (%+d boards, %+.0f mm)" % (r["boards"] - base["boards"],
                                                  r["bought"] - base["bought"])
        lines.append("%-34s %7d %11.0f %11.0f%s%s"
                     % (r["label"][:34], r["boards"], r["bought"], r["waste"],
                        delta, "" if r["exact"] else "  (greedy)"))
    if skipped:
        lines.append("%d candidate(s) rejected by CONSTRAINTS or failed to "
                     "compute" % skipped)
    return lines
