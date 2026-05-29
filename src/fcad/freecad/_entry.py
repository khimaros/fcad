"""bootstrap fcad inside FreeCAD and route one command to its handler.

the cli runs this as a script under the freecad binary it picked:
`freecadcmd <this> build stl`, `freecad <this> view`. FreeCAD's bundled python
does not know about the installed fcad package, so we put the package's parent
directory on sys.path first, then dispatch the command. the cli has already chosen
the right binary (headless vs gui) and exported the FCAD_* / DIFF_* env vars.
"""

import os
import sys

# commands handled in a gui session; everything else is a headless build target.
GUI = {"view", "view-parts", "pdf", "diff-doc", "view-diff"}


def _bootstrap():
    # .../fcad/freecad/_entry.py -> the directory that holds the fcad package
    pkg_parent = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if pkg_parent not in sys.path:
        sys.path.insert(0, pkg_parent)


def main(argv):
    _bootstrap()
    cmd = argv[0] if argv else "all"

    if cmd == "view":
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
    else:
        from fcad.freecad import dispatch
        from fcad.loader import load_project
        if cmd not in dispatch.TARGETS and cmd not in ("check", "precommit"):
            sys.stderr.write("fcad: unknown command %r\n" % cmd)
            raise SystemExit(2)
        project = load_project()
        if cmd == "check":
            dispatch.check(project)
        elif cmd == "precommit":
            dispatch.precommit(project)
        else:
            dispatch.run(project, cmd)


# freecadcmd sets __name__ to the file stem (not "__main__"), so detect being run
# as a script by finding this file on argv and take whatever follows as the command.
if __name__ == "__main__" or any(a.endswith("_entry.py") for a in sys.argv):
    _i = next((i for i, a in enumerate(sys.argv) if a.endswith("_entry.py")), 0)
    main(sys.argv[_i + 1:])
