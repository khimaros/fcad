"""import a project's module and hand back its `Project`.

the fcad package is already importable when this runs (the cli or
freecad/_entry put it on sys.path); we only have to add the project directory so
`import project` resolves, then read its descriptor.

a project declares itself in one of two ways. the explicit way exposes a ready
`PROJECT = fcad.Project(...)`. the minimal way exposes plain module globals
(`PARAMS` + `compute`, and optional refinements) and lets fcad assemble the
`Project`, so a whole project can be a single file. that file is either a
directory's `project.py` (imported by name) or a standalone `.fcad` file (python
loaded from its path).
"""

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

from fcad.project import Project
from fcad import config

# module globals the minimal (convention) path reads, mapped to Project kwargs.
# PARAMS + compute are required; everything else refines the inferred defaults.
_GLOBALS = {
    "PARAMS": "params", "SCHEMA": "schema", "COMPUTE": "compute",
    "FROM_SPEC": "from_spec", "PROFILE": "profile", "ENUM_CHOICES": "enum_choices",
    "FEM": "fem", "PARAM_META": "param_meta", "MATERIAL": "material",
    "MATERIALS": "materials", "STOCK": "stock", "CONSTRAINTS": "constraints",
    "ASSEMBLE": "assemble",
}


def _from_globals(mod):
    """assemble a Project from a project module's conventional globals."""
    kwargs = {}
    for upper, kw in _GLOBALS.items():
        # accept either the UPPER_CASE constant or a lowercase callable/value.
        val = getattr(mod, upper, None)
        if val is None:
            val = getattr(mod, kw, None)
        if val is not None:
            kwargs[kw] = val
    if "params" not in kwargs or "compute" not in kwargs:
        raise AttributeError(
            "project module exposes neither PROJECT nor PARAMS+compute")
    cfg = config.from_env()
    # dist is passed, not recomputed: the cli already resolved --dist/FCAD_DIST
    # and a `Project` deriving <root>/dist itself would build into the wrong
    # directory. an explicit `PROJECT = fcad.Project(...)` never comes through
    # here, which is why `Project.dist` reads the environment as well.
    return Project(name=getattr(mod, "NAME", cfg.name), root=cfg.project,
                   dist=cfg.dist, **kwargs)


def load_path(path, name="fcad_project"):
    """exec a standalone `.fcad` file (python) and return it as a module.

    its directory goes on sys.path first so a single-file project can still
    import sibling helpers if it wants to. no bytecode is cached: a project is
    one file its owner can see all of, and a `__pycache__/` appearing beside it
    after every command is clutter to explain and to ignore."""
    d = os.path.dirname(os.path.abspath(path))
    if d and d not in sys.path:
        sys.path.insert(0, d)
    # .fcad is not a registered source suffix, so name the loader explicitly.
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
    return mod


def load_project():
    cfg = config.from_env()
    if cfg.entry:
        mod = load_path(cfg.entry)
    else:
        if cfg.project and cfg.project not in sys.path:
            sys.path.insert(0, cfg.project)
        import project as mod
    return getattr(mod, "PROJECT", None) or _from_globals(mod)
