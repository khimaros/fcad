"""orchestrate the 3d geometry diff (the diff/diff-build/diff-open/git-diff cmds).

building the other revision's geometry and the booleans is the slow part, so it
runs headless and bakes the green/red/grey split into a document of static
solids; opening that in the gui is then instant (no booleans re-run). `diff`
chains build then open; `diff-build`/`diff-open` let the slow bake run in the
background and be viewed later.

two ways in, sharing one compute step:

- `diff` compares the *design* - the working tree against git HEAD - by building
  HEAD's geometry in a throwaway `git worktree` and diffing the two dist/ trees.
  we diff the design, not the tooling: each side is built from that revision's own
  copy of the project, but this same installed fcad drives both.
- `git-diff` is the external diff driver `install-git` registers, so plain
  `git diff` on a built `.FCStd` shows the 3d diff instead of "Binary files
  differ". it builds nothing: git hands it both revisions as files and they are
  already-built documents, so it compares whatever revisions git was asked about.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from fcad._run import run_entry

DIFF_SUFFIX = ".diff.FCStd"
# the git diff driver's name: `[diff "fcad"] command = ...` plus an attribute
# line binding the built documents to it.
GIT_DRIVER = "fcad"
# the 3d diff replaces "Binary files differ" on the built documents. a `.fcad`
# file is python and already diffs perfectly well as the source it is - which is
# also what we ask forges to render it as - so it is deliberately not bound here.
GIT_ATTR = "*.FCStd diff=" + GIT_DRIVER
# forges pick a language by file extension, so a .fcad file renders as plain text
# until told otherwise, even though it is python. github (linguist) and gitlab
# read different attributes for that, and both are cheap to state.
GIT_LINGUIST = "*.fcad linguist-language=Python gitlab-language=python"


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


def _compute(cfg, new, old, target, out, name, docs=False):
    """bake the green/red/grey split into `out`.

    `new`/`old` are two built dist/ trees, or two saved .FCStd revisions of one
    document when `docs` is set."""
    if os.path.exists(out):
        os.remove(out)
    print("computing 3d diff for %r (headless)..." % target)
    run_entry(cfg, ["diff-doc"], env_extra={
        "DIFF_NEW": new, "DIFF_OLD": old, "DIFF_TARGET": target,
        "DIFF_OUT": out, "FCAD_NAME": name, "DIFF_DOCS": "1" if docs else ""})
    # freecadcmd exits 0 even when the script raised, so believe the artifact
    # rather than the return code.
    if not os.path.exists(out):
        sys.exit("diff: could not compute the 3d diff (no %s written)" % out)


def _head_env(cfg, tmp):
    """point the HEAD child at the worktree's own copy of the design.

    every FCAD_* var is already in the child's environment as an absolute path
    into the working tree, so pointing FCAD_PROJECT at the worktree is not
    enough: a `.fcad` project is named by FCAD_ENTRY, and left alone the HEAD
    build would load the working tree's design and diff it against itself."""
    entry = (os.path.join(tmp, os.path.relpath(cfg.entry, cfg.project))
             if cfg.entry else "")
    return {"FCAD_PROJECT": tmp, "FCAD_ENTRY": entry,
            "FCAD_DIST": os.path.join(tmp, "dist")}


def build(cfg, target="assembly"):
    """compute the diff vs HEAD headless and save it as baked solids."""
    root = cfg.project
    _require_git(root)
    tmp = tempfile.mkdtemp()
    try:
        if _git(root, "worktree", "add", "--detach", "--quiet", tmp, "HEAD").returncode:
            sys.exit("diff: could not create the HEAD worktree")
        print("building HEAD geometry...")
        head = run_entry(cfg, ["step"], env_extra=_head_env(cfg, tmp))
        if head:
            sys.exit("diff: HEAD build failed (rc=%d)" % head)
        out = _diff_path(cfg, target)
        _compute(cfg, cfg.dist, os.path.join(tmp, "dist"), target, out, cfg.name)
        print("open it with: fcad diff-open %s" % target)
        return 0
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


def _stage_doc(src, root, name, beside):
    """put one revision's document where FreeCAD will open *and* resolve it.

    git hands each side over as a temporary file named however it likes, and
    FreeCAD keys on the `.FCStd` extension, so each side is copied into its own
    directory under the document's real name. an assembly's links into its part
    files are stored *relative* to the document, though, so a copy staged
    anywhere else would resolve to nothing: mirror the real file's sibling
    directories (`parts/`) beside the copy as symlinks. only directories are
    mirrored, so the copy can never be written through a link onto the real
    document. `/dev/null` is how git spells a side where the file does not exist;
    that stages nothing and the diff reads the other side as wholly added (or
    removed)."""
    if not src or os.path.realpath(src) == os.devnull:
        return ""
    os.makedirs(root, exist_ok=True)
    if os.path.isdir(beside):
        for entry in os.listdir(beside):
            target, link = os.path.join(beside, entry), os.path.join(root, entry)
            if os.path.isdir(target) and not os.path.exists(link):
                os.symlink(target, link)
    dest = os.path.join(root, name + ".FCStd")
    shutil.copyfile(src, dest)
    return dest


def git_diff_build(cfg, path, old_file, new_file, root):
    """bake the diff between two saved revisions of one .FCStd.

    nothing is built: both sides are already-built documents, so this only opens
    them. an assembly document carries no geometry, only links into the part
    files, so both revisions resolve their shapes from the parts on disk now -
    which is right, because what an assembly file records is its parts'
    placements, and a part's own geometry changing is a change to the part file
    that git diffs separately."""
    name = os.path.splitext(os.path.basename(path))[0]
    beside = os.path.dirname(os.path.abspath(path))
    old = _stage_doc(old_file, os.path.join(root, "old"), name, beside)
    new = _stage_doc(new_file, os.path.join(root, "new"), name, beside)
    os.makedirs(root, exist_ok=True)
    out = os.path.join(root, name + DIFF_SUFFIX)
    _compute(cfg, new, old, name, out, name, docs=True)
    return out


def git_diff(cfg, argv):
    """git's external diff driver for a `.FCStd` path (registered by install-git).

    git calls this with `path old-file old-hex old-mode new-file new-hex new-mode`
    (or with `path` alone when the path is unmerged), where either file may be the
    working file, a temporary checkout, or /dev/null. it must exit 0 whatever it
    finds: git treats any other status as a fatal error, so a failure is reported
    on stdout, where a diff driver's output belongs."""
    if not argv:
        sys.exit("git-diff: git calls this with the path it is diffing")
    path = argv[0]
    if len(argv) < 7:
        print("fcad: %s is unmerged, so there is no revision pair to diff" % path)
        return 0
    tmp = tempfile.mkdtemp()
    try:
        print("fcad: diffing both revisions of %s in 3d..." % path)
        out = git_diff_build(cfg, path, argv[1], argv[4], tmp)
        print("fcad: opening the 3d diff (close the FreeCAD window to continue)...")
        run_entry(cfg, ["view-diff"], gui=True,
                  env_extra={"FCAD_DIFF": out, "DIFF_TARGET": "assembly",
                             "FCAD_NAME": os.path.splitext(os.path.basename(path))[0]})
    except SystemExit as exc:
        print("fcad: %s" % exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def _add_attr(path, line):
    """append an attribute line unless it is already there; True if it was added."""
    text = ""
    if os.path.exists(path):
        with open(path) as f:
            text = f.read()
    if line in text.splitlines():
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(("" if not text or text.endswith("\n") else "\n") + line + "\n")
    return True


def install_git(cfg):
    """teach this repo to show a `.fcad` file as the 3d diff, and forges to
    render it as the python it is.

    the two attributes have to live in different files, and which goes where is
    forced. git will not run a command that a tracked file names - a clone would
    then execute the code it shipped - so the diff driver is local config plus
    `.git/info/attributes`, and teammates each run this once. a forge, on the
    other hand, only ever sees committed files, so the language hint has to be
    the tracked `.gitattributes`: it is the one line here worth committing."""
    root = cfg.project
    _require_git(root)
    # git runs the driver through a shell, so quote the path we were invoked as.
    command = "%s git-diff" % shlex.quote(os.path.abspath(sys.argv[0]))
    if _git(root, "config", "--local", "diff.%s.command" % GIT_DRIVER,
            command).returncode:
        sys.exit("install-git: could not set diff.%s.command" % GIT_DRIVER)
    git_dir = _git(root, "rev-parse", "--path-format=absolute",
                   "--git-common-dir").stdout.strip()
    top = _git(root, "rev-parse", "--show-toplevel").stdout.strip() or root
    local = os.path.join(git_dir, "info", "attributes")
    tracked = os.path.join(top, ".gitattributes")
    _add_attr(local, GIT_ATTR)
    _add_attr(tracked, GIT_LINGUIST)
    print("diff.%s.command = %s" % (GIT_DRIVER, command))
    print("%s: %s" % (local, GIT_ATTR))
    print("%s: %s" % (tracked, GIT_LINGUIST))
    print("`git diff` on a .fcad file now opens the 3d diff")
    print("commit .gitattributes so forges render .fcad as python")
    return 0
