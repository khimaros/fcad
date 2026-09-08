"""end-to-end test for the assembly animation and, mostly, its ordering.

R4.3. `fcad animate --subject assemble` flies the parts in one at a time, and the
only interesting question is what "one at a time" means. a z-sort looks right on
a stack and is wrong the moment a model has fasteners in it: on the shipped
fastenplates example it drives the screw home between the two plates it holds.
so the order is a breadth-first walk of the contact graph outward from the
`grounded` part, with `embeds` parts held to the end - three things fcad already
knows, none of which needed a new affordance invented for them.

the joint graph is deliberately not the source: `build_jointed_doc` fixes every
non-grounded part to one datum, so it is a star and every part is one hop from
the anchor. it makes the assembly constrained; it does not describe contact.

runs the real renderer over a synthesized dist (two stls, placements, flags), so
what is under test is the sequencing and the writing, not FreeCAD. needs
numpy-stl + matplotlib: run with `freecadcmd tests/test_assemble.py`.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np

from fcad import config
from fcad.render import ORDERS, assemble

NAME = "rig"


def _box(lo, hi):
    """the 12 triangles of an axis-aligned box, as an (n, 3, 3) array."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
             (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return np.array([[v[a], v[b], v[c]] for a, b, c in faces], np.float32)


def _parts(spec):
    """[(label, tris)] from {label: (lo, hi)}, in the order given."""
    return [(label, _box(*box)) for label, box in spec]


# a chain that a z-sort gets wrong: c sits lowest but only touches b, and the
# anchor is a. bottom-up would start at c, which is floating.
CHAIN = [("a.0", ((0, 0, 10), (10, 10, 20))),
         ("b.0", ((0, 0, 0), (10, 10, 10))),
         ("c.0", ((0, 0, -10), (10, 10, 0)))]
CHAIN_MARKS = {"a": {"grounded": True, "embeds": False},
               "b": {"grounded": False, "embeds": False},
               "c": {"grounded": False, "embeds": False}}

# the shape of the fastenplates example: two plates and a screw through them.
# a z-sort interleaves the screw; the fastener belongs last.
FASTENED = [("plate.0", ((0, 0, 0), (40, 40, 5))),
            ("screw.0", ((18, 18, 2), (22, 22, 12))),
            ("plate.1", ((0, 0, 9), (40, 40, 14)))]
FASTENED_MARKS = {"plate": {"grounded": True, "embeds": False},
                  "screw": {"grounded": False, "embeds": True}}


def _labels(parts, how, marks):
    return [label for label, _ in assemble.order(parts, how, marks, tol=1.0)]


def _order_checks():
    """R4.3: the sequence models assembly rather than approximating it."""
    chain = _parts(CHAIN)
    fastened = _parts(FASTENED)
    return [
        ("the grounded order starts at the grounded part, not the lowest",
         _labels(chain, "grounded", CHAIN_MARKS)[0] == "a.0"),
        ("and walks the contact graph outward from it",
         _labels(chain, "grounded", CHAIN_MARKS) == ["a.0", "b.0", "c.0"]),
        ("where a z-sort would start with a part touching nothing yet",
         _labels(chain, "bottom-up", CHAIN_MARKS)[0] == "c.0"),
        ("fasteners arrive last, after everything they hold",
         _labels(fastened, "grounded", FASTENED_MARKS) ==
         ["plate.0", "plate.1", "screw.0"]),
        ("which a z-sort gets wrong, driving the screw in mid-stack",
         _labels(fastened, "bottom-up", FASTENED_MARKS) ==
         ["plate.0", "screw.0", "plate.1"]),
        ("declared keeps the project's own order",
         _labels(fastened, "declared", FASTENED_MARKS) ==
         [label for label, _ in fastened]),
        ("every part arrives exactly once, whatever the order",
         all(sorted(_labels(fastened, how, FASTENED_MARKS)) ==
             sorted(label for label, _ in fastened) for how in ORDERS)),
    ]


def _robustness_checks():
    """the orders that have to survive a model the graph cannot describe."""
    split = _parts([("a.0", ((0, 0, 0), (5, 5, 5))),
                    ("b.0", ((100, 100, 100), (105, 105, 105)))])
    marks = {"a": {"grounded": True, "embeds": False},
             "b": {"grounded": False, "embeds": False}}
    all_bolts = _parts([("s.0", ((0, 0, 0), (2, 2, 2)))])
    return [
        ("a disconnected piece is still reached", sorted(
            _labels(split, "grounded", marks)) == ["a.0", "b.0"]),
        ("a model of nothing but fasteners still sequences",
         _labels(all_bolts, "grounded", {"s": {"embeds": True}}) == ["s.0"]),
        ("no flags at all falls back rather than failing",
         _labels(split, "grounded", {}) == ["a.0", "b.0"]),
        ("an unbuilt dist reports no flags rather than raising",
         assemble.flags("nope", tempfile.gettempdir()) == {}),
    ]


def _in_flight(count, at_once, steps=200):
    """the most parts simultaneously in the air over a whole clip."""
    worst = 0
    for s in range(steps + 1):
        frac = s / float(steps)
        moving = sum(1 for i in range(count)
                     if 0.0 < assemble.arrival(frac, i, count, at_once) < 1.0)
        worst = max(worst, moving)
    return worst


def _timing_checks():
    """arrival is a smoothstep over each part's slice of the clip."""
    a = [assemble.arrival(f / 20.0, 0, 4) for f in range(21)]
    last = [assemble.arrival(f / 20.0, 3, 4) for f in range(21)]
    half = 0.5 * assemble.flight_for(4)
    return [
        ("a part starts away from home", a[0] == 0.0),
        ("and ends at home", a[-1] == 1.0),
        ("arriving monotonically", all(x <= y for x, y in zip(a, a[1:]))),
        ("easing in and out rather than at constant speed",
         assemble.arrival(half, 0, 4) == 0.5 and a[1] < 0.5 * a[10]),
        ("the last part is home before the clip ends, so the model is seen",
         last[-1] == 1.0),
    ]


def _concurrency_checks():
    """R4.3: FCAD_AT_ONCE caps how many parts are in the air together.

    a fixed flight duration does not survive the part count - twenty parts at
    0.45 of the clip each converge twenty-deep, which reads as an explosion
    running backwards. capping the concurrency and solving the flight from it
    holds the picture steady however many parts there are."""
    counts = (2, 5, 20)
    caps = [(n, c, _in_flight(c, n)) for n in (1, 2, 4) for c in counts]
    seq = all(m == 1 for n, _, m in caps if n == 1)
    honoured = all(m <= n for n, _, m in caps)
    # and it is not trivially satisfied by never overlapping at all.
    reaches = _in_flight(20, 4) == 4
    ends = [assemble.arrival(1.0, c - 1, c, n) for n in (1, 4) for c in counts]
    return [
        ("at_once=1 is strictly sequential, one part landing before the next "
         "leaves", seq),
        ("a cap of N never exceeds N in flight %s"
         % [(n, c, m) for n, c, m in caps], honoured),
        ("and is actually reached, not just never approached", reaches),
        ("every part still lands by the end, at any concurrency",
         all(v == 1.0 for v in ends)),
    ]


def _length_checks():
    """R4.3: --seconds/--fps set the clip; the frame count is their product.

    arrival works in fractions of the loop, so lengthening a clip changes its
    pace and nothing else - the concurrency and the ordering are untouched."""
    from fcad.render import MESH_SECONDS, render
    return [
        ("frames are seconds x fps", render.frames_for(6, 12) == 72),
        ("a longer clip is proportionally more frames",
         render.frames_for(12, 12) == 2 * render.frames_for(6, 12)),
        ("a higher rate is proportionally more frames",
         render.frames_for(6, 24) == 2 * render.frames_for(6, 12)),
        ("a degenerate request still writes something playable",
         render.frames_for(0, 12) == 2 and render.frames_for(-5, 12) == 2),
        ("the default length is shared with the cli, not duplicated",
         render.frames_for(MESH_SECONDS, 10) == 72),
    ]


def _render_checks(root):
    """R4.3: it writes the clip, from the part stls and the placements."""
    from stl import mesh as stl_mesh
    from fcad.render import animate

    dist = os.path.join(root, "dist")
    os.makedirs(os.path.join(dist, "parts"))
    # only the plate's stl, so the screw's instance has nothing to load.
    for label, tris in _parts(FASTENED)[:1]:
        part = label.rsplit(".", 1)[0]
        m = stl_mesh.Mesh(np.zeros(len(tris), dtype=stl_mesh.Mesh.dtype))
        m.vectors = tris
        m.save(os.path.join(dist, "parts", part + ".stl"))
    with open(config.placements_path(dist, NAME), "w") as f:
        json.dump({"plate": [[[0, 0, 0], [0, 0, 0, 1]],
                             [[0, 0, 9], [0, 0, 0, 1]]],
                   "screw": [[[18, 18, 2], [0, 0, 0, 1]]]}, f)
    with open(config.parts_path(dist, NAME), "w") as f:
        json.dump(FASTENED_MARKS, f)

    stem = os.path.join(root, "clip")
    animate.animate("assembly", stem, name=NAME, dist=dist)
    made = [os.path.getsize(stem + ext) for ext in (".mp4", ".gif")]

    # a part target has nothing to assemble, and assembling everything anyway
    # would animate something the caller did not ask for.
    try:
        animate.animate("plate", os.path.join(root, "p"), name=NAME, dist=dist,
                        subject="assemble")
        mismatched = None
    except SystemExit as e:
        mismatched = str(e)
    spun = os.path.join(root, "spun")
    animate.animate("plate", spun, name=NAME, dist=dist, subject="static")

    empty = os.path.join(root, "empty")
    os.makedirs(os.path.join(empty, "parts"))
    with open(config.placements_path(empty, NAME), "w") as f:
        json.dump({}, f)
    try:
        animate.animate("assembly", os.path.join(root, "x"), name=NAME,
                        dist=empty, subject="assemble")
        refused = None
    except SystemExit as e:
        refused = str(e)
    return [
        ("animate defaults to assembling, no --subject needed",
         all(s > 1000 for s in made)),
        ("a part target with --subject assemble is refused, not guessed at",
         mismatched is not None and "names the part 'plate'" in mismatched),
        ("and is told how to get what it probably wanted",
         mismatched is not None and "--subject static" in mismatched),
        ("a part still spins on its own", os.path.getsize(spun + ".gif") > 1000),
        ("an instance whose part stl is missing is skipped, not fatal",
         len(assemble.instances(NAME, dist, 0.0)) == 2),
        ("nothing to assemble fails loudly", refused is not None
         and "no part stls" in refused),
        ("and says what to build first", refused is not None
         and "fcad build" in refused),
    ]


def main():
    root = tempfile.mkdtemp()
    try:
        checks = (_order_checks() + _robustness_checks() + _timing_checks()
                  + _concurrency_checks() + _length_checks()
                  + _render_checks(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)
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


if any(a.endswith("test_assemble.py") for a in sys.argv):
    raise SystemExit(main())
