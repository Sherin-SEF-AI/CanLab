# CanLab Plugin SDK

CanLab loads plugins from `~/.canlab/plugins/*.py`. Plugins let you add menu
actions, custom exporters, or new detectors without touching CanLab's source.

## Security model

Plugin **metadata** (name/version) is read statically without executing code, so
merely listing plugins never runs anything. A plugin's code executes only when it
is **activated** (loaded at startup / enabled in the Plugins panel). Only install
plugins you trust — an activated plugin runs with full app privileges.

## Writing a plugin

A plugin is a single `.py` file that defines:

```python
PLUGIN_NAME    = "My Plugin"     # shown in the Plugins panel
PLUGIN_VERSION = "1.0"

def register(app):
    """Called with the MainWindow instance when the plugin is activated."""
    ...
```

### What `app` gives you

- `app._state` — the shared `AppState`:
  - `app._state.frames_df` — the loaded capture (pandas DataFrame; columns
    `Timestamp, ID, Bus, DLC, B0..B7, Delta`; `ID` is canonical hex, e.g. `"0A6"`).
  - `app._state.dbc_signals` — list of signal-definition dicts.
  - `app._state.get_frames_for_id(id)` — frames for one ID.
  - signals: `frames_updated`, `dbc_updated`, `signal_analyzed`, … (connect to react to new data).
- `app.menuBar()` — add your own menus/actions.
- Anything in `core/` — reuse the detectors (`core.counter_checksum_detector`,
  `core.correlation_engine`, `core.reference_calibrate`, `core.opendbc_matcher`, …).

### Reusable helpers

- CAN ID normalization: `from core.canid import normalize_id`.
- Transmit safety: if your plugin sends frames, honor the ARM gate —
  `from core.safety import require_armed; require_armed()` (raises when disarmed).

## Examples

See `canlab/examples/plugins/`:

- `hello_plugin.py` — adds a menu action that reports the loaded frame count.
- `id_summary_exporter.py` — a custom exporter (per-ID summary CSV) — a template
  for your own export formats.

Copy either into `~/.canlab/plugins/` and restart CanLab (or reopen the Plugins
panel) to load it.
