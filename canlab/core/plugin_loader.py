"""Discover and load plugins from ~/.canlab/plugins/*.py.

Each plugin module must define:
    def register(app) -> None   # called with the MainWindow instance
    PLUGIN_NAME  = "My Plugin"  # display name
    PLUGIN_VERSION = "1.0"

Security note: :func:`discover_plugins` reads name/version *statically* (via the
``ast`` module) and never imports/executes plugin code. Execution happens only
in :func:`load_plugin`, invoked from :func:`activate_plugins`. This means merely
listing plugins (e.g. opening the Settings panel) cannot run arbitrary code —
only explicit activation does.
"""
import ast
import importlib.util
from pathlib import Path


PLUGIN_DIR = Path.home() / ".canlab" / "plugins"


def _read_metadata(py_file: Path) -> tuple[str, str]:
    """Extract PLUGIN_NAME / PLUGIN_VERSION without executing the module."""
    name, version = py_file.stem, "?"
    try:
        tree = ast.parse(py_file.read_text(errors="replace"))
    except Exception:
        return name, version
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                if target.id == "PLUGIN_NAME":
                    name = str(node.value.value)
                elif target.id == "PLUGIN_VERSION":
                    version = str(node.value.value)
    return name, version


def discover_plugins() -> list[dict]:
    """Return list of {name, version, path, module, enabled} dicts.

    Does NOT execute any plugin code; ``module`` is always None here and is
    populated lazily by :func:`activate_plugins`.
    """
    results = []
    if not PLUGIN_DIR.exists():
        return results
    for py_file in sorted(PLUGIN_DIR.glob("*.py")):
        name, version = _read_metadata(py_file)
        results.append({
            "name":    name,
            "version": version,
            "path":    str(py_file),
            "module":  None,
            "enabled": True,
        })
    return results


def load_plugin(path: str):
    """Import and execute a single plugin module. Executes plugin code."""
    py_file = Path(path)
    spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def activate_plugins(plugins: list[dict], app) -> list[str]:
    """Load (if needed) and call register(app) on each enabled plugin."""
    activated = []
    for p in plugins:
        if not p.get("enabled"):
            continue
        if p.get("module") is None:
            try:
                p["module"] = load_plugin(p["path"])
            except Exception as e:
                p["enabled"] = False
                p["version"] = "error"
                p["error"]   = str(e)
                continue
        mod = p["module"]
        if hasattr(mod, "register"):
            try:
                mod.register(app)
                activated.append(p["name"])
            except Exception as e:
                p["enabled"] = False
                p["error"]   = str(e)
    return activated
