"""Discover and load plugins from ~/.canlab/plugins/*.py.

Each plugin module must define:
    def register(app) -> None   # called with the MainWindow instance
    PLUGIN_NAME  = "My Plugin"  # display name
    PLUGIN_VERSION = "1.0"
"""
import importlib.util
import sys
from pathlib import Path


PLUGIN_DIR = Path.home() / ".canlab" / "plugins"


def discover_plugins() -> list[dict]:
    """Return list of {name, version, path, enabled} dicts."""
    results = []
    if not PLUGIN_DIR.exists():
        return results
    for py_file in sorted(PLUGIN_DIR.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            results.append({
                "name":    getattr(mod, "PLUGIN_NAME",    py_file.stem),
                "version": getattr(mod, "PLUGIN_VERSION", "?"),
                "path":    str(py_file),
                "module":  mod,
                "enabled": True,
            })
        except Exception as e:
            results.append({
                "name":    py_file.stem,
                "version": "error",
                "path":    str(py_file),
                "module":  None,
                "enabled": False,
                "error":   str(e),
            })
    return results


def activate_plugins(plugins: list[dict], app) -> list[str]:
    """Call register(app) on each enabled plugin; return list of activated names."""
    activated = []
    for p in plugins:
        if not p.get("enabled") or p.get("module") is None:
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
