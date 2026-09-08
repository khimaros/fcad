"""run the in-FreeCAD entry script under the chosen freecad binary.

shared by the cli (build/validate/inspect) and the diff orchestrator. the cli
resolves a Config, exports it into the child's environment, and runs
`<binary> <freecad/_entry.py> <command> ...`; _entry puts the package on
sys.path inside FreeCAD and dispatches the command.

the child is spawned into its own session under an inherited memory ceiling, so
the mesher and solver it goes on to spawn are bounded and can be reaped with it.
"""

import os
import signal
import subprocess

from fcad import limits

# signals that mean "stop": relayed to the child's group, which the new session
# is what makes possible.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


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


def supervise(proc):
    """wait for a popen child, taking its whole group down with us.

    the session that makes the group reapable is also what costs us the
    terminal's ctrl-c, since the child is no longer in the foreground process
    group, so we relay the stop signals ourselves. the finally clause covers
    every other way this process can end - an exception, a signal we did not
    handle - because the failure worth preventing is a solve left running after
    the command that started it is gone."""
    prior = [(s, signal.signal(s, lambda n, _f: _killpg(proc.pid, n)))
             for s in STOP_SIGNALS]
    try:
        return proc.wait()
    finally:
        for sig, handler in prior:
            signal.signal(sig, handler)
        if proc.poll() is None:
            _killpg(proc.pid, signal.SIGKILL)


def run_entry(cfg, args, gui=False, env_extra=None):
    binary = cfg.freecad_gui if gui else cfg.freecad
    env = {**os.environ, **cfg.env()}
    if env_extra:
        env.update(env_extra)
    return supervise(popen([binary, entry_path(), *args], env=env))
