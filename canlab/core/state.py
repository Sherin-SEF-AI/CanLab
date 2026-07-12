from PyQt6.QtCore import QObject, pyqtSignal
import pandas as pd


class AppState(QObject):
    id_selected       = pyqtSignal(str)
    frames_loaded     = pyqtSignal(int)
    signal_analyzed   = pyqtSignal(str)
    dbc_updated       = pyqtSignal()
    can_connected     = pyqtSignal(bool)
    frames_updated    = pyqtSignal()
    source_added      = pyqtSignal(str, int)
    repo_loaded       = pyqtSignal(dict)

    # New signals for advanced features
    project_loaded      = pyqtSignal()
    fingerprint_matched = pyqtSignal(dict)
    trigger_fired       = pyqtSignal(dict, object)   # rule, frame
    uds_response        = pyqtSignal(int, bytes)     # arb_id, data
    replay_tick         = pyqtSignal(int, int)       # current, total
    bus_load_update     = pyqtSignal(float)          # 0.0–1.0
    opendbc_matched     = pyqtSignal(dict)
    anomaly_requested   = pyqtSignal(str, object)    # hex_id, frames_df

    # ── New signals for 12-feature additions ──────────────────────────────────
    canfd_toggled        = pyqtSignal(bool)
    change_detected      = pyqtSignal(list)           # list of delta dicts
    fuzz_progress        = pyqtSignal(int, int)        # done, total
    multibus_frame       = pyqtSignal(str, object)     # bus_name, frame
    safety_cutout        = pyqtSignal(float, str)      # value_at_cutout, reason
    note_updated         = pyqtSignal(str)             # signal_key

    # ── New signals for 8 production enhancements ─────────────────────────────
    isotp_response       = pyqtSignal(int, bytes)      # arb_id, full assembled payload
    bus_health_update    = pyqtSignal(dict)             # health snapshot
    test_step_completed  = pyqtSignal(int, bool, str)  # step_idx, ok, message
    j1939_decoded        = pyqtSignal(int, dict)        # pgn, {spn: value}
    dbc_db_updated       = pyqtSignal()                 # cantools cache rebuilt

    # ── OBD-II live gauges ────────────────────────────────────────────────────
    pid_value_updated    = pyqtSignal(int, float, str)  # pid, value, unit

    # ── Signal Intelligence (ML) ──────────────────────────────────────────────
    ml_analysis_ready    = pyqtSignal(str, dict)         # id, roles_dict
    anomaly_detected     = pyqtSignal(str, float)        # id, score

    def __init__(self, parent=None):
        super().__init__(parent)

        self.frames_df:        pd.DataFrame = pd.DataFrame()
        self.selected_id:      str          = ""
        self.sources:          list         = []
        self.can_bus           = None
        self.is_connected:     bool         = False
        self.dbc_signals:      list         = []
        self.analyzed_ids:     dict         = {}
        self.live_frame_count: int          = 0
        self.frame_rate:       float        = 0.0
        self.annotations:      dict         = {}

        # GitHub repo context
        self.repo_info:        dict         = {}
        self.repo_readme:      str          = ""
        self.repo_url:         str          = ""

        # Advanced feature state
        self.diff_baseline_df: pd.DataFrame = pd.DataFrame()
        self.periodicities:    dict         = {}   # id -> cycle_time_ms
        self.fingerprint:      dict         = {}   # model, confidence, matched_ids
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
        self.multibus_buses:    dict         = {}   # name -> Bus instance
        self.change_baseline                 = None # ChangeRecorder snapshot
        self.notes_by_signal:   dict         = {}   # "{msg_id}/{sig_name}" -> str
        self.fuzz_running:      bool         = False
        self.active_backend:    str          = "python-can"
        self.panda_safety_model: str         = "SAFETY_NOOUTPUT"
        self.community_profiles: list        = []
        self.community_profiles_url: str     = (
            "https://raw.githubusercontent.com/commaai/opendbc/master/"
            "opendbc/can/hyundai_kona.dbc"
        )

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

    def load_frames(self, df: pd.DataFrame, source_name: str):
        self.frames_df = df
        count = len(df)
        self.sources.append({"name": source_name, "count": count})
        self.frames_loaded.emit(count)
        self.source_added.emit(source_name, count)
        self.frames_updated.emit()

    def append_frames(self, new_df: pd.DataFrame):
        if self.frames_df.empty:
            self.frames_df = new_df
        else:
            self.frames_df = pd.concat([self.frames_df, new_df], ignore_index=True)
        self.frames_updated.emit()

    def set_repo_context(self, info: dict, readme: str, url: str):
        self.repo_info   = info
        self.repo_readme = readme
        self.repo_url    = url
        self.repo_loaded.emit(info)

    def add_dbc_signal(self, signal_def: dict):
        self.dbc_signals.append(signal_def)
        self.dbc_updated.emit()

    def update_dbc_signal(self, index: int, signal_def: dict):
        if 0 <= index < len(self.dbc_signals):
            self.dbc_signals[index] = signal_def
            self.dbc_updated.emit()

    def remove_dbc_signal(self, index: int):
        if 0 <= index < len(self.dbc_signals):
            self.dbc_signals.pop(index)
            self.dbc_updated.emit()

    def get_frames_for_id(self, hex_id: str) -> pd.DataFrame:
        if self.frames_df.empty:
            return pd.DataFrame()
        from core.canid import normalize_id
        return self.frames_df[self.frames_df["ID"] == normalize_id(hex_id)].copy()

    def get_unique_ids(self) -> list:
        if self.frames_df.empty:
            return []
        return sorted(self.frames_df["ID"].unique().tolist())


_state = None

def get_state() -> AppState:
    global _state
    if _state is None:
        _state = AppState()
    return _state
