"""draw a built model with the FreeCAD gui, offscreen: one png for `fcad
render`, or every frame of a clip for `fcad animate`.

the renderer this replaced drew the assembly STL with matplotlib: one colour,
one camera fitted to the whole bounding box. that is enough for a planter and
useless for a wristwatch, where the 34 mm head is a blob on a 240 mm strap and
everything of interest sits under a crystal. this one reads what a build already
wrote - the part STEP files, the placements and the per-part look - and draws
them with FreeCAD's own viewer, so parts have a colour, a crystal can be seen
through, and the camera can be put on a subset, a section or an exploded stack.

it needs a display, which the cli supplies with `xvfb-run`. the inputs arrive in
FCAD_RENDER_* because nothing else crosses the process boundary.
"""

import json
import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtWidgets

from fcad import config

V = App.Vector
DEFAULT_COLOR = (0.72, 0.73, 0.75)
DEFAULT_SIZE = (1600, 1200)
# a clip is hundreds of frames and ends up a gif, so its frames are smaller.
FILM_SIZE = (960, 720)
# the frame's height as a multiple of the model's diagonal: a little air.
FILM_MARGIN = 1.05
FILM_REPORT = 50             # frames between progress lines
BACKGROUND = "White"
STEP_EXT = ".step"
PNG_EXT = ".png"
PUMPS = 30                   # event-loop turns the scene needs before a capture
# how far a section's cutting block reaches past the model, as a multiple of its
# diagonal: anything over 1 clears it at every orientation.
CUT_REACH = 2.0
# a housing's closed end: the share of its height sampled at each end, and how
# much more material one end must have than the other to be the closed one.
END_SLAB = 0.05
CLOSED_RATIO = 1.5


def orbit(yaw, tilt):
    """a camera `tilt` degrees down from overhead, turned `yaw` about z."""
    return (App.Rotation(V(0, 0, 1), yaw) * App.Rotation(V(1, 0, 0), tilt))


VIEWS = {
    "iso": orbit(30, 50),
    "top": orbit(0, 0),
    "bottom": App.Rotation(V(0, 1, 0), 180),
    "front": orbit(0, 90),
    "back": orbit(180, 90),
    "right": orbit(90, 90),
    "left": orbit(-90, 90),
    # near side-on: an exploded stack only reads from here, since from above
    # each layer hides the ones under it.
    "side": orbit(35, 78),
}


def _names(key):
    return [n for n in os.environ.get(key, "").split(",") if n]


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def instances(cfg, target, only=(), exclude=()):
    """[(part, placed shape)] for a part on its own, or the whole assembly."""
    parts_dir = os.path.join(cfg.dist, "parts")
    built = sorted(f[:-len(STEP_EXT)] for f in os.listdir(parts_dir)
                   if f.endswith(STEP_EXT)) if os.path.isdir(parts_dir) else []
    whole = config.is_assembly(cfg.name, target)
    unknown = [n for n in list(only) + list(exclude) + ([] if whole else [target])
               if n not in built]
    if unknown:
        raise SystemExit(
            "fcad render: no built part named %s. the output path comes after "
            "the target (`fcad render assembly out.png`). built parts: %s"
            % (", ".join(unknown), ", ".join(built) or "none; run fcad build"))

    def shape(part):
        base = Part.Shape()
        base.read(os.path.join(parts_dir, part + STEP_EXT))
        return base

    if not whole:
        return [(target, shape(target))]
    placed = _load_json(config.placements_path(cfg.dist, cfg.name))
    out = []
    for part in sorted(placed):
        if (only and part not in only) or part in exclude:
            continue
        base = shape(part)
        for pos, quat in placed[part]:
            s = base.copy()
            s.Placement = App.Placement(V(*pos), App.Rotation(*quat))
            out.append((part, s))
    return out


def _bbox(items):
    box = App.BoundBox()
    for _, shape in items:
        box.add(shape.BoundBox)
    return box


def _host(shape, items, flags):
    """the structural part a cross fastener sits in: the smallest one whose
    bounding box holds the fastener's centre, or None."""
    centre = shape.BoundBox.Center
    holders = [(other.BoundBox.DiagonalLength, part) for part, other in items
               if not flags.get(part, {}).get("embeds")
               and other.BoundBox.isInside(centre)]
    return min(holders)[1] if holders else None


def _carrier(part, flags, drawn):
    """the part `part` travels on: the end of its `rides` chain, as far as that
    chain stays among the structural parts drawn. a fastener is no carrier: it
    may have no layer of its own."""
    seen = {part}
    while True:
        host = flags.get(part, {}).get("rides")
        if (host not in drawn or host in seen
                or flags.get(host, {}).get("embeds")):
            return part
        seen.add(host)
        part = host


def _layers(items, flags):
    """{instance index: the part whose layer it travels in} for an explosion.

    a part is its own layer, with two exceptions. one the project declares
    `rides` another stays on it. and a fastener lying across the stack (a cross
    pin, a spring bar, a side button) has no layer of its own to go to: it
    belongs to the part it passes through and travels with it."""
    drawn = {part for part, _ in items}
    out = {}
    for i, (part, shape) in enumerate(items):
        box = shape.BoundBox
        across = box.ZLength < max(box.XLength, box.YLength)
        host = (_host(shape, items, flags)
                if across and flags.get(part, {}).get("embeds") else None)
        out[i] = _carrier(host or part, flags, drawn)
    return out


def _end_volume(shape, top):
    """how much of a part lies in the END_SLAB of its height at one end."""
    box = shape.BoundBox
    depth = END_SLAB * box.ZLength
    slab = Part.makeBox(box.XLength, box.YLength, depth, V(
        box.XMin, box.YMin, box.ZMax - depth if top else box.ZMin))
    return shape.common(slab).Volume


def _seat(part, shape, items, flags):
    """the height a structural part is ranked at in an explosion.

    its bounding box centre, unless it is a housing: a part around another
    part's centre and closed at one end. a deep cover's centre sits below what
    it covers and a tray's above what it holds, so each is ranked by its closed
    end, which is the side it comes off from. a sleeve, open at both ends,
    keeps its centre."""
    box = shape.BoundBox
    holds = any(other != part and not flags.get(other, {}).get("embeds")
                and box.isInside(inner.BoundBox.Center) for other, inner in items)
    if not holds:
        return box.Center.z
    top, bottom = _end_volume(shape, True), _end_volume(shape, False)
    if top > CLOSED_RATIO * bottom:
        return box.ZMax
    return box.ZMin if bottom > CLOSED_RATIO * top else box.Center.z


def exploded(items, flags, factor):
    """the instances lifted apart along z, one layer per part, lowest first.

    layers are ranked by where each part sits rather than scaled from it: parts
    nested inside a housing would otherwise stay inside it. a fastener driven
    along z is ranked by its outer end, so a screw up through a cover lands
    below the cover instead of above it. the gap is `factor` times the tallest
    part shown."""
    mid = _bbox(items).Center.z
    layer = _layers(items, flags)

    def key(part, shape):
        box = shape.BoundBox
        if not flags.get(part, {}).get("embeds"):
            return _seat(part, shape, items, flags)
        return box.ZMin if box.Center.z < mid else box.ZMax

    heights = {}
    for i, (part, shape) in enumerate(items):
        if layer[i] == part:
            heights.setdefault(part, []).append(key(part, shape))
    order = sorted(heights, key=lambda p: sum(heights[p]) / len(heights[p]))
    gap = factor * max(shape.BoundBox.ZLength for _, shape in items)
    out = []
    for i, (part, shape) in enumerate(items):
        moved = shape.copy()
        moved.Placement = App.Placement(
            shape.Placement.Base + V(0, 0, order.index(layer[i]) * gap),
            shape.Placement.Rotation)
        out.append((part, moved))
    return out


def sectioned(items, axis):
    """the instances with one half cut away at the model's centre: the half
    toward an `iso` camera for x and y, the top half for z."""
    box = _bbox(items)
    r = CUT_REACH * box.DiagonalLength
    c = box.Center
    corner = {"x": V(c.x, c.y - r, c.z - r),
              "y": V(c.x - r, c.y - 2 * r, c.z - r),
              "z": V(c.x - r, c.y - r, c.z)}[axis]
    block = Part.makeBox(2 * r, 2 * r, 2 * r, corner)
    cut = [(part, shape.cut(block)) for part, shape in items]
    return [(part, shape) for part, shape in cut if shape.Volume > 0]


def _pump():
    for _ in range(PUMPS):
        QtWidgets.QApplication.processEvents()


def _size(text, default):
    """(width, height) from a `WxH` flag, or the default when it was not given."""
    return [int(n) for n in (text or "").split("x") if n] or default


def stage(items, flags):
    """the instances as objects in a scratch document, each with its part's
    look; returns (document, objects, view)."""
    doc = App.newDocument("fcad_render")
    objs = []
    for i, (part, shape) in enumerate(items):
        obj = doc.addObject("Part::Feature", "%s_%03d" % (part, i))
        obj.Shape = shape
        look = flags.get(part, {})
        # floats, or the gui reads a whole-number (1, 0, 0) on its 0..255 scale
        # and draws near-black.
        obj.ViewObject.ShapeColor = tuple(
            float(v) for v in look.get("color") or DEFAULT_COLOR)
        obj.ViewObject.Transparency = int(look.get("transparency") or 0)
        objs.append(obj)
    doc.recompute()
    gview = Gui.getDocument(doc.Name).ActiveView
    _pump()
    return doc, objs, gview


def capture(items, flags, view, size, path):
    """draw `items` in a scratch document and save one png of them."""
    doc, _, gview = stage(items, flags)
    # written straight onto the camera node: `viewIsometric()` and
    # `setCameraOrientation()` both animate the move, and a capture taken
    # mid-flight is tilted and clipped.
    gview.getCameraNode().orientation.setValue(*VIEWS[view].Q)
    gview.fitAll()
    _pump()
    gview.saveImage(path, size[0], size[1], BACKGROUND)
    App.closeDocument(doc.Name)


def _movers(items, objs, plan):
    """[(object, home placement, offset, plan column)] for the planned instances.

    the plan names an instance `<part>.<n>`, n counting that part's placements
    in the order the build recorded them, which is the order `instances` kept."""
    column = {label: i for i, label in enumerate(plan["labels"])}
    count, out = {}, []
    for (part, _), obj in zip(items, objs):
        label = "%s.%d" % (part, count.get(part, 0))
        count[part] = count.get(part, 0) + 1
        if label in column:
            out.append((obj, obj.Placement, V(*plan["offsets"][column[label]]),
                        column[label]))
    return out


def _look(cam, box, elev, azim, reach):
    """put the camera `elev` degrees above the horizon at `azim`, zoom unchanged.

    `fitAll` would refit every frame, so the model would pulse as it turned and
    shrink whenever a part was in the air. the frame is sized once instead, to
    the assembled model's diagonal, which holds it at every angle. the clipping
    planes are set by hand because an offscreen capture does not move them: left
    alone, a camera placed here draws an empty frame."""
    rotation = orbit(azim + 90.0, 90.0 - elev)
    eye = box.Center + rotation.multVec(V(0, 0, 2.0 * reach))
    cam.orientation.setValue(*rotation.Q)
    cam.position.setValue(eye.x, eye.y, eye.z)
    cam.height.setValue(FILM_MARGIN * box.DiagonalLength)
    cam.nearDistance.setValue(reach)
    cam.farDistance.setValue(3.0 * reach)


def film(cfg):
    """draw every frame of the clip `fcad animate` planned, one png each.

    the plan (see fcad.render.animate) says where the camera is on each frame
    and how much of its starting offset each moving instance still has to
    cover; an instance it does not name stays where the build put it."""
    with open(os.environ["FCAD_RENDER_PLAN"]) as f:
        plan = json.load(f)
    items = instances(cfg, plan["target"])
    if not items:
        raise SystemExit("fcad animate: nothing to draw for %r (build first)"
                         % plan["target"])
    doc, objs, gview = stage(
        items, _load_json(config.parts_path(cfg.dist, cfg.name)))
    gview.setCameraType("Orthographic")
    cam, box = gview.getCameraNode(), _bbox(items)
    movers = _movers(items, objs, plan)
    reach = box.DiagonalLength + max(
        [off.Length for _, _, off, _ in movers], default=0.0)
    width, height = _size(plan["size"], FILM_SIZE)
    frames = plan["frames"]
    for n, (elev, azim, away) in enumerate(frames):
        for obj, home, off, col in movers:
            obj.Placement = App.Placement(home.Base + off * away[col],
                                          home.Rotation)
        _look(cam, box, elev, azim, reach)
        gview.saveImage(plan["shots"] % n, width, height, BACKGROUND)
        if (n + 1) % FILM_REPORT == 0:
            sys.__stdout__.write("  drawn %d/%d\n" % (n + 1, len(frames)))
            sys.__stdout__.flush()
    App.closeDocument(doc.Name)
    return "drew %d frame(s) of %s (%d instance(s))" % (
        len(frames), plan["target"], len(items))


def render(cfg):
    target = os.environ.get("FCAD_RENDER_TARGET") or "assembly"
    items = instances(cfg, target, _names("FCAD_RENDER_PARTS"),
                      _names("FCAD_RENDER_EXCLUDE"))
    if not items:
        raise SystemExit("fcad render: nothing to draw for %r (build first, and "
                         "check --parts/--exclude)" % target)
    flags = _load_json(config.parts_path(cfg.dist, cfg.name))
    explode = os.environ.get("FCAD_RENDER_EXPLODE")
    section = os.environ.get("FCAD_RENDER_SECTION")
    if section:
        items = sectioned(items, section)
    if explode:
        items = exploded(items, flags, float(explode))
    asked = os.environ.get("FCAD_RENDER_VIEW")
    view = asked or ("side" if explode else "iso")
    size = _size(os.environ.get("FCAD_RENDER_SIZE"), DEFAULT_SIZE)
    # each variant asked for is in the default name, so a second view of a
    # target does not overwrite the first. --parts/--exclude are not: a list
    # of names is no filename, so a subset worth keeping is given a path.
    variant = [v for v in (asked, section and "section-" + section,
                           explode and "explode") if v]
    path = (os.environ.get("FCAD_RENDER_OUT") or config.artifact_stem(
        cfg.dist, cfg.name, target, *variant) + PNG_EXT)
    capture(items, flags, view, size, path)
    return ("rendered %s (%d instance(s), %s view) -> %s"
            % (target, len(items), view, path))


def main(draw=render):
    """draw, then leave without Qt's teardown, which can hang offscreen.

    the gui points python's stdout at its own report view, so the verdict goes
    to the process's real one, and the exit status says whether it worked."""
    status = 0
    try:
        said = draw(config.from_env())
    except SystemExit as exc:
        said, status = str(exc.code), 1
    except Exception:
        said, status = traceback.format_exc(), 1
    sys.__stdout__.write(said + "\n")
    sys.__stdout__.flush()
    os._exit(status)
