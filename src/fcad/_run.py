"""run the in-FreeCAD entry script under the chosen freecad binary.

shared by the cli (build/validate/inspect) and the diff orchestrator. the cli
resolves a Config, exports it into the child's environment, and runs
`<binary> <freecad/_entry.py> <command> ...`; _entry puts the package on
sys.path inside FreeCAD and dispatches the command.

the child is spawned into its own session under an inherited memory ceiling, so
the mesher and solver it goes on to spawn are bounded and can be reaped with it.
"""

import io
import os
import re
import signal
import subprocess
import sys
import threading

from fcad import limits

# signals that mean "stop": relayed to the child's group, which the new session
# is what makes possible.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

# what the kernel prints that no one asked for: OpenCASCADE's STEP writer
# banners, FreeCAD's save/load progress bars and the assembly solver's trace.
# a two-part build writes 73 lines of which 12 are fcad's, and on a real model
# the verdicts of `check` are lost in several hundred. set FCAD_VERBOSE to see
# all of it.
VERBOSE_ENV = "FCAD_VERBOSE"
DRAIN_SECONDS = 5.0          # how long to wait on stderr once the child is gone
# the line the entry script prints before anything else, when asked to through
# BEGIN_ENV. everything FreeCAD wrote ahead of it is startup - its banner, and
# whatever each installed addon's Init chose to say, which no pattern can list.
BEGIN_ENV = "FCAD_BEGIN"
BEGIN = "fcad: entry"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# the words FreeCAD's save/load progress leaves behind, with no newline after
# them: whatever is printed next is on the same line, so the word is cut out
# rather than its line dropped.
PROGRESS = re.compile(
    r"(saving|Importing project files|Postprocessing)\.{3,}")
NOISE = re.compile(
    r"^\s*(\*\*|\(\d+ %\)|Step File Name|MbD: |Time = \d)"
    # the three lines FreeCAD opens every run with: its version, its copyright
    # and its licence.
    r"|^FreeCAD \d[\d.]*, Libs: |^\(C\) \d{4}-\d{4} FreeCAD contributors$"
    r"|^FreeCAD is free and open-source software"
    # and on stderr, three warnings every build prints and none can act on: the
    # element-map hasher, the assembly's joint cycle (see CONTRIBUTING) and the
    # Hole feature looking for its thread tables.
    r"|hasher mismatch$|The graph must be a DAG\.$"
    r"|^Looking for thread definitions in")


def entry_path():
    from fcad.freecad import _entry
    return os.path.abspath(_entry.__file__)


def popen(argv, env=None, **kwargs):
    """spawn a child that is bounded, and reapable as a whole.

    its own session, because the processes that get big - gmsh and CalculiX - are
    grandchildren FreeCAD spawns, and a plain kill of freecadcmd leaves a mesher
    behind still holding gigabytes. one process group means one signal reaps all
    three. and a heap ceiling, which rlimits make inherited, so the tools hit a
    wall of their own rather than the OOM killer's."""
    return subprocess.Popen(argv, env=env, start_new_session=True,
                            preexec_fn=limits.limit_child, **kwargs)


def _killpg(pid, sig):
    try:
        os.killpg(pid, sig)
    except OSError:
        pass


def supervise(proc, pump=None):
    """wait for a popen child, taking its whole group down with us. `pump`, when
    given, drains the child's output first and so runs under the same guard.

    the session that makes the group reapable is also what costs us the
    terminal's ctrl-c, since the child is no longer in the foreground process
    group, so we relay the stop signals ourselves. the finally clause covers
    every other way this process can end - an exception, a signal we did not
    handle - because the failure worth preventing is a solve left running after
    the command that started it is gone."""
    prior = [(s, signal.signal(s, lambda n, _f: _killpg(proc.pid, n)))
             for s in STOP_SIGNALS]
    try:
        if pump is not None:
            pump()
        return proc.wait()
    finally:
        for sig, handler in prior:
            signal.signal(sig, handler)
        if proc.poll() is None:
            _killpg(proc.pid, signal.SIGKILL)


def signal_of(text):
    """`text` with the kernel's chatter removed; "" when nothing is left.

    progress bars redraw with carriage returns, so one line can hold a dozen of
    them and then a verdict: each redraw is judged on its own and the survivors
    kept, rather than the whole line dropped for how it started."""
    kept = [part.lstrip("\t") for part in
            PROGRESS.sub("", ANSI.sub("", text)).rstrip("\n").split("\r")
            if part.strip() and not NOISE.search(part)]
    return "".join(kept)


def relay(stream, out=None, begin=None):
    """copy a child's stdout through, dropping the chatter and blank lines it
    leaves behind. flushed per line so a long build can still be watched.

    with `begin`, nothing is passed on until a line saying exactly that arrives,
    and what came before it is dropped. if it never arrives FreeCAD did not get
    as far as fcad, and what it said instead is written out after all."""
    out = out or sys.stdout
    begun, held = False, [] if begin else None
    # newline="\n" so a carriage return stays inside its line: translated, each
    # redraw of a progress bar would arrive as a line of its own.
    for line in io.TextIOWrapper(stream, newline="\n", errors="replace"):
        if held is not None:
            if line.rstrip("\n") == begin:
                held = None
            else:
                held.append(line)
            continue
        text = signal_of(line)
        # a blank line is kept only once something has been said: the one
        # FreeCAD prints under its banner would otherwise open every command.
        if text or (line == "\n" and begun):
            begun = True
            out.write(text + "\n")
            out.flush()
    out.write("".join(held or ()))
    out.flush()


def run_entry(cfg, args, gui=False, env_extra=None, wrap=()):
    """run one command inside FreeCAD. `wrap` is a command prefix, e.g. the
    virtual display an offscreen gui render runs under."""
    binary = cfg.freecad_gui if gui else cfg.freecad
    env = {**os.environ, **cfg.env()}
    if env_extra:
        env.update(env_extra)
    argv = [*wrap, binary, entry_path(), *args]
    if gui or os.environ.get(VERBOSE_ENV):
        return supervise(popen(argv, env=env))
    env[BEGIN_ENV] = BEGIN
    proc = popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # stderr is drained on a thread so neither pipe can fill and stall the
    # child while the other is being read; the streams stay separate.
    errors = threading.Thread(target=relay, args=(proc.stderr, sys.stderr),
                              daemon=True)
    errors.start()
    try:
        return supervise(proc, lambda: relay(proc.stdout, begin=BEGIN))
    finally:
        errors.join(DRAIN_SECONDS)
