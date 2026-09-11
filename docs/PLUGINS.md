# CanLab Plugin SDK

CanLab loads plugins from `~/.canlab/plugins/*.py`. Plugins let you add menu
actions, custom exporters or new detectors without touching CanLab's source.

## Security model

Plugin **metadata** (name/version) is read statically with `ast`, so listing
plugins never runs anything. A plugin's code executes only once you **enable it**
in Settings → PLUGINS; dropping a file into the directory does nothing on its
own, and the choice is remembered between sessions. An enabled plugin runs with
full application privileges — only enable plugins you trust.

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
  - `app._state.frames_df` — the loaded capture as a pandas DataFrame with
    columns `Timestamp, ID, Bus, DLC, Extended, B0..B7, Delta` (`ID` is
    canonical hex, e.g. `"0A6"`). It is materialised from the frame store on
    demand, so hold the result rather than re-reading it in a loop.
  - `app._state.frames_snapshot()` — an independent copy, safe to hand to a
    worker thread while capture continues.
  - `app._state.store` — the `FrameStore` itself, for cheap lookups:
    `store.frames_for_id(id, tail=500)`, `store.last_frame(id)`,
    `store.id_stats()`, `store.tail(n)`.
  - `app._state.dbc_signals` — the list of signal-definition dicts.
  - `app._state.bus_hub` — the receive dispatcher while a bus is connected.
- `app.menuBar()` — add your own menus and actions.
- Anything in `canlab.core` — reuse the detectors (`counter_checksum_detector`,
  `correlation_engine`, `checksums`, `reference_calibrate`, `opendbc_matcher`, …).

### Events you can subscribe to

`AppState` signals are the plugin event bus. Connect to whichever you need:

| Signal | Fires when |
|---|---|
| `frames_loaded(int)` | a capture finished loading |
| `frames_updated()` | frames changed (throttled during live capture) |
| `id_selected(str)` | the user selected a CAN ID |
| `dbc_updated()` | the signal list changed |
| `dbc_db_updated()` | the cantools database was rebuilt |
| `can_connected(bool)` | a bus was connected or disconnected |
| `tx_armed_changed(bool)` | ARM TX was toggled |
| `trigger_fired(dict, object)` | a trigger rule matched a live frame |
| `bus_load_update(float)` | a new bus-load sample (0.0–1.0) |
| `bus_health_update(dict)` | a bus-health snapshot |
| `replay_tick(int, int)` | replay progress (current, total) |
| `change_detected(list)` | change-on-action produced deltas |
| `anomaly_detected(str, float)` | an anomalous ID was scored |
| `safety_cutout(float, str)` | an actuator sweep aborted |
| `note_updated(str)` | a signal note was edited |
| `project_loaded()` | a `.canlab` project finished loading |
| `pid_value_updated(int, float, str)` | a live OBD-II PID value |

### Reading and writing the bus

- Subscribe to live frames without competing with anything else:
  `sub = app._state.bus_view(self)` then `sub.recv(timeout=0.1)`. Pass an ID
  filter to narrow it: `app._state.bus_view(self, {0x7E8})`.
- **Transmitting honours the ARM TX gate.** Send through the hub or a
  subscription (`sub.send(msg)`), or call
  `from canlab.core.safety import gated_send; gated_send(bus, msg)`. It raises
  `BusNotArmedError` when transmit is disarmed and `BlockedIdError` for a
  blocked ID. Never call `bus.send` directly — that bypasses the one safety
  guarantee the tool makes.

### Reusable helpers

- CAN ID normalisation: `from canlab.core.canid import normalize_id`.
- Decode/encode: `from canlab.core.dbc_manager import decode_frame, encode_frame`.
- Background work: `from canlab.ui.compute_worker import ComputeWorker` runs a
  callable off the GUI thread and delivers `done`/`failed`.

## Examples

See `canlab/examples/plugins/`:

- `hello_plugin.py` — adds a menu action that reports the loaded frame count.
- `id_summary_exporter.py` — a custom exporter (per-ID summary CSV), a template
  for your own export formats.

Copy either into `~/.canlab/plugins/`, then enable it in Settings → PLUGINS.
