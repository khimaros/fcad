"""pack a bom's cut lengths into purchasable stock: the cut-list optimizer.

the bom answers "how many pieces of what length". this answers "what do i buy":
1d bin packing where a saw kerf is lost between adjacent cuts, an optional trim
allowance comes off each board, and stock exists only in the discrete lengths a
supplier sells.

which lengths those are is market knowledge, not fcad's, so they arrive from the
project (a `STOCK` global) or per invocation (`--stock`). the module is pure
stdlib and imports no FreeCAD, so the same code runs under the cli and inside
freecadcmd.

two solvers: an exact dp over the demand vector while the search is small enough
to be honest about, and first-fit-decreasing above that. `Plan.exact` says which
one ran, so a plan never claims optimality it did not prove.
"""

import itertools
import os
from dataclasses import dataclass, field

DEFAULT_KERF = 3.0        # mm of material the blade eats between adjacent cuts
DEFAULT_TRIM = 0.0        # mm docked off each board before any cut is laid out
DEFAULT_OBJECTIVE = "length"   # or "boards"

# the exact search is a dp over the demand vector, so it costs one visit per
# (state, cut pattern) pair. past any of these budgets it gives way to greedy.
MAX_STATES = 100000
MAX_PATTERNS = 4000
MAX_WORK = 5000000

EPS = 1e-6

# a stock length may carry a unit; a bare number is mm (fcad is metric
# throughout). longest suffix first, so "mm" is not read as "m".
UNITS = (("mm", 1.0), ("cm", 10.0), ("ft", 304.8), ("in", 25.4), ("m", 1000.0))

# the cli exports its cutlist flags for the in-freecad build to read back.
ENV_STOCK = "FCAD_STOCK"
ENV_KERF = "FCAD_KERF"
ENV_TRIM = "FCAD_TRIM"
ENV_OBJECTIVE = "FCAD_CUT_OBJECTIVE"

CSV_HEADER = ["profile", "pattern", "stock_mm", "boards",
              "cut_mm", "per_board", "offcut_mm"]


@dataclass
class Board:
    """one purchased length and the cuts taken from it, `count` boards over."""
    stock: float
    cuts: tuple        # ((cut length, pieces per board), ...), longest first
    offcut: float
    count: int = 1


@dataclass
class Plan:
    """what to buy for one profile, and what to cut from each board."""
    boards: list = field(default_factory=list)
    demand: dict = field(default_factory=dict)
    unpacked: dict = field(default_factory=dict)   # longer than any stock
    exact: bool = True                             # False => greedy fallback
    kerf: float = DEFAULT_KERF
    trim: float = DEFAULT_TRIM

    @property
    def board_count(self):
        return sum(b.count for b in self.boards)

    @property
    def bought(self):
        return sum(b.stock * b.count for b in self.boards)

    @property
    def cut_total(self):
        """total length actually cut (pieces no stock can hold are excluded)."""
        return sum(l * q for l, q in self.demand.items() if l not in self.unpacked)

    @property
    def waste(self):
        return self.bought - self.cut_total


def _used(cuts, kerf):
    """board length a cut pattern consumes, kerf between adjacent cuts."""
    n = sum(c for _, c in cuts)
    return 0.0 if not n else sum(l * c for l, c in cuts) + (n - 1) * kerf


class _TooMany(Exception):
    """the pattern enumeration outgrew MAX_PATTERNS."""


def _patterns(lengths, capacity, caps, kerf):
    """every cut pattern fitting `capacity`, each count capped by the demand.

    a pattern is a tuple parallel to `lengths`. returns None when enumerating
    them all would outgrow MAX_PATTERNS (the caller falls back to greedy)."""
    out = []

    def rec(i, counts, rem, n):
        if len(out) > MAX_PATTERNS:
            raise _TooMany
        if i == len(lengths):
            if n:
                out.append(tuple(counts))
            return
        rec(i + 1, counts + [0], rem, n)
        length, r, k = lengths[i], rem, 0
        while k < caps[i]:
            cost = length if n + k == 0 else kerf + length
            if cost > r + EPS:
                break
            r -= cost
            k += 1
            rec(i + 1, counts + [k], r, n + k)

    try:
        rec(0, [], capacity, 0)
    except _TooMany:
        return None
    return out


def _options(lengths, caps, stock, kerf, trim):
    """(pattern, stock length) pairs across every stock length, or None."""
    opts = []
    for s in stock:
        pats = _patterns(lengths, s - trim, caps, kerf)
        if pats is None or len(opts) + len(pats) > MAX_PATTERNS:
            return None
        opts.extend((p, s) for p in pats)
    return opts


def _exact(lengths, demand, stock, kerf, trim, objective):
    """fewest-cost boards by dp over the demand vector, or None if too big.

    states are visited in increasing total pieces, so every successor state a
    pattern leads to (strictly smaller, since a pattern cuts at least one piece)
    is already solved when it is read. every state is solvable: no piece outlives
    the longest stock by the time we get here, so the one-piece pattern for a
    state's largest outstanding length is always among the options."""
    caps = [demand[l] for l in lengths]
    states = 1
    for c in caps:
        states *= c + 1
        if states > MAX_STATES:
            return None
    opts = _options(lengths, caps, stock, kerf, trim)
    if not opts or states * len(opts) > MAX_WORK:
        return None

    def cost_key(bought, boards):
        return (boards, bought) if objective == "boards" else (bought, boards)

    best = {}   # state -> (bought, boards, chosen option, successor state)
    for state in sorted(itertools.product(*(range(c + 1) for c in caps)), key=sum):
        if not any(state):
            best[state] = (0.0, 0, None, None)
            continue
        pick = None
        for pat, s in opts:
            nxt = tuple(max(0, a - b) for a, b in zip(state, pat))
            if nxt == state:
                continue
            bought, boards = best[nxt][0] + s, best[nxt][1] + 1
            key = cost_key(bought, boards)
            if pick is None or key < pick[0]:
                pick = (key, bought, boards, (pat, s), nxt)
        best[state] = pick[1:]

    state, out = tuple(caps), []
    while any(state):
        _, _, (pat, s), nxt = best[state]
        cuts = tuple((l, n) for l, n in zip(lengths, pat) if n)
        out.append((s, cuts, (s - trim) - _used(cuts, kerf)))
        state = nxt
    return out


def _fill(capacity, rem, kerf):
    """first-fit-decreasing: take the longest piece that still fits, repeat."""
    cuts, cap, n = {}, capacity, 0
    for length in sorted(rem, reverse=True):
        for _ in range(rem[length]):
            cost = length if n == 0 else kerf + length
            if cost > cap + EPS:
                break
            cap -= cost
            n += 1
            cuts[length] = cuts.get(length, 0) + 1
    return tuple(sorted(cuts.items(), reverse=True)), capacity - cap


def _greedy(demand, stock, kerf, trim):
    """board at a time, first-fit-decreasing onto whichever stock wastes least."""
    rem = dict(demand)
    out = []
    while any(rem.values()):
        pick = None
        for s in stock:
            cuts, used = _fill(s - trim, rem, kerf)
            if not cuts:
                continue
            key = ((s - trim) - used, s)   # least offcut, then shortest stock
            if pick is None or key < pick[0]:
                pick = (key, s, cuts)
        if pick is None:
            break
        _, s, cuts = pick
        for length, n in cuts:
            rem[length] -= n
        out.append((s, cuts, (s - trim) - _used(cuts, kerf)))
    return out


def _merge(raw):
    """collapse identical boards into one entry carrying a count."""
    seen = {}
    for s, cuts, offcut in raw:
        key = (s, cuts)
        if key in seen:
            seen[key].count += 1
        else:
            seen[key] = Board(s, cuts, offcut, 1)
    return sorted(seen.values(),
                  key=lambda b: (-b.stock, -sum(l * n for l, n in b.cuts)))


def plan(demand, stock, kerf=DEFAULT_KERF, trim=DEFAULT_TRIM,
         objective=DEFAULT_OBJECTIVE):
    """the cheapest set of stock boards yielding `demand` ({length: qty}).

    "cheapest" is least total purchased length (ties broken on board count), or
    the reverse under objective="boards"."""
    items = demand.items() if isinstance(demand, dict) else demand
    demand = {}
    for length, qty in items:
        if qty > 0:
            demand[float(length)] = demand.get(float(length), 0) + int(qty)
    stock = sorted({float(s) for s in stock})
    if not stock:
        return Plan(demand=demand, unpacked=dict(demand), kerf=kerf, trim=trim)

    longest = stock[-1] - trim
    unpacked = {l: q for l, q in demand.items() if l > longest + EPS}
    packable = {l: q for l, q in demand.items() if l not in unpacked}
    exact = True
    raw = []
    if packable:
        lengths = sorted(packable, reverse=True)
        raw = _exact(lengths, packable, stock, kerf, trim, objective)
        if raw is None:
            raw, exact = _greedy(packable, stock, kerf, trim), False
    return Plan(_merge(raw), demand, unpacked, exact, kerf, trim)


# ---------------------------------------------------------------------------
# stock specs, cli/env plumbing, and reporting
# ---------------------------------------------------------------------------

def _length(token):
    token = token.strip().lower()
    for suffix, factor in UNITS:
        if token.endswith(suffix):
            return round(float(token[:-len(suffix)]) * factor, 6)
    return float(token)


def parse_stock(text):
    """a stock spec -> [lengths mm], or {profile: [lengths mm]}.

    comma-separated values, mm unless suffixed (mm/cm/m/in/ft). a `profile=...`
    entry scopes its list to one bom profile; several are separated by ';'
    (a list of specs, as --stock is repeatable, is joined the same way)."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = ";".join(str(t) for t in text)
    plain, by_profile = [], {}
    for entry in (e.strip() for e in text.split(";")):
        if not entry:
            continue
        profile, sep, values = entry.partition("=")
        lengths = [_length(v) for v in values.split(",") if v.strip()] if sep else \
                  [_length(v) for v in entry.split(",") if v.strip()]
        if sep:
            by_profile[profile.strip()] = lengths
        else:
            plain.extend(lengths)
    if by_profile:
        if plain:
            by_profile["*"] = plain
        return by_profile
    return plain


def _length_of(entry):
    """a stock entry's length, whether or not it carries a price.

    an entry is either a bare length or `(length, price)`, so a project can
    price its stock without changing anything that only wants the lengths."""
    if isinstance(entry, (tuple, list)):
        return float(entry[0])
    return float(entry)


def _price_of(entry):
    return float(entry[1]) if isinstance(entry, (tuple, list)) and len(entry) > 1 \
        else None


def lengths_for(stock, profile):
    """stock lengths declared for one bom profile.

    a dict is looked up by profile name, with "*" as the catch-all entry; a bare
    list applies to every profile. prices, where an entry carries one, are
    stripped here: everything downstream plans on lengths."""
    return [_length_of(e) for e in _entries_for(stock, profile)]


def _entries_for(stock, profile):
    """the raw stock entries for a profile, prices intact."""
    if not stock:
        return []
    if isinstance(stock, dict):
        return list(stock.get(profile) or stock.get("*") or [])
    return list(stock)


def prices_for(stock, profile):
    """{length: price} for a profile, empty when the project prices nothing."""
    out = {}
    for entry in _entries_for(stock, profile):
        price = _price_of(entry)
        if price is not None:
            out[_length_of(entry)] = price
    return out


def resolve_lengths(declared, override, profile):
    """the stock lengths to plan `profile` against.

    the project's STOCK decides *which* profiles are cut from stock at all - a
    fastener declares none and gets no plan - while an override only changes the
    lengths. so a bare `--stock` list re-lengths every declared profile, and an
    explicit `profile=...` entry additionally opts that profile in."""
    if override is None:
        return declared
    if isinstance(override, dict):
        named = override.get(profile)
        if named:
            return list(named)
        blanket = override.get("*")
        return list(blanket) if blanket and declared else declared
    return list(override) if declared else []


def options_from_env(env=None):
    """the cutlist options the cli exported for this build (all optional)."""
    env = os.environ if env is None else env

    def num(key, default):
        raw = env.get(key)
        return float(raw) if raw else default

    return {"stock": parse_stock(env.get(ENV_STOCK) or None),
            "kerf": num(ENV_KERF, DEFAULT_KERF),
            "trim": num(ENV_TRIM, DEFAULT_TRIM),
            "objective": env.get(ENV_OBJECTIVE) or DEFAULT_OBJECTIVE}


def rows(profile, plan_):
    """flat csv rows for one profile: one per (cut pattern, cut length)."""
    out = []
    for i, b in enumerate(plan_.boards, 1):
        for length, n in b.cuts:
            out.append([profile, i, round(b.stock, 1), b.count,
                        round(length, 1), n, round(b.offcut, 1)])
    return out


def summary(profile, plan_):
    """the printable one-liner for a profile, plus a line per unbuyable piece."""
    buy = {}
    for b in plan_.boards:
        buy[b.stock] = buy.get(b.stock, 0) + b.count
    lines = []
    if buy:
        pct = 100.0 * plan_.waste / plan_.bought if plan_.bought else 0.0
        lines.append("  %s: buy %s mm = %d board(s), %.1f%% waste%s"
                     % (profile,
                        " + ".join("%d x %g" % (n, s)
                                   for s, n in sorted(buy.items(), reverse=True)),
                        plan_.board_count, pct, "" if plan_.exact else " (greedy)"))
    for length, qty in sorted(plan_.unpacked.items(), reverse=True):
        lines.append("  %s: WARNING %d x %g mm exceeds every stock length"
                     % (profile, qty, length))
    return lines


def totals(plans, prices=None):
    """the line a person actually wants: what the whole model costs to buy.

    per-profile summaries answer "how do I cut the 2x6"; nobody's shopping list
    is one profile. purchased length leads because board count is only a proxy
    for cost -- a 16 ft board is not one 8 ft board -- and waste *percentage* is
    worse than either: a model can buy less total timber at a higher percentage.

    `plans` is {profile: Plan}; `prices` the optional {profile: {length: price}}
    a priced `STOCK` yields."""
    boards = sum(p.board_count for p in plans.values())
    bought = sum(p.bought for p in plans.values())
    waste = sum(p.waste for p in plans.values())
    pct = 100.0 * waste / bought if bought else 0.0
    line = ("  total: %d board(s), %.0f mm purchased, %.0f mm waste (%.1f%%)"
            % (boards, bought, waste, pct))
    money = 0.0
    priced = True
    for profile, plan_ in plans.items():
        table = (prices or {}).get(profile) or {}
        for b in plan_.boards:
            if b.stock in table:
                money += table[b.stock] * b.count
            else:
                priced = False
    if priced and money:
        line += ", %.2f to buy" % money
    return [line]
