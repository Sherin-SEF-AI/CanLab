"""Discover and load plugins from ~/.canlab/plugins/*.py.

Each plugin module must define:
    def register(app) -> None   # called with the MainWindow instance
    PLUGIN_NAME  = "My Plugin"  # display name
    PLUGIN_VERSION = "1.0"

Security note: :func:`discover_plugins` reads name/version *statically* (via the
``ast`` module) and never imports/executes plugin code. Execution happens only
in :func:`load_plugin`, invoked from :func:`activate_plugins`, and only for
plugins the user has enabled in Settings. A file sitting in the plugin directory
does nothing on its own, and an approval is pinned to the file's SHA-256, so an
edited plugin is disabled again until it is re-approved.
"""
import ast
import hashlib
import importlib.util
import logging
from pathlib import Path

log = logging.getLogger(__name__)


PLUGIN_DIR = Path.home() / ".canlab" / "plugins"
ENABLED_KEY = "plugins/enabled"


def plugin_fingerprint(path: str) -> str:
    """SHA-256 of the plugin file.

    Enabling a plugin approves *that content*, not the filename. If the file is
    edited afterwards the fingerprint no longer matches and the plugin goes back
    to disabled, so approved code cannot be swapped for something else without
    asking again.
    """
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        log.debug("cannot fingerprint %s", path, exc_info=True)
        return ""


def _approvals() -> dict[str, str]:
    """Approved plugins as {path: fingerprint}.

    Stored one per line as ``path\x1ffingerprint``. Entries written before
    fingerprints existed have no separator and are treated as unapproved, so
    they are re-confirmed once rather than silently trusted.
    """
    try:
        from canlab.settings_dialog import settings
        raw = settings().value(ENABLED_KEY, "", str)
    except Exception:
        log.debug("cannot read the plugin allow-list", exc_info=True)
        return {}
    out: dict[str, str] = {}
    for line in (raw or "").split("\n"):
        if not line:
            continue
        path, sep, digest = line.partition("\x1f")
        if sep:
            out[path] = digest
    return out


def enabled_paths() -> set[str]:
    """Plugins the user enabled whose content still matches what was approved.

    Previously every file in the plugin directory was executed at startup, so
    dropping a file in was enough to run it. Nothing runs now until it is ticked
    in Settings, and an approval only covers the exact bytes that were ticked.
    """
    return {path for path, digest in _approvals().items()
            if digest and plugin_fingerprint(path) == digest}


def set_enabled(path: str, enabled: bool) -> None:
    """Add or remove one plugin from the allow-list, pinning its fingerprint."""
    current = _approvals()
    if enabled:
        current[str(path)] = plugin_fingerprint(path)
    else:
        current.pop(str(path), None)
    from canlab.settings_dialog import settings
    st = settings()
    st.setValue(ENABLED_KEY, "\n".join(
        f"{p}\x1f{d}" for p, d in sorted(current.items())))
    st.sync()


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
    approvals = _approvals()
    for py_file in sorted(PLUGIN_DIR.glob("*.py")):
        name, version = _read_metadata(py_file)
        approved = approvals.get(str(py_file))
        enabled = bool(approved) and plugin_fingerprint(str(py_file)) == approved
        results.append({
            "name":    name,
            "version": version,
            "path":    str(py_file),
            "module":  None,
            "enabled": enabled,
            # Approved once, then the file changed: off until re-approved.
            "changed": bool(approved) and not enabled,
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
