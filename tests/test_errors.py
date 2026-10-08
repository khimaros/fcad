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


# a part whose build passes a keyword its callee does not take: the commonest
# way a project crashes, and nothing fcad raised itself.
CRASHING = '''
import fcad, FreeCAD as App
from fcad.freecad import partdesign

PARAMS = {"size": 1.0}

def _build(doc, body):
    partdesign.add_sketch(doc, body, "outline", colour=(1, 0, 0))

def compute(p):
    return [fcad.PartSpec("a", build=_build, placements=[App.Placement()])]
'''
# what the kernel leaves in C stdio's buffer: nearly a block of progress bar,
# then a word with no newline after it. the block boundary falls inside the
# word, so whatever python prints next used to land in the middle of it.
STDIO_BLOCK = 4096
BAR = "\\t(50 %%%%)\\t\\r"        # ten bytes once both formats have run
WORD = "Postprocessing......"
CHATTY = '''
import ctypes
_libc = ctypes.CDLL(None)
_libc.fflush(None)
_libc.printf(b"%%%%s", b"%s" * %d + b"%s")
''' % (BAR, STDIO_BLOCK // 10, WORD) + PROJECT


def _run(root, command, offset=OFFSET, env_extra=None, source=PROJECT):
    """one command in a child process with stdout on a pipe; (rc, output)."""
    path = os.path.join(root, NAME + ".fcad")
    with open(path, "w") as f:
        f.write(source % {"size": SIZE, "offset": offset})
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
        # nothing here is flagged `grounded`, so support is not tested, and a
        # line reading "ok" would claim a result nobody computed.
        ("an assertion that did not run says skipped, not ok",
         "check skipped: no part is flagged `grounded`" in good
         and "resting on another" not in good),
        ("loading a project leaves no bytecode beside it (%s)"
         % sorted(os.listdir(root)), "__pycache__" not in os.listdir(root)),
    ]


def _crash_checks(root):
    """an exception fcad did not raise is still a failure, and says where.

    freecadcmd reports an uncaught exception as one line and exits 0, so
    `fcad precommit` on a build that crashed looked like a pass to anything
    that read the status."""
    out = []
    for command in ("parts", "precommit"):
        rc, said = _run(root, command, source=CRASHING)
        out += [("a crashing %s exits non-zero (%d)" % (command, rc), rc != 0),
                ("... with the exception", "unexpected keyword argument" in said),
                ("... and where it was raised", "in _build" in said)]
    return out


def _interleave_checks(root):
    """what fcad prints never lands inside what the kernel was printing."""
    import io

    from fcad import _run as run
    rc, raw = _run(root, "check", offset=SIZE, source=CHATTY)
    verdict = "check ok: no overlapping parts"
    kept = io.StringIO()
    run.relay(io.BytesIO(raw.encode()), kept)
    joined = io.StringIO()
    run.relay(io.BytesIO((WORD + verdict + "\n").encode()), joined)
    return [
        ("the kernel's unfinished line is whole, ahead of fcad's (%d, %d)"
         % (raw.find(WORD), raw.find(verdict)),
         0 <= raw.find(WORD) < raw.find(verdict)),
        ("... so none of it survives the filter",
         "Postpr" not in kept.getvalue() and "ocessing" not in kept.getvalue()),
        ("... and the verdict does", verdict in kept.getvalue().splitlines()),
        ("a verdict on the end of a progress word is kept (%r)"
         % joined.getvalue(), joined.getvalue() == verdict + "\n"),
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


def _quiet_checks():
    """the cli drops the kernel's chatter, and must never drop a verdict.

    the lines are the ones a real build writes: OpenCASCADE's banners in colour,
    and a progress bar that redraws with carriage returns and then has fcad's
    own line printed straight after it, on the same line."""
    import io

    from fcad import _run as run
    bar = "\t\t(9 %)\t\r\t\t(54 %)\t\r\t\t(100 %)\t\r\t\t\r"
    noisy = "".join([
        "FreeCAD 1.1.3, Libs: 1.1.3R20260725 (Git shallow)\n",
        "(C) 2001-2026 FreeCAD contributors\n",
        "FreeCAD is free and open-source software licensed under the terms of "
        "LGPL2+ license.\n",
        "\n",
        "\x1b[32;1m** WorkSession : Sending all data\x1b[0m\n",
        "*******************************************************************\n",
        bar + "saving......\n",
        bar + "check failed: 1 overlapping part pair(s):\n",
        "  a_001 <-> b_001 : 6000.0 mm^3\n",
        "\n",
        "check ok: every part is one connected solid\n",
    ])
    out = io.StringIO()
    run.relay(io.BytesIO(noisy.encode()), out)
    kept = out.getvalue().splitlines()
    # the kernel's warnings arrive on stderr, and its progress bars leave tabs
    # in front of whatever is printed next.
    err = io.StringIO()
    run.relay(io.BytesIO(
        b"<TopoShape> TopoShapeExpansion.cpp(922): hasher mismatch\n"
        b"2.9e-08 <App> Document.cpp(2504): The graph must be a DAG.\n"
        b"\t\t\r\t\t  total: 1 board(s)\n"
        b"fcad: unknown command 'nope'\n"), err)
    errs = err.getvalue().splitlines()
    # an addon's Init prints whatever it likes while FreeCAD starts, and no
    # pattern can list every addon: everything ahead of the entry's own marker
    # is startup. with no marker FreeCAD never got as far as fcad, and what it
    # said instead is the only explanation there is.
    addon = b"Some Addon: Init loaded\nSome Addon: Auto-start = False\n"
    started, crashed = io.StringIO(), io.StringIO()
    run.relay(io.BytesIO(addon + run.BEGIN.encode() + b"\ncheck ok: fine\n"),
              started, begin=run.BEGIN)
    run.relay(io.BytesIO(addon), crashed, begin=run.BEGIN)
    return [
        ("what an addon prints while FreeCAD starts is dropped (%r)"
         % started.getvalue(), started.getvalue() == "check ok: fine\n"),
        ("... and kept when fcad's entry never started (%r)" % crashed.getvalue(),
         crashed.getvalue() == addon.decode()),
        ("benign kernel warnings are dropped, fcad's own errors kept (%s)" % errs,
         errs == ["  total: 1 board(s)", "fcad: unknown command 'nope'"]),
        ("FreeCAD's and the kernel's banners and progress bars are dropped "
         "(%d lines kept)"
         % len(kept), len(kept) == 4),
        ("a verdict printed after a progress bar survives it",
         kept[0] == "check failed: 1 overlapping part pair(s):"),
        ("its detail lines keep their indentation",
         kept[1] == "  a_001 <-> b_001 : 6000.0 mm^3"),
        ("a blank line fcad printed is kept", kept[2] == ""),
    ]


def _begin_checks(root):
    """the marker `relay` waits for has to arrive after FreeCAD's own startup.

    the console writes through C stdio and python through its own buffer, so
    into a pipe the marker once overtook the banner it was meant to follow, and
    every startup line came out after it."""
    from fcad import _run as run
    rc, out = _run(root, "check", offset=SIZE,
                   env_extra={run.BEGIN_ENV: run.BEGIN})
    lines = out.splitlines()
    banner = [i for i, l in enumerate(lines) if l.startswith("FreeCAD ")]
    marks = [i for i, l in enumerate(lines) if l == run.BEGIN]
    return [("the entry marks where startup ends, once (%s)" % marks,
             len(marks) == 1),
            ("... and FreeCAD's banner is ahead of it (%s < %s)" % (banner, marks),
             bool(banner and marks) and max(banner) < marks[0])]


def main():
    root = tempfile.mkdtemp()
    try:
        checks = (_check_checks(root) + _fem_checks(root) + _quiet_checks()
                  + _begin_checks(root) + _crash_checks(root)
                  + _interleave_checks(root))
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
