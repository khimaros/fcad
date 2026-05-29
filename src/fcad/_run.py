"""run the in-FreeCAD entry script under the chosen freecad binary.

shared by the cli (build/validate/inspect) and the diff orchestrator. the cli
resolves a Config, exports it into the child's environment, and runs
`<binary> <freecad/_entry.py> <command> ...`; _entry puts the package on
sys.path inside FreeCAD and dispatches the command.
"""

import os
import subprocess


def entry_path():
    from fcad.freecad import _entry
    return os.path.abspath(_entry.__file__)


def run_entry(cfg, args, gui=False, env_extra=None):
    binary = cfg.freecad_gui if gui else cfg.freecad
    env = {**os.environ, **cfg.env()}
    if env_extra:
        env.update(env_extra)
    return subprocess.run([binary, entry_path(), *args], env=env).returncode
