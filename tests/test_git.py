"""end-to-end test for the git integration (`fcad install-git` + `fcad git-diff`).

`install-git` registers fcad as this repo's external diff driver for the built
`.FCStd` documents, so plain `git diff` shows them in 3d instead of "Binary files
differ", and marks the `.fcad` source as python so forges render it. three things
must hold:

  wiring: the driver command and both attributes land in the right files, and
  installing twice changes nothing. which file is not a preference: the driver
  names a command to execute, so it must stay untracked or a clone would run code
  it shipped; the forge language hint is only ever read from a committed file, so
  it must be tracked. getting those two backwards is the failure this pins;

  routing: git really does resolve a committed `.FCStd` to the driver and call it
  with the 7-parameter external-diff contract;

  the diff itself: given two saved `.FCStd` revisions (all git hands the driver),
  it bakes the green/red/grey split without building anything, including when one
  side is /dev/null. it binds the *built documents*, not the `.fcad` source: that
  is python, diffs fine as text, and is what we ask forges to render it as.

only the final gui open is out of scope headlessly. needs FreeCAD and git: run
with `freecadcmd tests/test_git.py`. results go to $RESULT_FILE (freecadcmd
swallows script stdout and exits 0 on error, so a file is the reliable channel).
"""

import os
import shutil
import subprocess
import sys
import tempfile

# make the in-repo fcad package importable under FreeCAD's bundled python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import FreeCAD as App

from fcad import config, diff
from fcad._run import run_entry

# a whole single-file project: one box part, `qty` instances in a row. the diff
# then has a part whose geometry changes and a part whose count changes.
PROJECT = '''
import fcad, Part, FreeCAD as App

PARAMS = {"length": %(length)r, "qty": %(qty)r}

def compute(p):
    box = Part.makeBox(p["length"], 40.0, 20.0)
    places = [App.Placement(App.Vector(0, i * 60.0, 0), App.Rotation())
              for i in range(int(p["qty"]))]
    return [fcad.Part("bar", solid=lambda: box, placements=places)]
'''
NAME = "widget"
WIDTH, THICK, PITCH = 40.0, 20.0, 60.0
V1 = dict(length=100.0, qty=3)
V2 = dict(length=120.0, qty=5)
BAR_V1 = V1["length"] * WIDTH * THICK
SLAB = (V2["length"] - V1["length"]) * WIDTH * THICK
TOL = 1.0


def _write(path, params):
    with open(path, "w") as f:
        f.write(PROJECT % params)
    return path


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _repo(root):
    """a git repo holding one committed revision of the project."""
    os.makedirs(root, exist_ok=True)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    _write(os.path.join(root, NAME + ".fcad"), V1)
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "baseline")
    return root


def _cfg(root):
    return config.resolve(project=root)


def _lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return f.read().splitlines()


def _local_attrs(root):
    """the untracked attributes file, where the diff driver binding must live."""
    return _lines(os.path.join(
        _git(root, "rev-parse", "--path-format=absolute",
             "--git-common-dir").stdout.strip(), "info", "attributes"))


def _tracked_attrs(root):
    """the committed .gitattributes, where the forge language hint must live."""
    return _lines(os.path.join(root, ".gitattributes"))


def _vol(doc, prefix):
    return sum(o.Shape.Volume for o in doc.Objects
               if o.TypeId == "Part::Feature" and o.Name.startswith(prefix))


def _layers(path, label):
    """(green, red, grey) volumes from a baked diff; objects are named after the
    document that was diffed, which for the git driver is its file stem."""
    doc = App.openDocument(path)
    try:
        return tuple(_vol(doc, label + s)
                     for s in ("_added", "_removed", "_unchanged"))
    finally:
        App.closeDocument(doc.Name)


def _wiring(checks, root):
    diff.install_git(_cfg(root))
    command = _git(root, "config", "--local", "--get",
                   "diff.%s.command" % diff.GIT_DRIVER).stdout.strip()
    dirty = [l[3:] for l in _git(root, "status", "--porcelain").stdout.splitlines()]
    checks += [
        ("install-git: driver command registered", command.endswith(" git-diff")),
        ("install-git: driver command is an absolute path",
         os.path.isabs(command.split(" ")[0])),
        # the driver names a command to execute, so it must never be tracked: a
        # clone would then run code it shipped.
        ("install-git: the driver binding stays untracked",
         diff.GIT_ATTR in _local_attrs(root)
         and diff.GIT_ATTR not in _tracked_attrs(root)),
        # a forge only sees committed files, so the language hint must be tracked.
        ("install-git: the forge language hint is tracked",
         diff.GIT_LINGUIST in _tracked_attrs(root)),
        ("install-git: .gitattributes is the only worktree change (%s)" % dirty,
         dirty == [".gitattributes"]),
    ]
    diff.install_git(_cfg(root))
    checks.append(("install-git: idempotent",
                   _local_attrs(root).count(diff.GIT_ATTR) == 1
                   and _tracked_attrs(root).count(diff.GIT_LINGUIST) == 1))
    # git resolving a path to each attribute is the half a file check cannot see.
    for fname, attr, want in ((NAME + ".FCStd", "diff", diff.GIT_DRIVER),
                              (NAME + ".fcad", "linguist-language", "Python"),
                              (NAME + ".fcad", "gitlab-language", "python")):
        got = _git(root, "check-attr", attr, "--", fname).stdout.strip()
        checks.append(("install-git: git binds %s %s (%s)" % (fname, attr, got),
                       got.endswith(": %s: %s" % (attr, want))))
    # the source is python and diffs fine as text; only the built binaries are
    # worth replacing with a 3d view.
    got = _git(root, "check-attr", "diff", "--", NAME + ".fcad").stdout.strip()
    checks.append(("install-git: .fcad keeps its ordinary text diff (%s)" % got,
                   got.endswith(": diff: unspecified")))


def _routing(checks, root, built):
    """git must actually call the driver on a committed, then changed, .FCStd."""
    log = os.path.join(root, "driver.log")
    recorder = os.path.join(root, "recorder.sh")
    with open(recorder, "w") as f:
        f.write('#!/bin/sh\nprintf "%s\\n" "$#" "$1" > ' + log + '\n')
    os.chmod(recorder, 0o755)
    _git(root, "config", "--local", "diff.%s.command" % diff.GIT_DRIVER, recorder)

    tracked = os.path.join(root, NAME + ".FCStd")
    shutil.copyfile(built, tracked)
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "built artifact")
    with open(tracked, "ab") as f:      # any content change is enough to diff
        f.write(b"\0")
    _git(root, "diff")

    seen = open(log).read().split() if os.path.exists(log) else []
    checks += [
        ("git diff: the driver was called on the .FCStd", bool(seen)),
        ("git diff: called with 7 parameters", seen[:1] == ["7"]),
        ("git diff: first parameter is the path", seen[1:2] == [NAME + ".FCStd"]),
    ]


def _build(work, tag, params):
    """build one revision's dist/, returning (assembly .FCStd, part .FCStd)."""
    root = os.path.join(work, tag)
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, NAME + ".fcad"), params)
    # parts first: build_jointed_doc only links a part whose file already exists.
    for target in ("parts", "assembly"):
        rc = run_entry(config.resolve(project=root), [target])
        if rc:
            raise RuntimeError("building %s %s failed (rc=%d)" % (tag, target, rc))
    dist = os.path.join(root, "dist")
    return (os.path.join(dist, NAME + ".FCStd"),
            os.path.join(dist, "parts", "bar.FCStd"))


def _diff_build(checks, root, work):
    """the driver's headless half, on the revision pair git would hand it.

    git diffs one file at a time, and the two built documents record different
    things: a part file holds its own solid, so it diffs as geometry; an assembly
    holds only links, so it diffs as placements. both are asserted."""
    old_asm, old_part = _build(work, "old", V1)
    new_asm, new_part = _build(work, "new", V2)
    cfg = _cfg(root)

    # the path git passes is the document's real location on both sides (only its
    # *content* differs per revision), which is what relative links resolve from.
    # a part document: geometry only, one instance, so the bar's growth shows.
    out = diff.git_diff_build(cfg, new_part, old_part, new_part,
                              os.path.join(work, "part"))
    added, removed, grey = _layers(out, "bar")
    checks += [
        ("git-diff part: baked a diff document", os.path.exists(out)),
        ("git-diff part: added = the slab the bar grew by (%.0f vs %.0f)"
         % (added, SLAB), abs(added - SLAB) < TOL),
        ("git-diff part: nothing removed (%.0f)" % removed, abs(removed) < TOL),
        ("git-diff part: the original bar survives (%.0f vs %.0f)" % (grey, BAR_V1),
         abs(grey - BAR_V1) < TOL),
    ]

    # an assembly document: links resolve their shape from the *current* part
    # files, so what this diff reports is the instance count going 3 -> 5.
    out = diff.git_diff_build(cfg, new_asm, old_asm, new_asm,
                              os.path.join(work, "asm"))
    added, removed, grey = _layers(out, NAME)
    bar = BAR_V1 + SLAB   # the links resolve to the new (grown) part on both sides
    checks += [
        ("git-diff assembly: added = the 2 new instances (%.0f vs %.0f)"
         % (added, 2 * bar), abs(added - 2 * bar) < TOL),
        ("git-diff assembly: nothing removed (%.0f)" % removed, abs(removed) < TOL),
        ("git-diff assembly: the 3 kept instances stay grey (%.0f vs %.0f)"
         % (grey, 3 * bar), abs(grey - 3 * bar) < TOL),
    ]

    # git passes /dev/null for a side where the file does not exist.
    out = diff.git_diff_build(cfg, new_part, os.devnull, new_part,
                              os.path.join(work, "created"))
    added, removed, _ = _layers(out, "bar")
    checks += [
        ("git-diff: a created document is wholly green (%.0f vs %.0f)"
         % (added, BAR_V1 + SLAB), abs(added - (BAR_V1 + SLAB)) < TOL),
        ("git-diff: a created document removes nothing (%.0f)" % removed,
         abs(removed) < TOL),
    ]
    out = diff.git_diff_build(cfg, old_part, old_part, os.devnull,
                              os.path.join(work, "deleted"))
    added, removed, _ = _layers(out, "bar")
    checks += [
        ("git-diff: a deleted document is wholly red (%.0f vs %.0f)"
         % (removed, BAR_V1), abs(removed - BAR_V1) < TOL),
        ("git-diff: a deleted document adds nothing (%.0f)" % added, abs(added) < TOL),
    ]
    return new_part


def main():
    checks = []
    tmp = tempfile.mkdtemp()
    try:
        root = _repo(os.path.join(tmp, "repo"))
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        _wiring(checks, root)
        built = _diff_build(checks, root, work)
        _routing(checks, root, built)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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


if any(a.endswith("test_git.py") for a in sys.argv):
    raise SystemExit(main())
