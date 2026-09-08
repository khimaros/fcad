"""the unified fcad cli: the `fcad` console script and only entrypoint.

it routes each command to the interpreter it needs (the user never chooses): the
headless build/validate commands and the gui inspect commands run the in-FreeCAD
entry under freecadcmd/freecad; render/animate run in-process under this python;
diff orchestrates a git worktree. configuration (project/name/dist/binaries) is
resolved once from flags + environment and shared with the children.
"""

import argparse
import os
import shutil
import subprocess
import sys

from fcad import __version__, config, cutlist
from fcad._run import run_entry
# the animation vocabulary only; fcad.render is import-free at package level, so
# naming these in --help costs nothing (importing the renderers pulls matplotlib).
from fcad.render import (CAMERAS, FEM_SECONDS, MESH_SECONDS, MESH_SUBJECTS,
                         ORDERS, SUBJECTS)

# headless build/validate commands handled by freecad/_entry's dispatch path.
BUILD_TARGETS = ["parts", "assembly", "step", "stl", "svg", "dxf",
                 "drawings", "sketches", "bom", "cutlist"]

# the agent skill fcad ships, installed into a claude skills directory.
SKILL_NAME = "freecad-python"
SKILL_DIR = os.path.expanduser("~/.claude/skills")
# records which freecad the installed `api/` describes. the reference is only
# valid for the build that produced it, and `freecadcmd --version` is a 50ms
# answer to "has that changed", so a reinstall costs nothing when it has not.
SKILL_STAMP = "BUILD"


def _build_parser():
    p = argparse.ArgumentParser(
        prog="fcad",
        description="build, export, validate and inspect a parametric FreeCAD model.")
    p.add_argument("--version", action="version", version="fcad " + __version__)
    p.add_argument("-p", "--project", metavar="PATH",
                   help="dir holding project.py, or a .fcad file "
                        "(env FCAD_PROJECT, default .)")
    p.add_argument("-n", "--name", metavar="STEM",
                   help="output stem (env FCAD_NAME, default project basename)")
    p.add_argument("-d", "--dist", metavar="DIR",
                   help="output dir (env FCAD_DIST, default <project>/dist)")
    p.add_argument("--freecad", metavar="BIN",
                   help="headless binary (env FREECAD, default freecadcmd)")
    p.add_argument("--freecad-gui", metavar="BIN", dest="freecad_gui",
                   help="gui binary (env FREECAD_GUI, default freecad)")

    sub = p.add_subparsers(dest="command", metavar="<command>")

    b = sub.add_parser("build", help="build/export into dist/ (default: all)")
    b.add_argument("targets", nargs="*", metavar="TARGET",
                   help="one or more of: " + " ".join(BUILD_TARGETS) + " (none = all)")
    # cutlist knobs: what stock is buyable, and what the shop loses cutting it.
    b.add_argument("--stock", action="append", metavar="SPEC",
                   help="cutlist stock lengths, overriding the project's STOCK: "
                        "'8ft,10ft,12ft' or '2x6=8ft,12ft' (mm if unsuffixed; "
                        "repeatable)")
    b.add_argument("--kerf", metavar="MM",
                   help="cutlist saw kerf between adjacent cuts (default %g)"
                        % cutlist.DEFAULT_KERF)
    b.add_argument("--trim", metavar="MM",
                   help="cutlist trim allowance docked off each board "
                        "(default %g)" % cutlist.DEFAULT_TRIM)
    b.add_argument("--objective", choices=["length", "boards"],
                   help="cutlist goal: least purchased length (default) or "
                        "fewest boards")
    sub.add_parser("check", help="interference + constraint validation")
    sub.add_parser("precommit", help="build all, then check")

    v = sub.add_parser("view", help="open the assembly (or 'view parts') in the gui")
    v.add_argument("what", nargs="?", choices=["parts"], metavar="[parts]")
    v.add_argument("--part", metavar="NAME", help="with 'parts': open only this part")

    r = sub.add_parser("render", help="offscreen png of a built stl")
    r.add_argument("target", nargs="?", default="assembly",
                   help="part name or 'assembly' (default)")
    r.add_argument("out", nargs="?", metavar="PNG",
                   help="output file (default <dist>/<target>.png)")
    a = sub.add_parser("animate", help="turntable mp4 + gif of a built stl")
    a.add_argument("target", nargs="?", default="assembly",
                   help="part name or 'assembly' (default)")
    a.add_argument("stem", nargs="?", metavar="STEM",
                   help="output path stem, .mp4/.gif appended "
                        "(default <dist>/spin_<target>)")
    a.add_argument("--camera", choices=CAMERAS, default="orbit",
                   help="orbit every side incl. the underside (default), spin "
                        "level, or hold FCAD_ELEV/FCAD_AZIM")
    a.add_argument("--subject", choices=MESH_SUBJECTS,
                   help="'assemble' flies the parts in one by one, 'static' "
                        "holds the model whole. default: assemble for the "
                        "assembly, static for a single part, which has nothing "
                        "to assemble")
    a.add_argument("--seconds", type=float, metavar="N",
                   help="clip length (default %g)" % MESH_SECONDS)
    a.add_argument("--fps", type=int, metavar="N",
                   help="frames per second; raise for smoother, lower for a "
                        "smaller file (default 10)")
    a.add_argument("--order", choices=ORDERS, default="grounded",
                   help="with 'assemble': the sequence parts arrive in "
                        "(default outward from the part flagged `grounded` over "
                        "what touches what, fasteners last)")

    f = sub.add_parser("fem", help="solve FEM (von Mises + displacement; headless)")
    f.add_argument("target", nargs="?", default="assembly",
                   help="part name, 'assembly', or 'all' for every declared case")
    f.add_argument("--modal", action="store_true", help="also compute eigenmodes")
    f.add_argument("--modes", type=int, metavar="K",
                   help="eigenmode count (implies --modal)")
    fr = sub.add_parser("fem-render", help="png of a solved FEM result")
    fr.add_argument("target", nargs="?", default="assembly",
                    help="part name or 'assembly' (default)")
    fr.add_argument("out", nargs="?", metavar="PNG",
                    help="output file (default <dist>/fem_<target>.png)")
    fa = sub.add_parser("fem-animate",
                        help="deformation + modal mp4/gif of a FEM result")
    fa.add_argument("target", nargs="?", default="assembly",
                    help="part name or 'assembly' (default)")
    fa.add_argument("stem", nargs="?", metavar="STEM",
                    help="output path stem, .mp4/.gif appended "
                         "(default <dist>/fem_<target>)")
    fa.add_argument("--camera", choices=CAMERAS, default="orbit",
                    help="orbit every side incl. the underside (default), spin "
                        "level, or hold FCAD_ELEV/FCAD_AZIM")
    fa.add_argument("--seconds", type=float, metavar="N",
                    help="length of each clip (default %g)" % FEM_SECONDS)
    fa.add_argument("--fps", type=int, metavar="N",
                    help="frames per second; raise for smoother, lower for a "
                         "smaller file (default 12)")
    fa.add_argument("--subject", choices=SUBJECTS, default="all",
                    help="what moves: flex + one clip per mode (default), just "
                         "the flex, just the modes, or 'static' to hold peak "
                         "deflection and let the camera do the work")

    for name, help_ in (("diff", "3d diff vs git HEAD (compute, then open)"),
                        ("diff-build", "compute + save the diff headless (slow part)"),
                        ("diff-open", "open a precomputed diff in the gui (instant)")):
        d = sub.add_parser(name, help=help_)
        d.add_argument("target", nargs="?", default="assembly")

    sub.add_parser("pdf", help="dimensioned techdraw pdfs (gui)")
    ad = sub.add_parser("api-docs",
                        help="freecad api reference for the installed build")
    ad.add_argument("out", nargs="?", metavar="DIR",
                    help="output dir (default <dist>/api)")
    sub.add_parser("clean", help="remove dist/")
    sub.add_parser("info", help="print the resolved configuration")
    m = sub.add_parser("install-macro", help="install the rebuild macro into FreeCAD")
    m.add_argument("--dir", metavar="DIR", dest="macro_dir",
                   help="macro directory (default ~/.local/share/FreeCAD/Macro)")
    sub.add_parser("install-git",
                   help="make `git diff` on a .fcad file open the 3d diff")
    sk = sub.add_parser("install-skill",
                        help="install/refresh the freecad-python agent skill")
    sk.add_argument("--dir", metavar="DIR", dest="skill_dir",
                    help="skills directory (default " + SKILL_DIR + ")")
    sk.add_argument("--force", action="store_true",
                    help="regenerate even when the freecad build is unchanged")
    g = sub.add_parser("git-diff",
                       help="git's external diff driver (git calls this itself; "
                            "register it with install-git)")
    g.add_argument("args", nargs="*", metavar="ARG",
                   help="path old-file old-hex old-mode new-file new-hex new-mode")
    h = sub.add_parser("help", help="show usage (optionally for one command)")
    h.add_argument("topic", nargs="?", metavar="[COMMAND]")
    p.subparsers = sub
    return p


def _help(parser, topic):
    """print top-level usage, or one command's usage when topic names it."""
    choices = parser.subparsers.choices
    if topic and topic not in choices:
        parser.error("unknown command: " + topic)
    (choices[topic] if topic else parser).print_help()


def _clean(cfg):
    shutil.rmtree(cfg.dist, ignore_errors=True)
    print("cleaned " + cfg.dist)


def _info(cfg):
    for k, val in (("project", cfg.project), ("entry", cfg.entry or "(project.py)"),
                   ("name", cfg.name), ("dist", cfg.dist),
                   ("freecad", cfg.freecad), ("freecad-gui", cfg.freecad_gui)):
        print("%-11s %s" % (k, val))


def _install_macro(macro_dir=None):
    import fcad
    pkg = os.path.dirname(os.path.abspath(fcad.__file__))
    template = os.path.join(pkg, "resources", "macros", "rebuild.FCMacro")
    with open(template) as f:
        text = f.read().replace("__FCAD_PKG_PARENT__", os.path.dirname(pkg))
    macro_dir = macro_dir or os.path.expanduser("~/.local/share/FreeCAD/Macro")
    os.makedirs(macro_dir, exist_ok=True)
    out = os.path.join(macro_dir, "fcad_rebuild.FCMacro")
    with open(out, "w") as f:
        f.write(text)
    print("installed macro: " + out)


def _freecad_build(cfg):
    """the `freecadcmd --version` line, or "" if it cannot be asked."""
    try:
        r = subprocess.run([cfg.freecad, "--version"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout or r.stderr).strip()


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _install_wiki(source, dest):
    """copy the curated wiki subset in, file by file.

    deliberately not a tree replace: someone may have cloned the full 2630-page
    export into this directory, and those extra pages are theirs to keep. we
    overwrite only the names we ship and never delete."""
    if not os.path.isdir(source):
        return 0
    os.makedirs(dest, exist_ok=True)
    pages = sorted(f for f in os.listdir(source) if f.endswith(".md"))
    for page in pages:
        shutil.copyfile(os.path.join(source, page), os.path.join(dest, page))
    return len(pages)


def _install_skill(cfg, skill_dir=None, force=False):
    """install (or refresh) the bundled agent skill: prose, wiki, api reference.

    the skill is shipped by fcad rather than hand-maintained because its api
    reference has to come from the freecad actually installed, which only the
    machine running it knows. re-running is the update path: the reference is
    pinned to a build, so it is regenerated when that build changes and skipped
    when it has not."""
    import fcad
    pkg = os.path.dirname(os.path.abspath(fcad.__file__))
    skill = os.path.join(pkg, "resources", "skills", SKILL_NAME)
    source = os.path.join(skill, "SKILL.md")
    out = os.path.join(skill_dir or SKILL_DIR, SKILL_NAME)
    api = os.path.join(out, "api")
    stamp = os.path.join(api, SKILL_STAMP)

    build = _freecad_build(cfg)
    wanted = _read(source)
    current = (build and (_read(stamp) or "").strip() == build
               and _read(os.path.join(out, "SKILL.md")) == wanted)
    if current and not force:
        print("install-skill: already current for %s -> %s" % (build, out))
        return 0

    os.makedirs(api, exist_ok=True)
    with open(os.path.join(out, "SKILL.md"), "w") as f:
        f.write(wanted)
    pages = _install_wiki(os.path.join(skill, "wiki"), os.path.join(out, "wiki"))
    rc = run_entry(cfg, ["api-docs"], env_extra={"FCAD_API_OUT": api})
    if rc:
        return rc
    if build:
        with open(stamp, "w") as f:
            f.write(build + "\n")
    print("installed skill: %s (%d wiki pages)" % (out, pages))
    return 0


def _cutlist_env(args):
    """export the build's cutlist flags for the in-freecad builder to read back."""
    given = ((cutlist.ENV_STOCK, ";".join(args.stock) if args.stock else None),
             (cutlist.ENV_KERF, args.kerf), (cutlist.ENV_TRIM, args.trim),
             (cutlist.ENV_OBJECTIVE, args.objective))
    return {k: v for k, v in given if v}


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command or args.command == "help":
        _help(parser, getattr(args, "topic", None))
        return 0
    cfg = config.resolve(args.project, args.name, args.dist,
                         args.freecad, args.freecad_gui)
    cmd = args.command

    if cmd == "build":
        targets = args.targets or ["all"]
        rc = 0
        for t in targets:
            rc = run_entry(cfg, [t], env_extra=_cutlist_env(args)) or rc
        return rc
    if cmd in ("check", "precommit"):
        return run_entry(cfg, [cmd])
    if cmd == "view":
        if args.what == "parts":
            return run_entry(cfg, ["view-parts"], gui=True,
                             env_extra={"FCAD_PART": args.part or ""})
        return run_entry(cfg, ["view"], gui=True)
    if cmd == "pdf":
        return run_entry(cfg, ["pdf"], gui=True)
    if cmd == "render":
        from fcad.render import render
        render.render(args.target, args.out, name=cfg.name, dist=cfg.dist)
        return 0
    if cmd == "animate":
        from fcad.render import animate
        # the assembly is the thing worth watching build itself; a single part
        # has nothing to assemble, so it spins instead.
        subject = args.subject or ("assemble" if args.target in
                                   ("assembly", cfg.name) else "static")
        animate.animate(args.target, args.stem, name=cfg.name, dist=cfg.dist,
                        camera=args.camera, subject=subject, order=args.order,
                        seconds=args.seconds, fps=args.fps)
        return 0
    if cmd == "fem":
        env = {"FCAD_TARGET": args.target}
        if args.modes is not None:
            env["FCAD_FEM_MODES"] = str(args.modes)
        elif args.modal:
            env["FCAD_FEM_MODAL"] = "1"
        return run_entry(cfg, ["fem"], env_extra=env)
    if cmd == "fem-render":
        from fcad.render import fem_render
        fem_render.render_static(args.target, args.out, name=cfg.name, dist=cfg.dist)
        return 0
    if cmd == "fem-animate":
        from fcad.render import fem_animate
        fem_animate.animate(args.target, args.stem, name=cfg.name, dist=cfg.dist,
                            camera=args.camera, subject=args.subject,
                            seconds=args.seconds, fps=args.fps)
        return 0
    if cmd in ("diff", "diff-build", "diff-open"):
        from fcad import diff
        fn = {"diff": diff.run, "diff-build": diff.build, "diff-open": diff.open_}[cmd]
        return fn(cfg, args.target)
    if cmd == "install-git":
        from fcad import diff
        return diff.install_git(cfg)
    if cmd == "git-diff":
        from fcad import diff
        return diff.git_diff(cfg, args.args)
    if cmd == "clean":
        _clean(cfg)
        return 0
    if cmd == "info":
        _info(cfg)
        return 0
    if cmd == "install-macro":
        _install_macro(args.macro_dir)
        return 0
    if cmd == "install-skill":
        return _install_skill(cfg, args.skill_dir, args.force)
    if cmd == "api-docs":
        out = os.path.abspath(args.out or os.path.join(cfg.dist, "api"))
        return run_entry(cfg, ["api-docs"], env_extra={"FCAD_API_OUT": out})
    return 2


if __name__ == "__main__":
    sys.exit(main())
