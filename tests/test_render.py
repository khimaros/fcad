"""`fcad render` draws what was built, in the colours the project gave it.

the renderer it replaced painted the assembly STL one colour from one camera
fitted to the whole bounding box. on a wristwatch that is a blob on a strap with
nothing visible under the crystal, and "does it look like the model" could not
be answered from it. so what is pinned here is what that one could not do, and
each is asserted on the pixels of a real render rather than on the flags:

- a part is drawn in the colour its `PartSpec` declares;
- a see-through part shows what is under it;
- `--parts` / `--exclude` decide what is drawn at all;
- `--section` opens the model up, and `--explode` moves the parts apart;
- `fcad animate` is drawn the same way, so a clip is in those colours too.

the fixture is a red block with a blue block inside it, under a green lid, so
every one of those is a question about which colours reach the image.

needs FreeCAD, its gui and `xvfb-run`: run with `freecadcmd tests/test_render.py`.
results go to $RESULT_FILE.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from PySide import QtGui

from fcad import cli, config, testing
from fcad._run import entry_path

PROJECT = '''
from FreeCAD import Placement, Rotation, Vector

import fcad
from fcad.freecad import partdesign


def _sq(side):
    h = side / 2.0
    return [(-h, -h), (h, -h), (h, h), (-h, h)]


def _box(doc, body, name, side, height, hollow=0.0):
    sk = partdesign.add_sketch(doc, body, name + "_sketch", points=_sq(side),
                               loops=[_sq(hollow)] if hollow else [])
    pad = body.newObject("PartDesign::Pad", name + "_pad")
    pad.Profile = sk
    pad.Length = height
    doc.recompute()


def _bar(doc, body):
    """a cross pin: long in x, in the shell's walls at mid height, beside the
    core and hidden by the shell from every side."""
    sk = partdesign.add_sketch(doc, body, "pin_sketch", z=8.0,
                               points=[(-18, 11), (18, 11), (18, 14), (-18, 14)])
    pad = body.newObject("PartDesign::Pad", "pin_pad")
    pad.Profile = sk
    pad.Length = 4.0
    doc.recompute()


PARAMS = {"lid_clear": %d}


def compute(p):
    at = lambda z: [Placement(Vector(0, 0, z), Rotation())]
    return [
        fcad.PartSpec("pin", at(0), color=(0, 0, 1), length=60.0, embeds=True,
                      build=_bar),
        fcad.PartSpec("shell", at(0), color=(1, 0, 0), length=40.0,
                      build=lambda d, b: _box(d, b, "shell", 40.0, 20.0, 30.0)),
        fcad.PartSpec("core", at(0), color=(0, 0, 1), length=20.0,
                      build=lambda d, b: _box(d, b, "core", 20.0, 20.0)),
        fcad.PartSpec("lid", at(20), color=(0, 1, 0), length=40.0,
                      transparency=p["lid_clear"],
                      build=lambda d, b: _box(d, b, "lid", 40.0, 2.0)),
    ]
'''
NAME = "rproj"
SIZE = "320x240"
STEP = 4                     # sample every nth pixel: colours, not edges
# how far one channel must lead the others to count as that colour. shading
# darkens a face without changing which channel leads; a part seen through a
# tinted one keeps its lead but by less, which is what sets this.
LEAD = 40


def _hues(path):
    """which of red, green and blue appear in an image, as a set of letters."""
    img = QtGui.QImage(path)
    seen = set()
    for y in range(0, img.height(), STEP):
        for x in range(0, img.width(), STEP):
            c = QtGui.QColor(img.pixel(x, y))
            r, g, b = c.red(), c.green(), c.blue()
            for letter, lead, others in (("r", r, (g, b)), ("g", g, (r, b)),
                                         ("b", b, (r, g))):
                if all(lead - o > LEAD for o in others):
                    seen.add(letter)
    return seen


def _rows(path, letter):
    """(first, last) pixel row on which a colour appears, top row first."""
    img = QtGui.QImage(path)
    hit = []
    for y in range(img.height()):
        for x in range(0, img.width(), STEP):
            c = QtGui.QColor(img.pixel(x, y))
            lead = {"r": c.red(), "g": c.green(), "b": c.blue()}
            if all(lead[letter] - v > LEAD for k, v in lead.items() if k != letter):
                hit.append(y)
                break
    return (hit[0], hit[-1]) if hit else (0, -1)


def _said(path, dist, *flags):
    """what a render that should fail printed; (rc, output)."""
    cfg = config.resolve(project=path, dist=dist)
    r = subprocess.run([cli.XVFB_RUN, "-a", cfg.freecad_gui, entry_path(),
                        "render"], capture_output=True, text=True,
                       env={**os.environ, **cfg.env(), **cli.OFFSCREEN_ENV,
                            **dict(flags)})
    return r.returncode, r.stdout + r.stderr


def _project(root, lid_clear):
    path = os.path.join(root, NAME + ".fcad")
    with open(path, "w") as f:
        f.write(PROJECT % lid_clear)
    return path


def _render(path, dist, name, *flags):
    out = os.path.join(dist, name + ".png")
    rc = cli.main(["-p", path, "-d", dist, "render", "assembly", out,
                   "--size", SIZE, *flags])
    return out if rc == 0 and os.path.exists(out) else None


def _frames(mp4, into):
    """every frame of a clip as a png, first to last."""
    os.makedirs(into)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", mp4,
                    os.path.join(into, "%03d.png")], check=True)
    return [os.path.join(into, f) for f in sorted(os.listdir(into))]


def _animate_checks(c, path, dist):
    """R4.3: a clip is drawn by the same viewer, so it has the parts' colours.

    the camera is held still, so anything that differs between two frames is a
    part moving."""
    clip = ["--camera", "fixed", "--seconds", "2", "--fps", "5", "--size", SIZE]
    cli.main(["-p", path, "-d", dist, "animate", *clip])
    stem = os.path.join(dist, NAME + ".assemble")
    made = all(os.path.exists(stem + ext) and os.path.getsize(stem + ext) > 1000
               for ext in (".mp4", ".gif"))
    c("animate defaults to assembling, into <name>.assemble.{mp4,gif}", made)
    if not made:
        return
    frames = _frames(stem + ".mp4", os.path.join(dist, "assemble_frames"))
    c("... of seconds x fps frames (%d)" % len(frames), len(frames) == 10)
    c("the assembled model is in its parts' colours (%s)"
      % sorted(_hues(frames[-1])), {"r", "g"} <= _hues(frames[-1]))
    c("... having arrived there: the first frame is not the last",
      QtGui.QImage(frames[0]) != QtGui.QImage(frames[-1]))

    cli.main(["-p", path, "-d", dist, "animate", "core", *clip])
    spun = os.path.join(dist, "parts", "core.spin")
    c("a part still spins on its own, into parts/<part>.spin",
      os.path.exists(spun + ".gif") and os.path.getsize(spun + ".gif") > 1000)
    if os.path.exists(spun + ".mp4"):
        seen = _hues(_frames(spun + ".mp4", os.path.join(dist, "spin_frames"))[0])
        c("... in its own colour (%s)" % sorted(seen), seen == {"b"})


def main():
    c = testing.Checks()
    root = tempfile.mkdtemp(prefix="fcad_render_")
    try:
        dist = os.path.join(root, "dist")
        path = _project(root, 0)
        c("the fixture builds", cli.main(["-p", path, "-d", dist, "build",
                                          "parts", "assembly"]) == 0)

        top = _render(path, dist, "top", "--view", "top")
        c("render writes a png", top is not None)
        if top is None:
            return c.report()
        c("a solid lid hides what is under it (%s)" % sorted(_hues(top)),
          _hues(top) == {"g"})

        bare = _render(path, dist, "bare", "--view", "top", "--exclude", "lid")
        c("--exclude drops a part, showing the parts in their colours (%s)"
          % sorted(_hues(bare)), _hues(bare) == {"r", "b"})
        only = _render(path, dist, "only", "--view", "top", "--parts", "core")
        c("--parts draws only what it names (%s)" % sorted(_hues(only)),
          _hues(only) == {"b"})

        cut = _render(path, dist, "cut", "--view", "front", "--section", "y",
                      "--exclude", "pin")
        c("--section opens the shell onto the core (%s)" % sorted(_hues(cut)),
          "b" in _hues(cut))
        whole = _render(path, dist, "whole", "--view", "front")
        c("... which a whole front view does not show (%s)" % sorted(_hues(whole)),
          "b" not in _hues(whole))

        apart = _render(path, dist, "apart", "--view", "front", "--explode")
        c("--explode lifts the core out of the shell (%s)" % sorted(_hues(apart)),
          _hues(apart) == {"r", "g", "b"})

        # --explode's GAP is optional, so written before the positionals it
        # used to take the target for its gap and die on a float conversion.
        first = os.path.join(dist, "first.png")
        try:
            cli.main(["-p", path, "-d", dist, "render", "--size", SIZE,
                      "--explode", "assembly", first])
        except SystemExit:
            pass
        c("--explode may come before the target", os.path.exists(first))

        # one positional that is a png is where to write, not what to draw:
        # `render --exclude lid out.png` means the assembly.
        lone = os.path.join(dist, "lone.png")
        cli.main(["-p", path, "-d", dist, "render", "--size", SIZE,
                  "--view", "top", "--exclude", "lid", lone])
        c("a lone png is the output, of the assembly",
          os.path.exists(lone) and _hues(lone) == {"r", "b"})

        # a fastener that enters from the side belongs to the part it is in. a
        # screw driven along z is ranked by its outer end so it clears its
        # cover; ranked that way a cross pin leaves its part and hangs in the
        # stack on its own.
        pinned = _render(path, dist, "pinned", "--view", "front", "--explode",
                         "--section", "y", "--parts", "shell,pin")
        shell, pin = _rows(pinned, "r"), _rows(pinned, "b")
        c("--explode keeps a cross pin in the part it passes through "
          "(pin rows %s, shell rows %s)" % (pin, shell),
          shell[0] <= pin[0] <= pin[1] <= shell[1])

        # with no path given a render is one more export of its target, beside
        # the others, and each variant gets a name of its own so asking for a
        # second view does not overwrite the first.
        for flags, png in (((), NAME + ".png"),
                           (("--view", "top", "--section", "y"),
                            NAME + ".top.section-y.png"),
                           (("core",), os.path.join("parts", "core.png"))):
            cli.main(["-p", path, "-d", dist, "render", "--size", SIZE, *flags])
            c("the default path is %s" % png,
              os.path.exists(os.path.join(dist, png)))

        # a target that is not a part is a mistake worth one plain sentence:
        # `render --parts a out.png` reads the png as the target.
        rc, said = _said(path, dist, ("FCAD_RENDER_TARGET", "out.png"))
        c("an unknown target fails (%d)" % rc, rc != 0)
        c("... naming it and the parts there are",
          "out.png" in said and "shell" in said)
        c("... without a traceback", "Traceback" not in said)
        rc, said = _said(path, dist, ("FCAD_RENDER_PARTS", "shell,nosuch"))
        c("an unknown --parts name fails the same way",
          rc != 0 and "nosuch" in said and "Traceback" not in said)

        _animate_checks(c, path, dist)

        # the same model with a clear lid: rebuilt, since the look is recorded
        # by the build and read back by the renderer.
        path = _project(root, 80)
        cli.main(["-p", path, "-d", dist, "build", "assembly"])
        clear = _render(path, dist, "clear", "--view", "top")
        c("a see-through lid shows the parts under it (%s)" % sorted(_hues(clear)),
          "b" in _hues(clear))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
