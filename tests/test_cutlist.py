"""end-to-end test for the cut-list optimizer (`fcad build cutlist`).

the bom says how many pieces of what length; the cut list says what to buy. this
writes a single-file project declaring STOCK, builds the bom + cutlist through
the real assembly builder, and asserts the plan against a fixture whose optimum
is unique and hand-checkable. it then exercises the pieces the build path cannot
reach on its own: the --stock/--kerf env overrides, unit-suffixed stock specs,
kerf accounting, oversize pieces, and the greedy fallback for demands too large
to search exactly.

needs FreeCAD: run with `freecadcmd tests/test_cutlist.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel).
"""

import csv
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import config, cutlist  # noqa: E402
from fcad.freecad import build_assembly  # noqa: E402

# the fixture's 2x4 demand is 3 x 700 + 2 x 400 against 1000/1500 stock. its
# optimum is unique: a 1500 must take 2 x 700 (1 x 700 + 1 x 400 there strands a
# 400), leaving 1000s for the last 700 and the pair of 400s. 3500 mm bought, and
# no cheaper multiset of 1000s/1500s can hold 2900 mm of cuts plus their kerfs.
PROJECT_PY = '''
import fcad
import Part, FreeCAD as App

PARAMS = {"rail_len": 700.0, "stile_len": 400.0}
STOCK = {"2x4": [1000.0, 1500.0]}

def compute(p):
    r, s = p["rail_len"], p["stile_len"]
    box = lambda l: (lambda: Part.makeBox(l, 50, 50))
    return [
        fcad.PartSpec("rail", placements=[App.Placement()] * 3, length=r,
                      profile="2x4", solid=box(r)),
        fcad.PartSpec("stile", placements=[App.Placement()] * 2, length=s,
                      profile="2x4", solid=box(s)),
        # no stock declared for this profile: the cut list must skip it.
        fcad.PartSpec("pin", placements=[App.Placement()] * 10, length=50.0,
                      profile="dowel", solid=box(50.0)),
    ]
'''

EXPECTED = {(1500.0, ((700.0, 2),)): 1,
            (1000.0, ((700.0, 1),)): 1,
            (1000.0, ((400.0, 2),)): 1}


def _load(proj_dir):
    os.environ[config.ENV_PROJECT] = proj_dir
    os.environ[config.ENV_NAME] = "tcut"
    os.environ.pop(config.ENV_DIST, None)
    from fcad.loader import load_project
    return load_project()


def _rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def _boards(rows):
    """csv rows -> {(stock, ((cut, per_board), ...)): boards}."""
    out = {}
    for r in rows:
        key = (r["profile"], int(r["pattern"]))
        out.setdefault(key, []).append(r)
    plan = {}
    for (_, _), group in sorted(out.items()):
        cuts = tuple(sorted((float(r["cut_mm"]), int(r["per_board"])) for r in group))
        plan[(float(group[0]["stock_mm"]), cuts)] = int(group[0]["boards"])
    return plan


def _conserves(plan, demand, kerf):
    """every demanded piece is cut exactly once, and no board is overfilled."""
    got = {}
    for b in plan.boards:
        used = sum(l * n for l, n in b.cuts) + (sum(n for _, n in b.cuts) - 1) * kerf
        if used > b.stock + 1e-6:
            return False
        for length, n in b.cuts:
            got[length] = got.get(length, 0) + n * b.count
    return got == {l: q for l, q in demand.items() if q}


def main():
    checks = []

    def ck(name, ok):
        checks.append((name, bool(ok)))

    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "project.py"), "w") as f:
            f.write(PROJECT_PY)
        project = _load(tmp)
        ck("STOCK global reaches the Project", project.stock_for("2x4") == [1000.0, 1500.0])
        ck("undeclared profile has no stock", project.stock_for("dowel") == [])

        from fcad.freecad import dispatch
        d = dispatch.dirs(project)
        vals = project.defaults()
        build_assembly.build(project, vals, d, {"bom", "cutlist"})

        path = os.path.join(d["dist"], project.name + "-cutlist.csv")
        ck("cutlist csv written", os.path.exists(path))
        rows = _rows(path)
        ck("profile without stock is skipped",
           not [r for r in rows if r["profile"] == "dowel"])
        ck("optimal plan (%s)" % (_boards(rows),), _boards(rows) == EXPECTED)
        ck("offcut reported", all(float(r["offcut_mm"]) >= 0 for r in rows))

        # --stock overrides the project's STOCK for what-if runs. one 2000 holds
        # 2 x 700 + 1 x 400; the rest goes on a second, so the answer must move.
        os.environ[cutlist.ENV_STOCK] = "2000"
        try:
            build_assembly.build(project, vals, d, {"cutlist"})
            over = _rows(path)
        finally:
            os.environ.pop(cutlist.ENV_STOCK)
        ck("env stock override applied",
           over and {float(r["stock_mm"]) for r in over} == {2000.0})
        ck("override plan buys 2 boards",
           sum(int(r["boards"]) for r in _boards_first(over)) == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k in ("FCAD_ENTRY", "FCAD_PROJECT", "FCAD_NAME", "FCAD_DIST"):
            os.environ.pop(k, None)

    # --- stock specs: bare mm, unit suffixes, and per-profile assignment ---
    ck("bare mm stock parsed", cutlist.parse_stock("1000,1500") == [1000.0, 1500.0])
    ck("ft/in suffixes converted",
       cutlist.parse_stock("8ft,96in") == [2438.4, 2438.4])
    ck("per-profile stock parsed",
       cutlist.parse_stock("2x6=8ft,12ft;2x4=8ft")
       == {"2x6": [2438.4, 3657.6], "2x4": [2438.4]})

    # --- kerf is real material: two 400s do not fit an 800 board with a blade ---
    tight = cutlist.plan({400.0: 2}, [800.0], kerf=0.0)
    ck("kerf 0: two 400s share an 800", len(tight.boards) == 1)
    split = cutlist.plan({400.0: 2}, [800.0], kerf=3.0)
    ck("kerf 3: they no longer fit", split.board_count == 2)
    ck("trim allowance shrinks usable stock",
       cutlist.plan({700.0: 1}, [750.0], trim=100.0).unpacked == {700.0: 1})

    # --- a piece longer than any stock is reported, not silently dropped ---
    over = cutlist.plan({5000.0: 1, 700.0: 2}, [1000.0, 1500.0])
    ck("oversize piece reported as unpacked", over.unpacked == {5000.0: 1})
    ck("the packable rest is still planned (2 x 700 share one 1500)",
       over.boards == [cutlist.Board(1500.0, ((700.0, 2),), 97.0, 1)])
    ck("unpacked length is left out of the waste figure",
       over.cut_total == 1400.0 and over.waste == 100.0)

    # --- big demand: exact search gives way to greedy, still a valid plan ---
    demand = {float(300 + 37 * i): 8 for i in range(12)}
    big = cutlist.plan(demand, [2400.0, 3000.0, 3600.0])
    ck("greedy fallback engaged", big.exact is False)
    ck("greedy plan conserves demand", _conserves(big, demand, cutlist.DEFAULT_KERF))
    ck("greedy plan has no unpacked pieces", not big.unpacked)

    small = cutlist.plan({700.0: 3, 400.0: 2}, [1000.0, 1500.0], kerf=3.0)
    ck("exact search used for a small demand", small.exact is True)
    ck("exact plan conserves demand",
       _conserves(small, {700.0: 3, 400.0: 2}, 3.0))
    ck("exact plan buys 3500 mm", small.bought == 3500.0)
    ck("waste = bought - cuts", round(small.waste, 6) == round(3500.0 - 2900.0, 6))

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


def _boards_first(rows):
    """one row per board pattern (the csv repeats the pattern's totals)."""
    seen, out = set(), []
    for r in rows:
        key = (r["profile"], r["pattern"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


if any(a.endswith("test_cutlist.py") for a in sys.argv):
    raise SystemExit(main())
