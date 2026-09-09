"""end-to-end test for how a failed command reports itself.

an exit status with no reason is a bug, and two frozen requirements say so: R3.1
makes `check` state which parts overlap, R6.2 makes an unsolvable FEM target fail
"with a clear, non-zero error". freecadcmd loses both by default. it discards
whatever python still holds buffered on stdout when a command exits non-zero -
and stdout is only block-buffered when it is *not* a tty, so this reproduces off
a terminal and nowhere else - and it never prints the message a `SystemExit`
carries. between them a failing `fcad check` printed nothing whatsoever once
redirected, and fcad's own deliberate FEM diagnostics reached no one at all.

so this drives the real entry point in child processes with stdout on a pipe (a
pty would hide the first defect), over a project rigged to fail each way, and
asserts the reason arrived along with the status.

needs FreeCAD: run with `freecadcmd tests/test_errors.py`. results go to
$RESULT_FILE (freecadcmd swallows script stdout and exits 0 on error, so a file
is the reliable channel), matching the other tests.
"""

import os
import shutil
import subprocess
import sys
import tempfile

# make the in-repo fcad package importable under FreeCAD's bundled python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import config
from fcad._run import entry_path

# two cubes offset along x. at OFFSET they interpenetrate by a known volume; at
# SIZE they merely touch face to face, which is the clean case.
PROJECT = '''
import fcad, Part, FreeCAD as App

PARAMS = {"size": %(size)r}

def compute(p):
    solid = lambda: Part.makeBox(p["size"], p["size"], p["size"])
    return [fcad.PartSpec("a", solid=solid, placements=[App.Placement()]),
            fcad.PartSpec("b", solid=solid, placements=[
                App.Placement(App.Vector(%(offset)r, 0, 0), App.Rotation())])]
'''
NAME = "clash"
SIZE, OFFSET = 20.0, 5.0
OVERLAP = (SIZE - OFFSET) * SIZE * SIZE
NO_SUCH = "bogus"


def _run(root, command, offset=OFFSET, env_extra=None):
    """one command in a child process with stdout on a pipe; (rc, output)."""
    path = os.path.join(root, NAME + ".fcad")
    with open(path, "w") as f:
        f.write(PROJECT % {"size": SIZE, "offset": offset})
    cfg = config.resolve(project=path)
    env = {**os.environ, **cfg.env(), **(env_extra or {})}
    r = subprocess.run([cfg.freecad, entry_path(), command],
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _check_checks(root):
    """R3.1: a failing check names what failed, redirected or not."""
    rc, bad = _run(root, "check")
    ok_rc, good = _run(root, "check", offset=SIZE)
    return [
        ("failing check exits non-zero", rc != 0),
        ("failing check says it failed", "check failed:" in bad),
        ("failing check names the pair", "<->" in bad),
        ("failing check quantifies the overlap: %.1f mm^3" % OVERLAP,
         "%.1f mm^3" % OVERLAP in bad),
        ("passing check exits 0", ok_rc == 0),
        ("passing check reports no overlap",
         "check ok: no overlapping parts" in good),
        ("touching solids do not count as overlapping",
         "check failed:" not in good),
    ]


def _fem_checks(root):
    """R6.2: a FEM target that cannot be solved explains itself.

    freecadcmd prints nothing for a SystemExit's message, so every fcad diagnostic
    raised that way used to be swallowed whole - including the one that tells you
    to reduce mesh_size after a degenerate mesh."""
    rc, out = _run(root, "fem", env_extra={"FCAD_TARGET": NO_SUCH})
    return [
        ("unsolvable fem target exits non-zero", rc != 0),
        ("fem failure explains itself", "fcad fem:" in out),
        ("fem failure names the target", NO_SUCH in out),
    ]


def main():
    root = tempfile.mkdtemp()
    try:
        checks = _check_checks(root) + _fem_checks(root)
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


if any(a.endswith("test_errors.py") for a in sys.argv):
    raise SystemExit(main())
