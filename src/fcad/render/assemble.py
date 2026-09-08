"""the assembly sequence: parts flying in one by one to build the model.

plain python (no FreeCAD), from what a build already wrote: `parts/<name>.stl`
for each part's geometry in its own frame, and `<name>-placements.json` for where
every instance of it belongs. that pairing is the whole trick - the assembly stl
is one welded lump with no seams in it, so it cannot be taken apart, while the
part files plus their placements *are* the model in pieces and can be assembled
in any order the animator likes.

each instance starts pushed out along the line from the assembly's centre through
its own, so it arrives from the direction it belongs to rather than sliding in
from an arbitrary axis, and lands with an ease-out so the motion settles instead
of stopping dead.

the order is the part worth getting right, and it comes from three things fcad
already knows. `grounded` says which part anchors the model. the contact graph -
which instances touch - is arithmetic over geometry the renderer already holds.
and `embeds` already marks the fasteners, because it is the flag that excludes
screws from the interference check. so the sequence is a breadth-first walk
outward from the anchor with the fasteners held to the end, which is the order
someone would actually build the thing in.

note the joint graph is *not* the source: `build_jointed_doc` fixes every
non-grounded part to a single datum, so it is a star with everything one hop from
the anchor. it exists to make the assembly fully constrained, not to describe
what touches what.
"""

import json
import math
import os

import numpy as np

from fcad import config
from fcad.render import render

# how far out a part starts, as a multiple of its offset from the assembly
# centre. big enough to read as "apart", small enough to keep the view cube
# usable - the cube is squared to the *assembled* model, so parts fly in from
# outside the frame and that is fine.
EXPLODE = float(os.environ.get("FCAD_EXPLODE", 1.6))
# how many parts may be in the air at once, which is the thing that actually
# reads. a fixed flight duration does not survive the part count: at 0.45 of the
# clip each, three parts overlap two-deep and twenty overlap twenty-deep, so a
# real assembly arrived as one converging swarm - an explosion running backwards
# rather than something being built. fixing the concurrency instead and deriving
# the flight from it holds the picture steady from three parts to fifty. 1 is
# strictly sequential: each part lands before the next leaves.
AT_ONCE = max(1, int(os.environ.get("FCAD_AT_ONCE", 3)))
# seconds one part spends in the air, and the beat at the end with everything in
# place so the finished model is looked at rather than glimpsed on the last
# frame. both at speed 1, and both in *seconds* rather than fractions of the clip
# because the clip's length is derived from them and the model, not fixed: more
# parts, or fewer of them in the air at once, means a longer animation. that is
# the point. a fixed total would squeeze twenty sequential landings into the same
# time as three, and each one would be a blur.
FLIGHT_SECONDS = float(os.environ.get("FCAD_FLIGHT_SECONDS", 1.2))
SETTLE_SECONDS = float(os.environ.get("FCAD_SETTLE_SECONDS", 1.0))
# mm of slack when deciding two instances touch. a built model's parts meet
# exactly, but its stls are faceted and then decimated onto a grid, so "exactly"
# needs a tolerance wider than the decimation moved anything.
CONTACT = float(os.environ.get("FCAD_CONTACT", 6.0))


def _matrix(quat):
    """rotation matrix from an (x, y, z, w) quaternion, as placements record it."""
    x, y, z, w = quat
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]],
        np.float32)


def instances(name, dist, grid):
    """[(label, placed triangles)] for every instance the assembly build wrote.

    each part's stl is loaded once and decimated once, then re-posed per
    instance: a part used twenty times costs one load and twenty transforms."""
    with open(config.placements_path(dist, name)) as f:
        placements = json.load(f)
    out = []
    for part in sorted(placements):
        path = render.stl_path(part, name, dist)
        if not os.path.exists(path):
            continue
        tris = render.cluster(render.load_tris(part, name, dist), grid)
        for i, (base, quat) in enumerate(placements[part]):
            posed = tris @ _matrix(quat).T + np.array(base, np.float32)
            out.append(("%s.%d" % (part, i), posed.astype(np.float32)))
    return out


def flags(name, dist):
    """{part: {grounded, embeds}} the build recorded, or {} for an older one."""
    try:
        with open(config.parts_path(dist, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def touching(parts, tol):
    """{index: {neighbour index}} for instances whose bounding boxes meet.

    adjacency for *sequencing*, not a contact model: an inflated bounding box
    over-reports (two boards near each other diagonally read as touching) and
    that is the right trade here, because a false neighbour only affects the
    order things fly in, while the real BREP distance would cost a quadratic
    sweep of kernel queries on every animation.

    it is computed here rather than at build time for the same reason: the
    renderer already holds every instance's triangles, so this is arithmetic it
    can do for free, while `grounded` and `embeds` genuinely have to be carried
    out of the project."""
    boxes = []
    for _, tris in parts:
        flat = tris.reshape(-1, 3)
        boxes.append((flat.min(axis=0) - tol, flat.max(axis=0) + tol))
    near = {i: set() for i in range(len(parts))}
    for i, (lo_i, hi_i) in enumerate(boxes):
        for j in range(i + 1, len(parts)):
            lo_j, hi_j = boxes[j]
            if bool((lo_i <= hi_j).all() and (lo_j <= hi_i).all()):
                near[i].add(j)
                near[j].add(i)
    return near


def _lowest(parts, among):
    return min(among, key=lambda i: (float(parts[i][1][:, :, 2].min()),
                                     parts[i][0]))


def from_grounded(parts, marks, tol):
    """parts in build order: outward from the grounded part, fasteners last.

    a breadth-first walk of the contact graph starting at the grounded part, so
    every piece arrives attached to something already there rather than floating
    into position - which is what "assembled" looks like and what a z-sort only
    accidentally achieves. neighbours are taken lowest-first so a course of
    boards lays itself in a readable direction, and the walk restarts at the
    lowest unvisited part when the model is in several disconnected pieces.

    parts flagged `embeds` are held back to the end regardless of where they sit
    in the graph. they are fasteners: a screw driven before the board it holds is
    both wrong and unreadable, and `embeds` is already fcad's word for exactly
    this class of part (it is what excludes them from the interference check)."""
    fastener = [bool(marks.get(label.rsplit(".", 1)[0], {}).get("embeds"))
                for label, _ in parts]
    anchors = [i for i, (label, _) in enumerate(parts)
               if marks.get(label.rsplit(".", 1)[0], {}).get("grounded")]
    near = touching(parts, tol)
    structural = {i for i in range(len(parts)) if not fastener[i]}

    seq, seen = [], set()
    queue = [i for i in sorted(anchors) if i in structural]
    while len(seen) < len(structural):
        if not queue:
            queue = [_lowest(parts, structural - seen)]
        i = queue.pop(0)
        if i in seen:
            continue
        seen.add(i)
        seq.append(i)
        queue += sorted(near[i] & structural - seen,
                        key=lambda k: (float(parts[k][1][:, :, 2].min()),
                                       parts[k][0]))
    # then the fasteners, each following the structure it fastens to.
    place = {i: n for n, i in enumerate(seq)}
    rest = [i for i in range(len(parts)) if fastener[i]]
    rest.sort(key=lambda i: (min((place[k] for k in near[i] if k in place),
                                 default=len(seq)), parts[i][0]))
    return [parts[i] for i in seq + rest]


def order(parts, how, marks=None, tol=CONTACT):
    """the sequence parts arrive in.

    `grounded` is the default and the only one that models assembly rather than
    approximating it; it is named for the part flag it starts from, which is the
    same word a project writes on the part. the others are escape hatches:
    `bottom-up` for a model whose contact graph misleads, `declared` to keep the
    project's own order, which is sometimes the only sensible one for something
    not stacked."""
    if how == "grounded" and marks:
        return from_grounded(parts, marks, tol)
    if how == "declared":
        return parts
    keyed = sorted(parts, key=lambda p: float(p[1][:, :, 2].min()))
    return keyed if how != "top-down" else keyed[::-1]


def offsets(parts):
    """the vector each part starts displaced by: outward from the assembly's
    centre, so it arrives from its own side of the model.

    a part sitting exactly at the centre has no outward direction to speak of, so
    it drops in from above instead of jittering along whatever rounding error
    produced."""
    centres = np.array([p[1].reshape(-1, 3).mean(axis=0) for p in parts])
    span = float(np.ptp(np.concatenate([p[1].reshape(-1, 3) for p in parts]),
                        axis=0).max()) or 1.0
    out = []
    for c in centres - centres.mean(axis=0):
        length = float(np.linalg.norm(c))
        direction = c / length if length > span * 1e-3 else np.array([0, 0, 1.0])
        out.append((EXPLODE * max(length, span * 0.25) * direction).astype(np.float32))
    return out


def spacing_for(at_once=AT_ONCE):
    """seconds between one part's departure and the next.

    a flight divided by the concurrency: at 1 each part lands before the next
    leaves, at 4 four are in the air at any moment."""
    return FLIGHT_SECONDS / max(1, int(at_once))


def duration(count, at_once=AT_ONCE):
    """seconds the assembly clip runs at speed 1, derived from the model.

    this is the whole reason the length is not a setting: twenty parts arriving
    one at a time is a genuinely longer film than three, and asking for both in
    the same seconds makes one of them unreadable."""
    return (spacing_for(at_once) * max(0, count - 1)
            + FLIGHT_SECONDS + SETTLE_SECONDS)


def arrival(frac, index, count, at_once=AT_ONCE):
    """0 (not yet placed) .. 1 (home) for one part at loop position `frac`.

    departures are spaced by `spacing_for`, so the last one lands exactly as the
    settling beat begins and the model is seen whole before the clip ends. the
    ease is a smoothstep: it leaves and arrives at rest, which is what stops each
    landing looking like a collision."""
    total = duration(count, at_once)
    flight = FLIGHT_SECONDS / total
    start = (spacing_for(at_once) / total) * index
    t = (frac - start) / max(1e-6, flight)
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)
