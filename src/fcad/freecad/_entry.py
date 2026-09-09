"""bootstrap fcad inside FreeCAD and route one command to its handler.

the cli runs this as a script under the freecad binary it picked:
`freecadcmd <this> build stl`, `freecad <this> view`. FreeCAD's bundled python
does not know about the installed fcad package, so we put the package's parent
directory on sys.path first, then dispatch the command. the cli has already chosen
the right binary (headless vs gui) and exported the FCAD_* / DIFF_* env vars.
"""

import os
import sys


def _bootstrap():
    # .../fcad/freecad/_entry.py -> the directory that holds the fcad package
    pkg_parent = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)


def main(argv):
    """route one command, making sure its failure survives the trip out.

    freecadcmd's embedded interpreter loses a failure two ways, and a command that
    explained itself perfectly well still reached the user as a bare exit status:

    - it discards whatever python holds buffered on stdout when a command exits
      non-zero, and stdout is block-buffered whenever it is not a tty - so a
      redirected or piped `fcad check` failure printed nothing at all, while the
      same run on a terminal printed in full. hence the flush.
    - it never prints the message a `SystemExit` carries, so the deliberate
      diagnostics ("CalculiX produced no result for ... try a smaller mesh_size")
      vanished outright. so we print those ourselves and exit 1."""
    _bootstrap()
    try:
        _route(argv[0] if argv else "all", argv[1:])
    except SystemExit as e:
        if not isinstance(e.code, str):
            raise
        print(e.code)
        raise SystemExit(1)
    finally:
        sys.stdout.flush()


def _route(cmd, args=()):
    if cmd == "test":
        # a project test, run here rather than directly under freecadcmd so it
        # inherits the sys.path bootstrap above and can import fcad.testing.
        from fcad.freecad import run_test
        run_test.main(args[0])
    elif cmd == "view":
        from fcad.freecad import view
        view.main()
    elif cmd == "view-parts":
        from fcad.freecad import view_parts
        view_parts.main()
    elif cmd == "pdf":
        from fcad.freecad import export_pdf
        export_pdf.main()
    elif cmd == "diff-doc":
        from fcad.freecad import diff_doc
        diff_doc.main()
    elif cmd == "view-diff":
        from fcad.freecad import view_diff
        view_diff.main()
    elif cmd == "fem":
        from fcad.freecad import fem
        fem.main(os.environ.get("FCAD_TARGET", "assembly"))
    elif cmd == "api-docs":
        # documents the freecad build, not a model, so it loads no project.
        from fcad.freecad import api_docs
        api_docs.main()
    else:
        from fcad.freecad import dispatch
        from fcad.loader import load_project
        if cmd not in dispatch.TARGETS and cmd not in ("check", "precommit",
                                                       "optimize"):
            sys.stderr.write("fcad: unknown command %r\n" % cmd)
            raise SystemExit(2)
        project = load_project()
        if cmd == "check":
            dispatch.check(project)
        elif cmd == "precommit":
            dispatch.precommit(project)
        elif cmd == "optimize":
            dispatch.optimize(project)
        else:
            dispatch.run(project, cmd)


# freecadcmd sets __name__ to the file stem (not "__main__"), so detect being run
# as a script by finding this file on argv and take whatever follows as the command.
if __name__ == "__main__" or any(a.endswith("_entry.py") for a in sys.argv):
    _i = next((i for i, a in enumerate(sys.argv) if a.endswith("_entry.py")), 0)
    main(sys.argv[_i + 1:])
