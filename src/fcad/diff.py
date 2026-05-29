"""orchestrate the 3d geometry diff vs git HEAD (the diff/diff-build/diff-open cmds).

building the HEAD geometry and the booleans is the slow part: it runs headless in
a throwaway `git worktree` checked out at HEAD and bakes the green/red/grey split
into <dist>/<target>.diff.FCStd. opening that document in the gui is then instant
(no booleans re-run). `diff` chains build then open; `diff-build`/`diff-open` let
the slow bake run in the background and be viewed later.

we diff the design, not the tooling: the HEAD build uses the project's own
project.py from the worktree, but this same installed fcad drives it.
"""

import os
import shutil
import subprocess
import sys
import tempfile

from fcad._run import run_entry

DIFF_SUFFIX = ".diff.FCStd"


def _diff_path(cfg, target):
    return os.path.join(cfg.dist, target + DIFF_SUFFIX)


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root,
                          capture_output=True, text=True)


def _require_git(root):
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        sys.exit("diff needs a git repo: run 'git init' and commit a baseline first")
    if _git(root, "rev-parse", "HEAD").returncode != 0:
        sys.exit("diff needs at least one commit to compare against")


def build(cfg, target="assembly"):
    """compute the diff vs HEAD headless and save it as baked solids."""
    root = cfg.project
    _require_git(root)
    tmp = tempfile.mkdtemp()
    try:
        if _git(root, "worktree", "add", "--detach", "--quiet", tmp, "HEAD").returncode:
            sys.exit("diff: could not create the HEAD worktree")
        # build HEAD geometry from the worktree's own project.py; its dist/ lands
        # in the worktree (the project derives dist from its own root), so leave
        # FCAD_DIST unset for this child and only point the project at tmp.
        print("building HEAD geometry...")
        head = run_entry(cfg, ["step"], env_extra={"FCAD_PROJECT": tmp,
                                                    "FCAD_DIST": os.path.join(tmp, "dist")})
        if head:
            sys.exit("diff: HEAD build failed (rc=%d)" % head)
        out = _diff_path(cfg, target)
        print("computing 3d diff for %r (headless)..." % target)
        rc = run_entry(cfg, ["diff-doc"], env_extra={
            "DIFF_NEW": cfg.dist, "DIFF_OLD": os.path.join(tmp, "dist"),
            "DIFF_TARGET": target, "DIFF_OUT": out})
        if rc == 0:
            print("open it with: fcad diff-open %s" % target)
        return rc
    finally:
        _git(root, "worktree", "remove", "--force", tmp)
        shutil.rmtree(tmp, ignore_errors=True)


def open_(cfg, target="assembly"):
    """open a precomputed diff document in the gui (instant, no booleans)."""
    out = _diff_path(cfg, target)
    if not os.path.exists(out):
        sys.exit("no precomputed diff at %s: run 'fcad diff-build %s' first"
                 % (out, target))
    print("opening 3d diff for %r (close the FreeCAD window to finish)..." % target)
    return run_entry(cfg, ["view-diff"], gui=True,
                     env_extra={"FCAD_DIFF": out, "DIFF_TARGET": target})


def run(cfg, target="assembly"):
    """the one-shot convenience: compute headless, then open in the gui."""
    rc = build(cfg, target)
    return rc or open_(cfg, target)
