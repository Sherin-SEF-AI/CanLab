from PyQt6.QtCore import QObject, pyqtSignal
import pandas as pd

from canlab.core.frame_store import FrameStore

class AppState(QObject):
    id_selected       = pyqtSignal(str)
    frames_loaded     = pyqtSignal(int)
    signal_analyzed   = pyqtSignal(str)
    dbc_updated       = pyqtSignal()
    can_connected     = pyqtSignal(bool)
    frames_updated    = pyqtSignal()
    source_added      = pyqtSignal(str, int)

    project_loaded      = pyqtSignal()
    trigger_fired       = pyqtSignal(dict, object)   # rule, frame
    replay_tick         = pyqtSignal(int, int)       # current, total
    bus_load_update     = pyqtSignal(float)          # 0.0–1.0

    # Signals below are the event bus plugins can connect to (see docs/PLUGINS.md).
    canfd_toggled        = pyqtSignal(bool)
    change_detected      = pyqtSignal(list)           # list of delta dicts
    safety_cutout        = pyqtSignal(float, str)      # value_at_cutout, reason
    note_updated         = pyqtSignal(str)             # signal_key

    bus_health_update    = pyqtSignal(dict)             # health snapshot
    dbc_db_updated       = pyqtSignal()                 # cantools cache rebuilt
    tx_armed_changed     = pyqtSignal(bool)             # ARM TX toggled

    pid_value_updated    = pyqtSignal(int, float, str)  # pid, value, unit

    anomaly_detected     = pyqtSignal(str, float)        # id, score
    annotations_changed  = pyqtSignal()                  # marks edited outside INTELLIGENCE

    def __init__(self, parent=None):
        super().__init__(parent)
        # The cantools Database cached by core.dbc_manager.get_db is rebuilt
        # lazily after any signal change.
        self.dbc_updated.connect(self._invalidate_dbc_db)
        from canlab.core import safety
        safety.add_observer(self._on_armed_changed)

        self._store = FrameStore()
        self.selected_id:      str          = ""
        self.sources:          list         = []
        self.can_bus           = None      # the BusHub while connected (has .send)
        self.bus_hub           = None      # core.bus_hub.BusHub
        self._bus_views: dict  = {}        # owner id -> (hub, Subscription)
        self.is_connected:     bool         = False
        self.dbc_signals:      list         = []
        self.analyzed_ids:     dict         = {}
        self.live_frame_count: int          = 0
        self.frame_rate:       float        = 0.0

        # Advanced feature state
        self.diff_baseline_df: pd.DataFrame = pd.DataFrame()
        self.periodicities:    dict         = {}   # id -> cycle_time_ms
        self.ai_memory:        list         = []   # list of prior AI conclusions
        self.opendbc_matches:  dict         = {}   # sig_name -> opendbc path
        self.project_path:     str          = ""
        self.plugins:          list         = []
        self.rest_api_running: bool         = False
        self.rest_api_port:    int          = 8765
        self.triggers:         list         = []   # list of trigger dicts
        self.injection_active: dict         = {}   # sig_name -> (value, period_ms)

        # ── New fields for 12-feature additions ───────────────────────────────
        self.canfd_enabled:     bool         = False
        self.notes_by_signal:   dict         = {}   # "{msg_id}/{sig_name}" -> str
        # Marks on the capture timeline ("brake pressed", 12.4s to 14.1s),
        # made live or against a loaded log, and ranked against every byte.
        from canlab.core.annotations import AnnotationSet
        self.annotations = AnnotationSet()
        # The live anomaly watch, when the WATCH sub-tab has started one;
        # read by the MCP backend so an assistant can list its events.
        self.live_watch = None
        self.fuzz_running:      bool         = False
        self.active_backend:    str          = "python-can"
        # Framing conventions for injection/export/AI hints; "generic" asserts
        # nothing about the vehicle.
        self.vehicle_profile:   str          = "generic"
        self.panda_safety_model: str         = "SAFETY_NOOUTPUT"

        # ── New fields for 8 production enhancements ──────────────────────────
        self.dbc_db              = None        # cached cantools.database.Database
        self.bus_health: dict    = {           # live health counters
            "error_frames": 0,
            "bus_off":      0,
            "peak_load":    0.0,
            "avg_load":     0.0,
            "total_frames": 0,
        }
        self.test_sequences: list = []         # list of TestStep dicts

        # OBD-II live gauges
        self.obd_active_pids: list = []        # PID ints selected by user

        # Signal Intelligence
        self._embedding_index: dict = {}       # id -> np.ndarray, built by signal_intelligence_tab

    def select_id(self, hex_id: str):
        self.selected_id = hex_id
        self.id_selected.emit(hex_id)

    # ── frame storage ────────────────────────────────────────────────────
    # frames_df stays the public contract (a canonical DataFrame); it is now
    # materialised from FrameStore on demand and cached until frames change.

    @property
    def frames_df(self) -> pd.DataFrame:
        return self._store.materialize()

    @frames_df.setter
    def frames_df(self, df: pd.DataFrame):
        self._store.load_dataframe(df)

    @property
    def store(self) -> FrameStore:
        return self._store

    def frames_snapshot(self) -> pd.DataFrame:
        """A frame safe to hand to a worker thread while capture continues."""
        return self._store.snapshot()

    def bus_view(self, owner, id_filter=None):
        """A private receive queue on the live bus for ``owner`` (None if offline).

        The returned object duck-types a python-can bus (send/recv), so workers
        take it in place of the raw bus and never compete for frames.
        """
        hub = self.bus_hub
        if hub is None:
            return None
        cached = self._bus_views.get(id(owner))
        if cached is not None and cached[0] is hub and not cached[1].closed:
            return cached[1]
        sub = hub.subscribe(id_filter)
        self._bus_views[id(owner)] = (hub, sub)
        return sub

    def drop_bus_views(self):
        for _hub, sub in self._bus_views.values():
            try:
                sub.close()
            except Exception:
                pass
        self._bus_views.clear()

    def append_rows(self, rows: list):
        """Append canonical live-capture rows (from BusHub.drain())."""
        if not rows:
            return
        self._store.append_batch(rows)
        self.frames_updated.emit()

    def load_frames(self, df: pd.DataFrame, source_name: str):
        self._store.load_dataframe(df)
        count = len(self._store)
        self.sources.append({"name": source_name, "count": count})
        self.frames_loaded.emit(count)
        self.source_added.emit(source_name, count)
        self.frames_updated.emit()

    def append_frames(self, new_df: pd.DataFrame):
        self._store.extend_dataframe(new_df)
        self.frames_updated.emit()

    def _invalidate_dbc_db(self):
        self.dbc_db = None

    def _on_armed_changed(self, armed: bool):
        self.tx_armed_changed.emit(bool(armed))

    # ── DBC signal edits, with history ───────────────────────────────────
    # Every mutation snapshots the list first. Undo restores the snapshot;
    # redo re-applies what undo took back. Snapshots are whole-list copies,
    # which is fine at the sizes a DBC reaches and makes correctness obvious.
    DBC_HISTORY_LIMIT = 200

    def _record_dbc(self) -> None:
        import copy
        hist = self.__dict__.setdefault("_dbc_history", [])
        hist.append(copy.deepcopy(self.dbc_signals))
        del hist[:-self.DBC_HISTORY_LIMIT]
        self.__dict__["_dbc_future"] = []

    def can_undo_dbc(self) -> bool:
        return bool(self.__dict__.get("_dbc_history"))

    def can_redo_dbc(self) -> bool:
        return bool(self.__dict__.get("_dbc_future"))

    def undo_dbc(self) -> bool:
        import copy
        hist = self.__dict__.get("_dbc_history", [])
        if not hist:
            return False
        self.__dict__.setdefault("_dbc_future", []).append(
            copy.deepcopy(self.dbc_signals))
        self.dbc_signals = hist.pop()
        self.dbc_updated.emit()
        return True

    def redo_dbc(self) -> bool:
        import copy
        fut = self.__dict__.get("_dbc_future", [])
        if not fut:
            return False
        self.__dict__.setdefault("_dbc_history", []).append(
            copy.deepcopy(self.dbc_signals))
        self.dbc_signals = fut.pop()
        self.dbc_updated.emit()
        return True

    def add_dbc_signal(self, signal_def: dict):
        self._record_dbc()
        self.dbc_signals.append(signal_def)
        self.dbc_updated.emit()

    def update_dbc_signal(self, index: int, signal_def: dict):
        if 0 <= index < len(self.dbc_signals):
            self._record_dbc()
            self.dbc_signals[index] = signal_def
            self.dbc_updated.emit()

    def remove_dbc_signal(self, index: int):
        if 0 <= index < len(self.dbc_signals):
            self._record_dbc()
            self.dbc_signals.pop(index)
            self.dbc_updated.emit()

    def add_dbc_signals(self, signals: list) -> int:
        """Append many as one undo step. An import of 300 signals must not
        take 300 presses of undo to take back."""
        signals = [s for s in signals if s]
        if not signals:
            return 0
        self._record_dbc()
        self.dbc_signals.extend(signals)
        self.dbc_updated.emit()
        return len(signals)

    def replace_dbc_signals(self, signals: list) -> None:
        """Bulk replace as one undo step: imports, auto-build, project load."""
        self._record_dbc()
        self.dbc_signals = list(signals)
        self.dbc_updated.emit()

    def get_frames_for_id(self, hex_id: str, tail: int | None = None) -> pd.DataFrame:
        return self._store.frames_for_id(hex_id, tail=tail)

    def get_unique_ids(self) -> list:
        return self._store.unique_ids()

_state = None

def get_state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state
