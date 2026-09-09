"""run one of a project's tests inside FreeCAD, with fcad importable.

`fcad test` cannot hand a test file straight to freecadcmd: FreeCAD's embedded
interpreter ignores PYTHONPATH, so the `from fcad import testing` that every
project test opens with fails, and each project ends up hand-rolling a sys.path
preamble to find the fcad checkout. routing through `_entry` instead reuses the
one bootstrap that already knows where fcad is.

runpy runs the file as the script it is, so `__file__`, `__name__` and `sys.argv`
all read the way a test expects - `fcad.testing`'s `find()` and `main()` both
work off argv, since freecadcmd never sets `__name__` to `"__main__"`.
"""

import os
import runpy
import sys


def main(path):
    d = os.path.dirname(os.path.abspath(path))
    if d not in sys.path:
        sys.path.insert(0, d)
    runpy.run_path(path, run_name=os.path.splitext(os.path.basename(path))[0])
