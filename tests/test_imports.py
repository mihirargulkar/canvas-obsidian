"""Guard against unresolvable imports, including ones hidden inside functions.

Several modules import lazily (`from . import ingest` inside a method) to keep
startup cheap and avoid cycles. Those lines never execute at import time, so a
broken one survives every other test and only fails when a user hits that code
path. Two real bugs shipped this way during a package refactor, so we walk the
AST and check every import target instead.
"""
import ast
import importlib
import pkgutil
from pathlib import Path

import canvas_vault

PKG = Path(canvas_vault.__file__).parent
STDLIB_OK = True          # unresolvable stdlib/3rd-party would fail loudly elsewhere


def test_every_module_imports():
    for m in pkgutil.iter_modules([str(PKG)]):
        importlib.import_module(f"canvas_vault.{m.name}")


def test_no_bare_intra_package_imports():
    """Inside the package, sibling modules must be imported relatively.

    `import sync` works when the modules sit in the repo root but raises
    ModuleNotFoundError once they live in a package — including in the
    comma-separated form `import io, contextlib, sync`, which is easy to miss.
    """
    siblings = {m.name for m in pkgutil.iter_modules([str(PKG)])}
    offenders = []
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in siblings:
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                # level 0 == absolute; a sibling name there is the bug
                if node.level == 0 and node.module and node.module.split(".")[0] in siblings:
                    offenders.append(f"{path.name}:{node.lineno} from {node.module} import ...")
    assert not offenders, "bare intra-package imports:\n  " + "\n  ".join(offenders)


def test_relative_imports_resolve():
    """Every `from . import X` / `from .X import Y` target actually exists."""
    missing = []
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            mod = f"canvas_vault.{node.module}" if node.module else "canvas_vault"
            try:
                imported = importlib.import_module(mod)
            except Exception as e:
                missing.append(f"{path.name}:{node.lineno} {mod} ({e})")
                continue
            if node.module is None:                 # from . import X
                for alias in node.names:
                    if not hasattr(imported, alias.name):
                        try:
                            importlib.import_module(f"canvas_vault.{alias.name}")
                        except Exception:
                            missing.append(f"{path.name}:{node.lineno} . has no {alias.name}")
    assert not missing, "unresolvable relative imports:\n  " + "\n  ".join(missing)
