"""end-to-end test for `fcad help` (fcad.cli).

`help` must behave like a real command: bare `fcad help` (and no args) prints the
top-level usage, and `fcad help COMMAND` prints that command's usage; an unknown
command is an error. the cli is pure python (no FreeCAD), but the suite drives
every test through freecadcmd, so results go to $RESULT_FILE as usual.
"""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fcad import cli


def _run(argv):
    """invoke the cli, capturing (rc, stdout+stderr); argparse exits via SystemExit."""
    buf = io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = cli.main(argv)
        except SystemExit as e:
            rc = e.code or 0
    return rc, buf.getvalue()


def main():
    checks = []

    def check(name, ok):
        checks.append((name, ok))

    rc, out = _run(["help"])
    check("help: exits 0", rc == 0)
    check("help: prints usage", "usage: fcad" in out)
    check("help: lists commands", "build" in out and "install-macro" in out)

    # bare invocation and `-h` must produce the same top-level usage as `help`.
    check("no args matches help", _run([])[1] == out)
    check("-h matches help", _run(["-h"])[1] == out)

    rc, out = _run(["help", "build"])
    check("help build: exits 0", rc == 0)
    check("help build: scopes to build", "fcad build" in out and "TARGET" in out)

    rc, out = _run(["help", "bogus"])
    check("help bogus: errors", rc != 0)
    check("help bogus: names the bad command", "bogus" in out)

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


if any(a.endswith("test_help.py") for a in sys.argv):
    raise SystemExit(main())
