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
        if not project.stock_for(spec.profile, values):
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
        plan = cutlist.plan(lengths, project.stock_for(profile, values), **opts)
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


# fallback densities (kg/m^3) when a part omits explicit `mass_g`.
DEFAULT_RHO = {
    "steel": 7850.0, "aluminum": 2700.0, "wood": 500.0,
    "softwood": 450.0, "hardwood": 700.0,
}


def _density(project, spec):
    """density (kg/m^3) from spec/project material declarations."""
    mat = getattr(spec, "material", None) or getattr(project, "material", None)
    table = dict(DEFAULT_RHO)
    for k, v in (getattr(project, "materials", None) or {}).items():
        if isinstance(v, dict) and "rho" in v:
            table[k.lower()] = float(v["rho"])
    if isinstance(mat, dict) and "rho" in mat:
        return float(mat["rho"])
    if isinstance(mat, str):
        return table.get(mat.lower(), 7850.0)
    return 7850.0


def price_of(project, specs, kerf=None, trim=None, objective=None, values=None):
    """total purchase price across 1D stock, 2D sheet stock, and COTS unit costs."""
    opts = {}
    for name, val in (("kerf", kerf), ("trim", trim)):
        if val is not None:
            opts[name] = val
    if objective in ("length", "boards"):
        opts["objective"] = objective
    stock = project.resolve_stock(values)
    total = 0.0
    # 1D stock
    want_1d = {}
    for spec in specs:
        if project.stock_for(spec.profile, values):
            key = (spec.profile, round(float(spec.length), 3))
            want_1d[key] = want_1d.get(key, 0) + len(spec.placements)
    for prof in sorted({p for p, _ in want_1d}):
        lengths = {l: q for (p, l), q in want_1d.items() if p == prof}
        p1 = cutlist.plan(lengths, project.stock_for(prof, values), **opts)
        ptab = cutlist.prices_for(stock, prof)
        for b in p1.boards:
            total += ptab.get(b.stock, 0.0) * b.count
    # 2D sheet stock
    want_2d = {}
    for spec in specs:
        dims = getattr(spec, "sheet_dims", None)
        if dims is not None and project.sheets_for(spec.profile, values):
            key = (spec.profile, (round(dims[0], 1), round(dims[1], 1)))
            want_2d[key] = want_2d.get(key, 0) + len(spec.placements)
    sopts = {k: opts[k] for k in ("kerf", "trim") if k in opts}
    for prof in sorted({p for p, _ in want_2d}):
        rects = {d: q for (p, d), q in want_2d.items() if p == prof}
        p2 = cutlist.plan_sheets(rects, project.sheets_for(prof, values), **sopts)
        ptab = cutlist.sheet_prices_for(stock, prof)
        for sh in p2.sheets:
            total += ptab.get((float(sh.stock_w), float(sh.stock_h)), 0.0) * sh.count
    # COTS / per-part unit cost
    for spec in specs:
        uc = getattr(spec, "unit_cost", None)
        if uc is not None:
            total += float(uc) * len(spec.placements)
    return round(total, 4)


def mass_of(project, specs):
    """total mass (grams) using explicit `mass_g` or `solid.Volume * density`."""
    total = 0.0
    for spec in specs:
        qty = len(spec.placements)
        if not qty:
            continue
        m = getattr(spec, "mass_g", None)
        if m is not None:
            total += float(m) * qty
        else:
            rho = _density(project, spec)
            vol = project.from_spec(spec).Volume   # mm^3
            total += vol * rho * 1e-6 * qty
    return round(total, 3)


def sweep(project, names, span, step, values=None, kerf=None, trim=None,
          objective=None):
    """rank nearby parameter sets by stock length, boards, price, or mass.

    returns (results, skipped, warning). `skipped` counts candidates rejected
    by `CONSTRAINTS`."""
    base = dict(values or project.defaults())
    for name in names:
        if name not in base:
            raise KeyError("no such parameter: %s" % name)
        if not isinstance(base[name], (int, float)) or isinstance(base[name], bool):
            raise TypeError("%s is not numeric, so it cannot be swept" % name)

    warnings = []
    if not project.constraints:
        warnings.append(
            "this project declares no CONSTRAINTS, so nothing here checked that "
            "a cheaper model still means what it said. a sweep will happily buy "
            "a saving by clamping a dimension or dropping a member.")
    try:
        base_specs = project.compute(base)["specs"]
    except Exception:
        base_specs = []
    no_len = [s.name for s in base_specs
              if project.stock_for(s.profile, base)
              and hasattr(s, "_length") and s._length is None]
    if no_len:
        warnings.append(
            "stock part(s) %s omit explicit `length=`; sweeping falls back to "
            "building geometry per candidate" % ", ".join(no_len))
    warning = "; ".join(warnings) if warnings else None

    cut_obj = objective if objective in ("length", "boards") else None
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
                                                cut_obj)
            row = {"values": {n: candidate[n] for n in names},
                   "label": label, "boards": boards, "bought": bought,
                   "waste": waste, "exact": exact}
            if objective == "price":
                row["price"] = price_of(project, data["specs"], kerf, trim,
                                        cut_obj, values=candidate)
            elif objective == "mass":
                row["mass_g"] = mass_of(project, data["specs"])
        except Exception:
            skipped += 1
            continue
        results.append(row)
    if objective == "price":
        results.sort(key=lambda r: (r["price"], r["bought"], r["boards"]))
    elif objective == "mass":
        results.sort(key=lambda r: (r["mass_g"], r["bought"], r["boards"]))
    elif objective == "boards":
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
    has_price = "price" in results[0]
    has_mass = "mass_g" in results[0]
    if has_price:
        lines.append("%-34s %9s %7s %11s" % ("moved", "price", "boards", "bought_mm"))
    elif has_mass:
        lines.append("%-34s %10s %7s %11s" % ("moved", "mass_g", "boards", "bought_mm"))
    else:
        lines.append("%-34s %7s %11s %11s" % ("moved", "boards", "bought_mm",
                                              "waste_mm"))
    shown = results[:limit]
    if base is not None and base not in shown:
        shown = shown + [base]
    for r in shown:
        if has_price:
            delta = ("" if base is None or r is base else
                     "  (%+.2f)" % (r["price"] - base["price"]))
            lines.append("%-34s %9.2f %7d %11.0f%s"
                         % (r["label"][:34], r["price"], r["boards"],
                            r["bought"], delta))
        elif has_mass:
            delta = ("" if base is None or r is base else
                     "  (%+.1f g)" % (r["mass_g"] - base["mass_g"]))
            lines.append("%-34s %10.1f %7d %11.0f%s"
                         % (r["label"][:34], r["mass_g"], r["boards"],
                            r["bought"], delta))
        else:
            delta = ""
            if base is not None and r is not base:
                delta = "  (%+d boards, %+.0f mm)" % (
                    r["boards"] - base["boards"], r["bought"] - base["bought"])
            lines.append("%-34s %7d %11.0f %11.0f%s%s"
                         % (r["label"][:34], r["boards"], r["bought"],
                            r["waste"], delta, "" if r["exact"] else "  (greedy)"))
    if skipped:
        lines.append("%d candidate(s) rejected by CONSTRAINTS or failed to "
                     "compute" % skipped)
    return lines
