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
import sys

from fcad import __version__, config, cutlist
from fcad._run import run_entry

# headless build/validate commands handled by freecad/_entry's dispatch path.
BUILD_TARGETS = ["parts", "assembly", "step", "stl", "svg", "dxf",
                 "drawings", "sketches", "bom", "cutlist"]


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
    r.add_argument("target", nargs="?", default="assembly")
    r.add_argument("out", nargs="?")
    a = sub.add_parser("animate", help="turntable mp4 + gif of a built stl")
    a.add_argument("target", nargs="?", default="assembly")
    a.add_argument("stem", nargs="?")

    f = sub.add_parser("fem", help="solve FEM (von Mises + displacement; headless)")
    f.add_argument("target", nargs="?", default="assembly")
    f.add_argument("--modal", action="store_true", help="also compute eigenmodes")
    f.add_argument("--modes", type=int, metavar="K",
                   help="eigenmode count (implies --modal)")
    fr = sub.add_parser("fem-render", help="png of a solved FEM result")
    fr.add_argument("target", nargs="?", default="assembly")
    fr.add_argument("out", nargs="?")
    fa = sub.add_parser("fem-animate",
                        help="deformation + modal mp4/gif of a FEM result")
    fa.add_argument("target", nargs="?", default="assembly")
    fa.add_argument("stem", nargs="?")

    for name, help_ in (("diff", "3d diff vs git HEAD (compute, then open)"),
                        ("diff-build", "compute + save the diff headless (slow part)"),
                        ("diff-open", "open a precomputed diff in the gui (instant)")):
        d = sub.add_parser(name, help=help_)
        d.add_argument("target", nargs="?", default="assembly")

    sub.add_parser("pdf", help="dimensioned techdraw pdfs (gui)")
    sub.add_parser("clean", help="remove dist/")
    sub.add_parser("info", help="print the resolved configuration")
    m = sub.add_parser("install-macro", help="install the rebuild macro into FreeCAD")
    m.add_argument("--dir", metavar="DIR", dest="macro_dir",
                   help="macro directory (default ~/.local/share/FreeCAD/Macro)")
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
        animate.animate(args.target, args.stem, name=cfg.name, dist=cfg.dist)
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
        fem_animate.animate(args.target, args.stem, name=cfg.name, dist=cfg.dist)
        return 0
    if cmd in ("diff", "diff-build", "diff-open"):
        from fcad import diff
        fn = {"diff": diff.run, "diff-build": diff.build, "diff-open": diff.open_}[cmd]
        return fn(cfg, args.target)
    if cmd == "clean":
        _clean(cfg)
        return 0
    if cmd == "info":
        _info(cfg)
        return 0
    if cmd == "install-macro":
        _install_macro(args.macro_dir)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
