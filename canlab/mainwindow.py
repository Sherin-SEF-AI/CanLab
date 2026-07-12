import os
import can
import pandas as pd
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTabWidget, QToolBar, QStatusBar, QLabel, QFileDialog,
    QMessageBox, QLineEdit, QPushButton, QProgressBar, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QAction

from theme import COLORS, mono_font
from core.state import get_state
from core.log_parser import parse_log_file
from core.event_correlator import parse_annotations, correlate_events
from core.dbc_manager import load_dbc
from core.bus_load import BusLoadMeter
from panels.id_panel import IDPanel
from panels.inspector_panel import InspectorPanel
from tabs.frames_tab import FramesTab
from tabs.signals_tab import SignalsTab
from tabs.plot_tab import PlotTab
from tabs.ai_engine_tab import AIEngineTab
from tabs.dbc_builder_tab import DBCBuilderTab
from tabs.code_gen_tab import CodeGenTab
from tabs.intelligence_tab import IntelligenceTab
from tabs.injection_tab import InjectionTab
from tabs.diagnostics_tab import DiagnosticsTab
from tabs.dashboard_tab import DashboardTab
from tabs.auto_re_tab import AutoRETab
from tabs.timeline_tab import TimelineTab
from tabs.obd_dashboard_tab import OBDDashboardTab
from tabs.signal_intelligence_tab import SignalIntelligenceTab
from tabs.gateway_tab import GatewayTab
from ui.animations import PulsingDot, CountUpLabel
from settings_dialog import (
    SettingsDialog, load_api_key, load_gh_token,
    load_groq_key, load_ai_provider, load_ai_model,
)


class LiveCANWorker(QThread):
    frame_received = pyqtSignal(object)
    error          = pyqtSignal(str)

    def __init__(self, interface, channel, bitrate, bus=None, parent=None,
                 fd=False, data_bitrate=None):
        """
        If `bus` is provided (e.g. PandaBus), it is used directly instead of
        creating a new python-can Bus. This is the pluggable-backend entry point.
        """
        super().__init__(parent)
        self._interface   = interface
        self._channel     = channel
        self._bitrate     = bitrate
        self._fd          = fd
        self._data_bitrate = data_bitrate
        self._injected_bus = bus   # pre-created Bus (Panda, virtual, etc.)
        self._running     = True
        self._bus         = None

    def get_bus(self):
        return self._bus

    def run(self):
        try:
            if self._injected_bus is not None:
                self._bus = self._injected_bus
            else:
                kwargs = dict(
                    channel=self._channel,
                    bustype=self._interface,
                    bitrate=self._bitrate,
                )
                if self._fd:
                    kwargs["fd"] = True
                    if self._data_bitrate:
                        kwargs["data_bitrate"] = self._data_bitrate
                self._bus = can.interface.Bus(**kwargs)
            while self._running:
                msg = self._bus.recv(timeout=0.1)
                if msg:
                    self.frame_received.emit(msg)
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._running = False
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                pass


class MultiBusWorker(QThread):
    """Spawn one LiveCANWorker per configured bus; tag frames with bus name."""
    frame_received = pyqtSignal(str, object)   # bus_name, frame
    error          = pyqtSignal(str, str)       # bus_name, error

    def __init__(self, bus_configs: list, parent=None):
        super().__init__(parent)
        self._configs  = bus_configs
        self._workers  = []

    def start_all(self):
        for cfg in self._configs:
            w = LiveCANWorker(
                interface=cfg.get("interface", "socketcan"),
                channel=cfg.get("channel", "can0"),
                bitrate=cfg.get("bitrate", 500000),
            )
            name = cfg.get("name", cfg.get("channel", "?"))
            w.frame_received.connect(lambda msg, n=name: self.frame_received.emit(n, msg))
            w.error.connect(lambda e, n=name: self.error.emit(n, e))
            w.start()
            self._workers.append(w)

    def stop_all(self):
        for w in self._workers:
            w.stop()
        self._workers.clear()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CANLAB — CAN Reverse Engineering Suite")
        self.showMaximized()

        self._state        = get_state()
        self._live_worker  = None
        self._can_settings = {"interface": "socketcan", "channel": "can0", "bitrate": 500000}
        self._api_key      = load_api_key()
        self._gh_token     = load_gh_token()
        self._frame_rate_timer = QTimer()
        self._live_frame_count = 0
        self._live_rows: list  = []
        self._bus_load_meter   = BusLoadMeter()
        self._rest_api_server  = None
        self._plugins          = []
        self._multibus_config  = []
        self._multibus_worker  = None

        self._build_central()
        self._build_toolbar()
        self._build_menubar()
        self._build_statusbar()
        self._connect_signals()

        self._frame_rate_timer.setInterval(1000)
        self._frame_rate_timer.timeout.connect(self._update_frame_rate)
        self._frame_rate_timer.start()

        self.ai_tab.set_api_key(self._api_key)
        self.ai_tab.set_ai_config(
            provider=load_ai_provider(),
            model=load_ai_model(),
            groq_key=load_groq_key(),
            api_key=self._api_key,
        )
        self._load_plugins()

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setFixedHeight(32)
        self.addToolBar(tb)

        def act(text, slot, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            a.setToolTip(tip)
            tb.addAction(a)
            return a

        # File
        act("Open Log",         self._open_log,            "Open CSV/candump log file")
        act("Open .rlog",       self._open_rlog,           "Import openpilot .rlog/.qlog")
        act("Save Project",     self._save_project,        "Save .canlab project")
        act("Open Project",     self._open_project,        "Open .canlab project")
        act("Export openpilot", self._export_openpilot_dbc,"Export openpilot DBC")
        act("Export Lua",       self._export_lua,          "Export Wireshark Lua dissector")
        act("Community Sync",   self._sync_community,      "Sync community vehicle profiles")
        tb.addSeparator()

        # GitHub
        lbl_gh = QLabel("  GitHub:")
        lbl_gh.setFont(mono_font(8))
        lbl_gh.setStyleSheet(f"color:{COLORS['dim']}")
        tb.addWidget(lbl_gh)

        self.gh_url_edit = QLineEdit()
        self.gh_url_edit.setPlaceholderText("https://github.com/owner/repo")
        self.gh_url_edit.setFixedWidth(300)
        self.gh_url_edit.setFont(mono_font(8))
        self.gh_url_edit.setStyleSheet(
            f"QLineEdit {{ background:{COLORS['panel_bg']}; color:{COLORS['text']}; "
            f"border:1px solid {COLORS['border']}; border-radius:2px; padding:1px 4px; }}"
            f"QLineEdit:focus {{ border-color:{COLORS['green']}; }}"
        )
        self.gh_url_edit.returnPressed.connect(self._fetch_github)
        tb.addWidget(self.gh_url_edit)

        btn_fetch = QPushButton("Fetch")
        btn_fetch.setFixedWidth(48)
        btn_fetch.setFixedHeight(22)
        btn_fetch.setFont(mono_font(8))
        btn_fetch.setStyleSheet(
            f"QPushButton {{ background:{COLORS['panel_bg']}; color:{COLORS['green']}; "
            f"border:1px solid {COLORS['green']}; border-radius:2px; padding:1px 4px; }}"
            f"QPushButton:hover {{ background:#003a1f; }}"
        )
        btn_fetch.clicked.connect(self._fetch_github)
        tb.addWidget(btn_fetch)

        self.lbl_repo_status = QLabel("  no repo")
        self.lbl_repo_status.setFont(mono_font(8))
        self.lbl_repo_status.setStyleSheet(f"color:{COLORS['dim']}")
        tb.addWidget(self.lbl_repo_status)
        tb.addSeparator()

        # CAN
        self._act_connect    = act("Connect CAN",  self._connect_can,    "Connect live CAN bus")
        self._act_disconnect = act("Disconnect",   self._disconnect_can, "Disconnect live CAN")
        self._act_disconnect.setEnabled(False)
        tb.addSeparator()

        # Global transmit safety gate — disarmed by default. Nothing (injection,
        # replay, fuzz, gateway) can write to the bus until the user arms it.
        self._act_arm = QAction("ARM TX: OFF", self)
        self._act_arm.setCheckable(True)
        self._act_arm.setToolTip("Arm/disarm bus transmit. Off = no frames can be sent.")
        self._act_arm.toggled.connect(self._toggle_arm)
        tb.addAction(self._act_arm)
        tb.addSeparator()

        # AI + DBC
        act("Run AI RE",  self._run_ai_re,     "AI-analyze all unknown IDs")
        act("Export DBC", self._export_dbc,    "Export DBC file")
        act("Code Gen",   self._generate_code, "Switch to Code Gen tab")
        tb.addSeparator()

        # REST API toggle
        self._act_rest = act("REST API: OFF", self._toggle_rest_api, "Toggle REST API server")
        tb.addSeparator()

        # Plugins
        btn_plugins = QPushButton("Plugins…")
        btn_plugins.setFixedHeight(22)
        btn_plugins.setFont(mono_font(8))
        btn_plugins.clicked.connect(self._show_plugins_menu)
        tb.addWidget(btn_plugins)

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menubar(self):
        mb = self.menuBar()

        # File menu
        file_menu = mb.addMenu("File")
        for text, slot in [
            ("Open Log…",           self._open_log),
            ("Open .rlog…",         self._open_rlog),
            ("Save Project…",       self._save_project),
            ("Open Project…",       self._open_project),
        ]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            file_menu.addAction(a)
        file_menu.addSeparator()
        for text, slot in [
            ("Export DBC…",             self._export_dbc),
            ("Export openpilot DBC…",   self._export_openpilot_dbc),
            ("Export Wireshark Lua…",   self._export_lua),
        ]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            file_menu.addAction(a)
        file_menu.addSeparator()
        a = QAction("Import CAN Matrix…", self)
        a.triggered.connect(self._import_can_matrix)
        file_menu.addAction(a)
        a = QAction("Import ARXML…", self)
        a.triggered.connect(lambda: self.dbc_tab._import_arxml())
        file_menu.addAction(a)
        file_menu.addSeparator()
        a = QAction("Community Sync…", self)
        a.triggered.connect(self._sync_community)
        file_menu.addAction(a)

        # Tools menu
        tools_menu = mb.addMenu("Tools")
        for text, slot in [
            ("Run AI RE",                self._run_ai_re),
            ("Code Gen",                 self._generate_code),
            ("Auto-discover OBD-II PIDs…", self._obd_discover),
            ("ML Signal Intelligence…",  self._open_ml_intel),
            ("CAN Gateway…",             self._open_gateway),
        ]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            tools_menu.addAction(a)

        # Settings menu
        settings_menu = mb.addMenu("Settings")
        a = QAction("Preferences…", self)
        a.triggered.connect(self._open_settings)
        a.setShortcut("Ctrl+,")
        settings_menu.addAction(a)

    # ── Central layout ────────────────────────────────────────────────────────

    def _build_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QHBoxLayout(central)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        self.id_panel = IDPanel()
        main_lay.addWidget(self.id_panel)

        self.tabs = QTabWidget()

        # Core tabs (0–5)
        self.frames_tab  = FramesTab()
        self.signals_tab = SignalsTab()
        self.plot_tab    = PlotTab()
        self.ai_tab      = AIEngineTab()
        self.dbc_tab     = DBCBuilderTab()
        self.codegen_tab = CodeGenTab()

        # Advanced tabs (6–10)
        self.intelligence_tab = IntelligenceTab()
        self.injection_tab    = InjectionTab()
        self.diagnostics_tab  = DiagnosticsTab()
        self.dashboard_tab    = DashboardTab()
        self.auto_re_tab      = AutoRETab()
        self.timeline_tab     = TimelineTab()
        self.obd_tab          = OBDDashboardTab()
        self.ml_intel_tab     = SignalIntelligenceTab()
        self.gateway_tab      = GatewayTab()

        self.tabs.addTab(self.frames_tab,       "FRAMES")
        self.tabs.addTab(self.signals_tab,      "SIGNALS")
        self.tabs.addTab(self.plot_tab,         "PLOT")
        self.tabs.addTab(self.ai_tab,           "AI ENGINE ★")
        self.tabs.addTab(self.dbc_tab,          "DBC BUILDER")
        self.tabs.addTab(self.codegen_tab,      "CODE GEN")
        self.tabs.addTab(self.intelligence_tab, "INTELLIGENCE")
        self.tabs.addTab(self.injection_tab,    "INJECTION")
        self.tabs.addTab(self.diagnostics_tab,  "DIAGNOSTICS")
        self.tabs.addTab(self.dashboard_tab,    "DASHBOARD")
        self.tabs.addTab(self.auto_re_tab,      "AUTO-RE ★")
        self.tabs.addTab(self.timeline_tab,     "TIMELINE ★")
        self.tabs.addTab(self.obd_tab,          "OBD-II ★")
        self.tabs.addTab(self.ml_intel_tab,     "ML INTEL ★")
        self.tabs.addTab(self.gateway_tab,      "GATEWAY ★")

        main_lay.addWidget(self.tabs, stretch=1)

        self.inspector = InspectorPanel()
        main_lay.addWidget(self.inspector)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._can_dot = PulsingDot(color=COLORS["green"], size=10)
        sb.addWidget(self._can_dot)

        self.lbl_connection = QLabel("BUS: disconnected")
        self.lbl_connection.setFont(mono_font(8))
        self.lbl_connection.setStyleSheet(f"color:{COLORS['dim']}")
        sb.addWidget(self.lbl_connection)
        sb.addWidget(_sep())

        self.lbl_repo_sb = QLabel("REPO: none")
        self.lbl_repo_sb.setFont(mono_font(8))
        self.lbl_repo_sb.setStyleSheet(f"color:{COLORS['dim']}")
        sb.addWidget(self.lbl_repo_sb)
        sb.addWidget(_sep())

        self.lbl_fingerprint_sb = QLabel("FP: —")
        self.lbl_fingerprint_sb.setFont(mono_font(8))
        self.lbl_fingerprint_sb.setStyleSheet(f"color:{COLORS['dim']}")
        sb.addWidget(self.lbl_fingerprint_sb)

        # Bus load bar (right side)
        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setValue(0)
        self.load_bar.setFixedWidth(80)
        self.load_bar.setFixedHeight(14)
        self.load_bar.setTextVisible(False)
        self.load_bar.setStyleSheet(
            f"QProgressBar {{ border:1px solid {COLORS['border']}; border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{COLORS['green']}; }}"
        )
        sb.addPermanentWidget(QLabel("LOAD:", font=mono_font(8)))
        sb.addPermanentWidget(self.load_bar)
        sb.addPermanentWidget(_sep())

        self.lbl_frame_rate = QLabel("0 fps")
        self.lbl_frame_rate.setFont(mono_font(8))
        sb.addPermanentWidget(self.lbl_frame_rate)
        sb.addPermanentWidget(_sep())

        self.lbl_selected_id = QLabel("ID: none")
        self.lbl_selected_id.setFont(mono_font(8))
        sb.addPermanentWidget(self.lbl_selected_id)
        sb.addPermanentWidget(_sep())

        self.lbl_total_frames = CountUpLabel("0", suffix="frames")
        self.lbl_total_frames.setFont(mono_font(8))
        sb.addPermanentWidget(self.lbl_total_frames)

    # ── Signal wiring ─────────────────────────────────────────────────────────

    def _connect_signals(self):
        self._state.id_selected.connect(self._on_id_selected)
        self._state.frames_loaded.connect(self._on_frames_loaded)
        self._state.can_connected.connect(self._on_can_status)
        self._state.repo_loaded.connect(self._on_repo_loaded)
        self._state.fingerprint_matched.connect(self._on_fingerprint)
        self._state.bus_load_update.connect(self._on_bus_load_update)
        self.id_panel.analyze_requested.connect(self._analyze_id)
        self.id_panel.plot_requested.connect(self._plot_id)
        self.inspector.send_to_ai.connect(self._analyze_id)

    # ── File actions ──────────────────────────────────────────────────────────

    def _open_log(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CAN Log", "",
            "Log Files (*.csv *.log *.pcap *.pcapng);;pcap (*.pcap *.pcapng);;All Files (*)"
        )
        if path:
            self._load_log_file(path)

    def _load_log_file(self, path: str):
        try:
            df = parse_log_file(path)
            if df.empty:
                QMessageBox.warning(self, "Empty", "No frames found in file.")
                return
            self._state.load_frames(df, os.path.basename(path))
            self._correlate_annotations(df)
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", str(e))

    def _load_dbc_file(self, path: str):
        try:
            sigs = load_dbc(path)
            for sig in sigs:
                self._state.add_dbc_signal(sig)
            self.statusBar().showMessage(
                f"DBC: imported {len(sigs)} signals from {path}", 4000
            )
        except Exception as e:
            QMessageBox.critical(self, "DBC Import Error", str(e))

    # ── Project save / load ───────────────────────────────────────────────────

    def _save_project(self):
        from core.project import save_project
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "project.canlab", "CANLAB Project (*.canlab)"
        )
        if not path:
            return
        try:
            save_project(self._state, path)
            self.statusBar().showMessage(f"Project saved: {path}", 4000)
            self.setWindowTitle(
                f"CANLAB — {os.path.basename(path)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _open_project(self):
        from core.project import load_project
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "CANLAB Project (*.canlab)"
        )
        if not path:
            return
        try:
            load_project(self._state, path)
            self.statusBar().showMessage(f"Project loaded: {path}", 4000)
            self.setWindowTitle(f"CANLAB — {os.path.basename(path)}")
            self.lbl_total_frames.animate_to(len(self._state.frames_df))
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    # ── GitHub fetch ──────────────────────────────────────────────────────────

    def _fetch_github(self):
        from core.github_fetcher import GitHubRepoDialog
        url = self.gh_url_edit.text().strip()
        dlg = GitHubRepoDialog(
            initial_url=url,
            token=self._gh_token,
            parent=self,
        )
        dlg.logs_ready.connect(self._on_github_logs_ready)
        dlg.dbcs_ready.connect(self._on_github_dbcs_ready)
        dlg.readme_ready.connect(self._on_github_readme)
        dlg.repo_meta_ready.connect(self._on_repo_meta)
        dlg.exec()

    def _on_github_logs_ready(self, paths: list):
        loaded = 0
        for path in paths:
            try:
                self._load_log_file(path)
                loaded += 1
            except Exception as e:
                self.statusBar().showMessage(
                    f"Could not load {os.path.basename(path)}: {e}", 5000
                )
        if loaded:
            self.statusBar().showMessage(f"Loaded {loaded} log file(s) from repo.", 4000)

    def _on_github_dbcs_ready(self, paths: list):
        loaded = 0
        for path in paths:
            try:
                self._load_dbc_file(path)
                loaded += 1
            except Exception as e:
                self.statusBar().showMessage(
                    f"Could not import {os.path.basename(path)}: {e}", 5000
                )
        if loaded:
            self.statusBar().showMessage(f"Imported {loaded} DBC file(s) from repo.", 4000)

    def _on_github_readme(self, readme: str):
        self._state.repo_readme = readme
        events = parse_annotations(readme)
        self._pending_events = events
        if not self._state.frames_df.empty:
            self._correlate_with_events(self._state.frames_df, events)

    def _on_repo_meta(self, info: dict):
        self._state.set_repo_context(
            info=info,
            readme=self._state.repo_readme,
            url=self.gh_url_edit.text().strip(),
        )

    def _on_repo_loaded(self, info: dict):
        name = f"{info.get('owner','')}/{info.get('repo','')}"
        self.lbl_repo_status.setText(f"  {name}")
        self.lbl_repo_status.setStyleSheet(f"color:{COLORS['green']}")
        self.lbl_repo_sb.setText(f"REPO: {name}")
        self.lbl_repo_sb.setStyleSheet(f"color:{COLORS['green']}")
        self.setWindowTitle(
            f"CANLAB — {name}  ({info.get('description','')})"
        )

    def _on_fingerprint(self, result: dict):
        model = result.get("model", "?")
        conf  = int(result.get("confidence", 0) * 100)
        self.lbl_fingerprint_sb.setText(f"FP: {model} ({conf}%)")
        self.lbl_fingerprint_sb.setStyleSheet(f"color:{COLORS['green']}")

    # ── Annotation correlation ────────────────────────────────────────────────

    def _correlate_annotations(self, df: pd.DataFrame):
        events = getattr(self, "_pending_events", [])
        if events and not df.empty:
            self._correlate_with_events(df, events)

    def _correlate_with_events(self, df: pd.DataFrame, events: list):
        if not events or df.empty:
            return
        correlations = correlate_events(df, events)
        self._state.annotations = correlations
        total_ids = sum(len(v) for v in correlations.values())
        if correlations:
            self.statusBar().showMessage(
                f"Correlated {len(correlations)} events across {total_ids} IDs", 5000
            )

    # ── CAN live ──────────────────────────────────────────────────────────────

    def _connect_can(self):
        iface   = self._can_settings["interface"]
        channel = self._can_settings["channel"]
        bitrate = self._can_settings["bitrate"]
        fd            = self._can_settings.get("fd", False)
        data_bitrate  = self._can_settings.get("data_bitrate")

        # Panda backend support
        injected_bus = None
        if getattr(self._state, "active_backend", "python-can") == "panda":
            try:
                from core.panda_backend import PandaBus, is_available
                if is_available():
                    injected_bus = PandaBus(
                        bus_index=0, bitrate=bitrate,
                        safety_model=getattr(self._state, "panda_safety_model",
                                             "SAFETY_NOOUTPUT"),
                    )
                else:
                    QMessageBox.warning(
                        self, "Panda",
                        "panda library not installed. "
                        "Run: pip install panda --break-system-packages\n\n"
                        "Falling back to python-can."
                    )
            except Exception as e:
                QMessageBox.warning(self, "Panda Error", str(e))

        self._live_worker = LiveCANWorker(iface, channel, bitrate, bus=injected_bus,
                                          fd=fd, data_bitrate=data_bitrate)
        self._live_worker.frame_received.connect(self._on_live_frame)
        self._live_worker.error.connect(self._on_live_error)
        self._live_worker.started.connect(self._on_worker_started)
        self._live_worker.start()
        self._act_connect.setEnabled(False)
        self._act_disconnect.setEnabled(True)
        self._state.can_connected.emit(True)

    def _on_worker_started(self):
        # Share the bus handle with state so injection + diagnostics can use it.
        # The Bus object is created inside the worker thread and may not exist
        # immediately, so poll for it rather than assuming it's ready after a
        # fixed 500 ms (slow USB/Panda opens raced that and left can_bus=None).
        self._share_bus_attempts = 0
        self._poll_share_bus()

    def _poll_share_bus(self):
        if not self._live_worker:
            return
        bus = self._live_worker.get_bus()
        if bus is not None:
            self._state.can_bus      = bus
            self._state.is_connected = True
            return
        self._share_bus_attempts += 1
        if self._share_bus_attempts < 50:      # retry up to ~5 s
            QTimer.singleShot(100, self._poll_share_bus)
        else:
            self.statusBar().showMessage(
                "CAN bus handle not ready — injection/diagnostics unavailable", 5000
            )

    def _disconnect_can(self):
        if self._live_worker:
            self._live_worker.stop()
            self._live_worker = None
        self._state.can_bus      = None
        self._state.is_connected = False
        self._act_connect.setEnabled(True)
        self._act_disconnect.setEnabled(False)
        self._state.can_connected.emit(False)
        self._bus_load_meter.reset()

    def _on_live_frame(self, msg):
        self._live_frame_count += 1

        # Bus load
        load = self._bus_load_meter.add_frame(msg.dlc, msg.timestamp)
        if load is not None:
            self._state.bus_load_update.emit(load)

        # Trigger check
        if self._state.triggers:
            from core.trigger import check_triggers
            fired = check_triggers(
                self._state.triggers, msg.arbitration_id, bytes(msg.data)
            )
            for rule in fired:
                self._state.trigger_fired.emit(rule, msg)

        # UDS response routing
        if 0x7E8 <= msg.arbitration_id <= 0x7EF:
            self._state.uds_response.emit(msg.arbitration_id, bytes(msg.data))

        byte_data = list(msg.data) + [None] * (8 - len(msg.data))
        row = {
            "Timestamp": msg.timestamp,
            "ID":        format(msg.arbitration_id, "03X"),
            "Bus":       "live",
            "DLC":       msg.dlc,
            "Delta":     0.0,
            **{f"B{i}": byte_data[i] for i in range(8)},
        }
        self._live_rows.append(row)
        if len(self._live_rows) >= 50:
            df = pd.DataFrame(self._live_rows)
            self._state.append_frames(df)
            self._live_rows.clear()

    def _on_live_error(self, err: str):
        QMessageBox.critical(self, "CAN Error", err)
        self._disconnect_can()

    # ── REST API ──────────────────────────────────────────────────────────────

    def _toggle_arm(self, checked: bool):
        from core.safety import set_armed
        if checked:
            ok = QMessageBox.warning(
                self, "Arm Bus Transmit",
                "Arming enables injection, replay, fuzzing, and gateway forwarding "
                "to write frames onto the connected bus.\n\n"
                "Only arm on an isolated bench setup — never on a vehicle you are "
                "driving. Arm transmit now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                self._act_arm.setChecked(False)   # reverts text via toggled
                return
        set_armed(checked)
        self._act_arm.setText("ARM TX: ON" if checked else "ARM TX: OFF")
        self.statusBar().showMessage(
            "Bus transmit ARMED" if checked else "Bus transmit disarmed", 3000
        )

    def _toggle_rest_api(self):
        if not self._state.rest_api_running:
            self._start_rest_api()
        else:
            self._stop_rest_api()

    def _start_rest_api(self):
        from core.rest_api import RestAPIServer
        try:
            self._rest_api_server = RestAPIServer(
                state_getter=get_state,
                port=self._state.rest_api_port,
            )
            self._rest_api_server.start()
            self._state.rest_api_running = True
            self._act_rest.setText(f"REST API: ON :{self._state.rest_api_port}")
            token = self._rest_api_server.token
            self.statusBar().showMessage(
                f"REST API running on 127.0.0.1:{self._state.rest_api_port}", 4000
            )
            QMessageBox.information(
                self, "REST API — Access Token",
                "The REST API is running on 127.0.0.1:"
                f"{self._state.rest_api_port}.\n\n"
                "Every request (including POST /inject) requires this header:\n\n"
                f"    X-API-Token: {token}\n\n"
                "Keep it secret — anyone with this token can inject CAN frames.",
            )
        except Exception as e:
            QMessageBox.warning(self, "REST API", f"Could not start: {e}")

    def _stop_rest_api(self):
        if self._rest_api_server:
            self._rest_api_server.stop()
            self._rest_api_server = None
        self._state.rest_api_running = False
        self._act_rest.setText("REST API: OFF")

    # ── Plugins ───────────────────────────────────────────────────────────────

    def _load_plugins(self):
        from core.plugin_loader import discover_plugins, activate_plugins
        self._plugins = discover_plugins()
        activated = activate_plugins(self._plugins, self)
        if activated:
            self.statusBar().showMessage(
                f"Plugins loaded: {', '.join(activated)}", 5000
            )

    def _show_plugins_menu(self):
        from core.plugin_loader import discover_plugins
        self._plugins = discover_plugins()
        menu = QMenu(self)
        if not self._plugins:
            menu.addAction("No plugins found  (~/.canlab/plugins/)")
        else:
            for p in self._plugins:
                status = "✓" if p.get("enabled") else "✗"
                err    = f"  [{p.get('error','')}]" if p.get("error") else ""
                a = menu.addAction(f"{status} {p['name']} v{p['version']}{err}")
                a.setEnabled(False)
        menu.exec(self.cursor().pos())

    # ── Bus load status bar ───────────────────────────────────────────────────

    def _on_bus_load_update(self, load: float):
        self.load_bar.setValue(int(load * 100))

    # ── Toolbar actions ───────────────────────────────────────────────────────

    def _run_ai_re(self):
        self.tabs.setCurrentIndex(3)
        self.ai_tab._add_all_unknown()
        self.ai_tab._run_queue()

    def _export_dbc(self):
        self.tabs.setCurrentIndex(4)
        self.dbc_tab._export_dbc()

    def _generate_code(self):
        self.tabs.setCurrentIndex(5)

    def _obd_discover(self):
        self.tabs.setCurrentIndex(12)   # OBD-II tab
        self.obd_tab._discover_pids()

    def _open_ml_intel(self):
        self.tabs.setCurrentIndex(13)   # ML INTEL tab

    def _open_gateway(self):
        self.tabs.setCurrentIndex(14)   # GATEWAY tab

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._api_key      = dlg.get_api_key()
            self._gh_token     = dlg.get_gh_token()
            self._can_settings = dlg.get_can_settings()
            self._state.rest_api_port = dlg.get_rest_api_port()
            self.ai_tab.set_api_key(self._api_key)
            self.ai_tab.set_ai_config(
                provider=dlg.get_ai_provider(),
                model=dlg.get_ai_model(),
                groq_key=dlg.get_groq_key(),
                api_key=self._api_key,
            )
            gh_url = dlg.get_github_url()
            if gh_url and not self.gh_url_edit.text().strip():
                self.gh_url_edit.setText(gh_url)
            self._multibus_config = dlg.get_multibus_config()

    def _open_rlog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open openpilot Log", "",
            "openpilot Logs (*.rlog *.qlog);;All Files (*)"
        )
        if path:
            self._load_log_file(path)

    def _export_openpilot_dbc(self):
        if not self._state.dbc_signals:
            QMessageBox.information(self, "Empty", "No signals defined in DBC Builder.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export openpilot DBC", "openpilot.dbc", "DBC (*.dbc)"
        )
        if not path:
            return
        try:
            from core.dbc_manager import export_opendbc
            from core.openpilot_export import HYUNDAI_MSG_META
            dbc_str = export_opendbc(self._state.dbc_signals, HYUNDAI_MSG_META)
            with open(path, "w") as f:
                f.write(dbc_str)
            self.statusBar().showMessage(f"openpilot DBC exported: {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _export_lua(self):
        if not self._state.dbc_signals:
            QMessageBox.information(self, "Empty", "No signals defined in DBC Builder.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Wireshark Lua Dissector", "canlab_dbc.lua", "Lua (*.lua)"
        )
        if not path:
            return
        try:
            from core.lua_exporter import signals_to_lua_dissector
            lua_str = signals_to_lua_dissector(self._state.dbc_signals)
            with open(path, "w") as f:
                f.write(lua_str)
            self.statusBar().showMessage(f"Lua dissector exported: {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    def _import_can_matrix(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import CAN Matrix", "",
            "CAN Matrix (*.xlsx *.xls *.csv);;All Files (*)"
        )
        if not path:
            return
        try:
            from core.can_matrix_parser import parse_can_matrix
            from core.dbc_manager import build_db_from_signals
            sigs = parse_can_matrix(path)
            for sig in sigs:
                self._state.add_dbc_signal(sig)
            build_db_from_signals(self._state.dbc_signals)
            self.statusBar().showMessage(
                f"CAN Matrix: imported {len(sigs)} signals from {os.path.basename(path)}", 5000
            )
            self.tabs.setCurrentIndex(4)   # DBC Builder tab
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def _sync_community(self):
        url = getattr(self._state, "community_profiles_url", "")
        if not url:
            QMessageBox.information(
                self, "No URL",
                "Set a Community Profiles URL in Settings → GITHUB."
            )
            return
        self.tabs.setCurrentIndex(6)   # INTELLIGENCE tab
        self.intelligence_tab._comm_fetch()

    def _analyze_id(self, hex_id: str):
        self.tabs.setCurrentIndex(3)
        self.ai_tab.queue_id(hex_id)
        self.ai_tab._load_id(hex_id)

    def _plot_id(self, hex_id: str):
        self.tabs.setCurrentIndex(2)
        self.plot_tab._highlight_id(hex_id)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_id_selected(self, hex_id: str):
        self.lbl_selected_id.setText(f"ID: 0x{hex_id}")

    def _on_frames_loaded(self, count: int):
        self.lbl_total_frames.animate_to(count)
        events = getattr(self, "_pending_events", [])
        if events and not self._state.frames_df.empty:
            self._correlate_with_events(self._state.frames_df, events)

    def _on_can_status(self, connected: bool):
        self._can_dot.set_active(connected)
        if connected:
            ch = self._can_settings["channel"]
            br = self._can_settings["bitrate"]
            self.lbl_connection.setText(f"BUS: {ch} @ {br}bps")
            self.lbl_connection.setStyleSheet(f"color:{COLORS['green']}")
        else:
            self.lbl_connection.setText("BUS: disconnected")
            self.lbl_connection.setStyleSheet(f"color:{COLORS['dim']}")

    def _update_frame_rate(self):
        fps   = self._live_frame_count
        self._live_frame_count = 0
        total = len(self._state.frames_df)
        self.lbl_frame_rate.setText(f"{fps} fps")
        if total:
            self.lbl_total_frames.animate_to(total)

    def closeEvent(self, event):
        self._stop_rest_api()
        if self._live_worker:
            self._live_worker.stop()
        event.accept()


def _sep() -> QLabel:
    lbl = QLabel("|")
    lbl.setStyleSheet(f"color:{COLORS['border']}; padding:0 4px;")
    return lbl
