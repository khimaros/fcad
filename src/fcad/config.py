"""resolve fcad's runtime configuration from cli flags + the environment.

pure stdlib so it imports under every interpreter (the cli's python, FreeCAD's
bundled python, and at install time). the cli resolves a `Config` once and exports
it into the environment via `Config.env()`; the in-FreeCAD modules read it straight
back with `from_env()`, so the project/name/dist source of truth is shared without
passing arguments across the process boundary.

a project is named by a path that is either a directory holding a `project.py`,
or a single `.fcad` file (python by another name: a whole project in one file).
either way the project *root* (where `dist/` is created) is the directory; for a
`.fcad` file `entry` carries the file to load and the root is its parent.
"""

import os
from dataclasses import dataclass

ENV_PROJECT = "FCAD_PROJECT"
ENV_ENTRY = "FCAD_ENTRY"
ENV_NAME = "FCAD_NAME"
ENV_DIST = "FCAD_DIST"
ENV_FREECAD = "FREECAD"
ENV_FREECAD_GUI = "FREECAD_GUI"

DEFAULT_FREECAD = "freecadcmd"
DEFAULT_FREECAD_GUI = "freecad"

# the per-instance placements every assembly build records for the 3d diff. it
# lives here because both sides need it: the builder writes it inside freecadcmd,
# the diff orchestrator reads it under the cli's own python.
PLACEMENTS_SUFFIX = "-placements.json"


def placements_path(dist, name):
    return os.path.join(dist, name + PLACEMENTS_SUFFIX)


@dataclass
class Config:
    project: str      # project root dir (holds dist/)
    entry: str        # the .fcad file to load, or "" for a project.py in `project`
    name: str         # output stem: <name>.FCStd, <name>-bom.csv
    dist: str         # output dir
    freecad: str      # headless binary
    freecad_gui: str  # gui binary

    def env(self):
        """the env vars the in-FreeCAD modules read back via from_env()."""
        return {ENV_PROJECT: self.project, ENV_ENTRY: self.entry, ENV_NAME: self.name,
                ENV_DIST: self.dist, ENV_FREECAD: self.freecad,
                ENV_FREECAD_GUI: self.freecad_gui}


def _discover_entry(path):
    """the .fcad file that names a single-file project, or "".

    `path` itself when it is a file; otherwise the sole `*.fcad` in the directory
    (but a `project.py` always wins, keeping the directory contract unchanged)."""
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        if os.path.exists(os.path.join(path, "project.py")):
            return ""
        hits = sorted(f for f in os.listdir(path) if f.endswith(".fcad"))
        if len(hits) == 1:
            return os.path.join(path, hits[0])
    return ""


def resolve(project=None, name=None, dist=None, freecad=None, freecad_gui=None):
    """flag (if given) -> env var -> default, for each field.

    the project path may be a directory or a `.fcad` file; a `--project` flag
    re-discovers the entry, otherwise an entry already exported by the cli wins."""
    raw = os.path.abspath(project or os.environ.get(ENV_PROJECT) or os.getcwd())
    entry = "" if project else os.environ.get(ENV_ENTRY, "")
    entry = entry or _discover_entry(raw)
    root = os.path.dirname(entry) if entry else raw
    stem = (os.path.splitext(os.path.basename(entry))[0] if entry
            else os.path.basename(root))
    name = name or os.environ.get(ENV_NAME) or stem
    dist = os.path.abspath(dist or os.environ.get(ENV_DIST)
                           or os.path.join(root, "dist"))
    freecad = freecad or os.environ.get(ENV_FREECAD) or DEFAULT_FREECAD
    freecad_gui = freecad_gui or os.environ.get(ENV_FREECAD_GUI) or DEFAULT_FREECAD_GUI
    return Config(root, entry, name, dist, freecad, freecad_gui)


def from_env():
    """resolve purely from the environment, as used inside freecadcmd/freecad."""
    return resolve()
