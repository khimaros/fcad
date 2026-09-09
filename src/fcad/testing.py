"""helpers for a project's own end-to-end tests.

every fcad project ends up writing the same two things, because FreeCAD forces
both.

**the verdict has to go to a file.** `freecadcmd` swallows whatever python still
holds buffered on stdout when a script exits non-zero, and stdout is only
block-buffered when it is *not* a tty -- so a test that passes on a terminal
reports nothing at all when redirected or run in CI. worse, freecadcmd exits 0
on an uncaught exception, so a runner that trusts the exit status treats a crash
as a pass. `Checks.report()` writes to `$RESULT_FILE` and `fcad test` greps it,
which is the only channel that survives both.

**the project has to be loaded by path.** a `.fcad` file is python under another
name and is not importable, so each project hand-rolled the same
`SourceFileLoader` dance.

everything else here is the geometry vocabulary those tests turn out to need:
placed solids, their blanks, extents, and the volume two of them share.
"""

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

from fcad.project import normalize

# a face-to-face contact leaves an OCC sliver rather than exactly zero, so a
# shared volume under this is two parts touching, not interfering.
TOUCH_VOL = 1.0


def load(path, name="fcad_project_under_test"):
    """import a project module from a path, `.fcad` or `.py` alike."""
    d = os.path.dirname(os.path.abspath(path))
    if d and d not in sys.path:
        sys.path.insert(0, d)
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def find(path=None, stem=None):
    """the project file beside the tests: `<stem>.fcad`, or the only one there.

    called from `tests/test_*.py` with no arguments it looks one directory up,
    which is where a single-file project lives."""
    root = os.path.abspath(path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[-1]))), "."))
    if stem:
        return os.path.join(root, stem + ".fcad")
    entries = sorted(f for f in os.listdir(root) if f.endswith(".fcad"))
    if len(entries) != 1:
        raise IOError("expected one .fcad in %s, found %d" % (root, len(entries)))
    return os.path.join(root, entries[0])


def specs(mod, values=None):
    """{name: spec} for the project's computed parts.

    a project's `compute` may hand back the bare list or the dict form, so this
    normalizes exactly as the loader does rather than making a test know which
    one its own project wrote."""
    return {s.name: s
            for s in normalize(mod.compute(dict(values or mod.PARAMS)))["specs"]}


def shape(mod, spec):
    """a spec's unplaced solid, however the project chose to declare it.

    the project's own `from_spec` if it has one (a project realizing its own
    duck-typed spec), else the spec's `solid()` thunk -- the same fallback
    `fcad.Project` makes, so a minimal project built out of `fcad.PartSpec` needs no
    `from_spec` here either."""
    build = getattr(mod, "from_spec", None)
    return build(spec) if build else spec.solid()


def _placed(base, spec):
    out = []
    for pl in spec.placements:
        copy = base.copy()
        copy.Placement = pl
        out.append(copy)
    return out


def solids(mod, spec):
    """every placed solid for a spec. the shape is built once and reused."""
    return _placed(shape(mod, spec), spec)


def blanks(mod, spec):
    """the same placed solids before their joinery is cut.

    `BoundBox` is only an outer estimate where a shape has curved faces, and a
    counterbore inflates it by a millimetre or two, so anything measuring where
    a face actually *sits* wants the blank."""
    build = getattr(mod, "blank", None)
    return _placed(build(spec) if build else shape(mod, spec), spec)


def extent(mod, spec, index=None):
    """the bounding box of one placed blank, or of all of them united."""
    box = None
    for i, s in enumerate(blanks(mod, spec)):
        if index is not None and i != index:
            continue
        bb = s.BoundBox
        box = bb if box is None else box.united(bb)
    return box


def overlap(a, b):
    """the volume two placed solids share, 0.0 if they cannot meet."""
    if not a.BoundBox.intersect(b.BoundBox):
        return 0.0
    try:
        return a.common(b).Volume
    except Exception:
        return 0.0


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


class Checks:
    """collect named assertions and report them where freecadcmd cannot lose them.

    usage mirrors what every project wrote by hand::

        c = Checks()
        c("the bed is the depth asked for", near(got, want))
        raise SystemExit(c.report())
    """

    def __init__(self):
        self.results = []

    def __call__(self, name, ok):
        self.results.append((name, bool(ok)))
        return bool(ok)

    check = __call__          # for callers that prefer the verb

    @property
    def failed(self):
        return [name for name, ok in self.results if not ok]

    def report(self, path=None):
        """write the verdict to $RESULT_FILE and return a process exit code."""
        lines = ["%s %s" % ("ok  " if ok else "FAIL", name)
                 for name, ok in self.results]
        lines.append("RESULT %s" % ("PASS" if not self.failed else "FAIL"))
        text = "\n".join(lines) + "\n"
        target = path or os.environ.get("RESULT_FILE")
        if target:
            with open(target, "w") as f:
                f.write(text)
        print(text)
        return 0 if not self.failed else 1


def main(fn, script):
    """run a test's `main`, routing a crash into the report.

    freecadcmd prints only an exception's message and never its traceback, so an
    uncaught error otherwise vanishes and the run still exits 0. the argv guard
    is here because freecadcmd sets `__name__` to the file stem rather than
    `"__main__"`, so the usual idiom does not fire."""
    if not any(a.endswith(os.path.basename(script)) for a in sys.argv):
        return
    try:
        raise SystemExit(fn())
    except SystemExit:
        raise
    except Exception:
        import traceback
        c = Checks()
        c(traceback.format_exc(), False)
        raise SystemExit(c.report())
