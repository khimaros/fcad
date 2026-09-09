"""`--dist` / `FCAD_DIST` has to reach the build, not just the cli.

`config.resolve()` reads the flag, then `FCAD_DIST`, then `<root>/dist`, and
`cfg.env()` exports the answer to the child. the in-FreeCAD side then has to
*use* it. it did not: `Project.dist` computed `<root>/dist` from scratch, so
`fcad -d /elsewhere build` reported the right directory from `info`, left it
empty, and wrote over the project's own `dist/` instead.

that failure mode is the bad kind - exit 0, no warning, requested directory
empty, tracked files in the working tree silently overwritten. a project doing a
what-if build into a scratch directory loses whatever was in `dist/`. it is
pinned here in both directions: the artifacts land where they were asked for,
and the default location is not touched at all.

found by the planter project, which ran exactly that command expecting an
out-of-tree build and had 24 tracked files clobbered.

needs FreeCAD: run with `freecadcmd tests/test_dist.py`. results go to
$RESULT_FILE.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import config, testing
from fcad._run import entry_path

PROJECT = '''
from FreeCAD import Placement

import fcad
from fcad.freecad import partdesign

PARAMS = {"size": 20.0}

def _block(doc, body, size):
    s = size / 2.0
    partdesign.pad_and_bore(doc, body, [(-s, -s), (s, -s), (s, s), (-s, s)],
                            size, name="block")

def compute(p):
    return [fcad.PartSpec("block", placements=[Placement()],
                          build=lambda doc, body: _block(doc, body, p["size"]))]
'''
NAME = "distproj"


def _build(root, dist=None):
    """run one build in a child process, as the cli would; (rc, output)."""
    path = os.path.join(root, NAME + ".fcad")
    with open(path, "w") as f:
        f.write(PROJECT)
    cfg = config.resolve(project=path, dist=dist)
    env = {**os.environ, **cfg.env()}
    r = subprocess.run([cfg.freecad, entry_path(), "parts"],
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr, cfg.dist


def _built(path):
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def main():
    c = testing.Checks()
    root = tempfile.mkdtemp(prefix="fcad_dist_")
    try:
        # a sentinel in the default location: an out-of-tree build must not
        # touch it, which is the whole point of asking for one.
        default = os.path.join(root, "dist")
        os.makedirs(os.path.join(default, "parts"))
        sentinel = os.path.join(default, "parts", "KEEP.txt")
        with open(sentinel, "w") as f:
            f.write("tracked file the build must not clobber\n")

        elsewhere = os.path.join(root, "scratch", "out")
        rc, out, resolved = _build(root, dist=elsewhere)
        c("an out-of-tree build exits 0 (%d)" % rc, rc == 0)
        c("... and resolves the requested dist (%s)" % resolved,
          resolved == os.path.abspath(elsewhere))
        parts = _built(os.path.join(elsewhere, "parts"))
        c("... writing the parts where they were asked for (%s)" % (parts,),
          any(f.startswith("block") for f in parts))
        c("... and leaving the default location alone (%s)"
          % (_built(os.path.join(default, "parts")),),
          _built(os.path.join(default, "parts")) == ["KEEP.txt"])
        c("... with the sentinel intact", os.path.exists(sentinel))

        # and with no --dist, the default is still where things land.
        plain = tempfile.mkdtemp(prefix="fcad_dist_plain_", dir=root)
        rc, out, resolved = _build(plain)
        c("a plain build still uses <root>/dist (%s)" % resolved,
          resolved == os.path.join(plain, "dist"))
        c("... and writes there (%s)"
          % (_built(os.path.join(plain, "dist", "parts")),),
          any(f.startswith("block")
              for f in _built(os.path.join(plain, "dist", "parts"))))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return c.report()


testing.main(main, __file__)
