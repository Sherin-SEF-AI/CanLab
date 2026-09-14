import os
import can
import pandas as pd
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QTabWidget, QToolBar, QStatusBar, QLabel, QFileDialog,
    QMessageBox, QProgressBar, QMenu, QComboBox, QToolButton, QSizePolicy, QVBoxLayout,
)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QColor

from canlab.theme import COLORS, dot_icon, mono_font
from canlab.core.state import get_state
from canlab.core.log_parser import parse_log_file
from canlab.core.dbc_manager import load_dbc
from canlab.panels.id_panel import IDPanel
from canlab.panels.inspector_panel import InspectorPanel
from canlab.tabs.frames_tab import FramesTab
from canlab.tabs.sniffer_tab import SnifferTab
from canlab.tabs.signals_tab import SignalsTab
from canlab.tabs.plot_tab import PlotTab
from canlab.tabs.ai_engine_tab import AIEngineTab
from canlab.tabs.dbc_builder_tab import DBCBuilderTab
from canlab.tabs.code_gen_tab import CodeGenTab
from canlab.tabs.intelligence_tab import IntelligenceTab
from canlab.tabs.injection_tab import InjectionTab
from canlab.tabs.diagnostics_tab import DiagnosticsTab
from canlab.tabs.dashboard_tab import DashboardTab
from canlab.tabs.auto_re_tab import AutoRETab
from canlab.tabs.timeline_tab import TimelineTab
from canlab.tabs.obd_dashboard_tab import OBDDashboardTab
from canlab.tabs.signal_intelligence_tab import SignalIntelligenceTab
from canlab.tabs.gateway_tab import GatewayTab
from canlab.ui.animations import PulsingDot, CountUpLabel
from canlab.ui.workspace_bar import WorkspaceBar
from canlab.settings_dialog import (
    SettingsDialog, load_api_key,
    load_groq_key, load_openai_key, load_ai_provider, load_ai_model,
)
import logging

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    # Emitted from a bus receive thread; queued to the GUI thread by Qt.
    live_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._loaded_name = ""
        self._update_title()

        self._state        = get_state()
        self._hubs: list   = []        # one BusHub per connected bus
        self._can_settings = _saved_can_settings()
        self._api_key      = load_api_key()
        self._frame_rate_timer = QTimer()
        self._drain_timer      = QTimer()
        self._live_frame_count = 0
        self._rest_api_server  = None
        self._mcp_service      = None
        self._plugins          = []
        self._multibus_config  = _saved_multibus()

        self._build_central()
        self._build_toolbar()
        self._build_menubar()
        self._build_statusbar()
        self._connect_signals()
        self._restore_geometry()

        self._frame_rate_timer.setInterval(1000)
        self._frame_rate_timer.timeout.connect(self._update_frame_rate)
        self._frame_rate_timer.start()

        # Captured frames are handed from the receive threads to the GUI in
        # batches on a timer, so a busy bus cannot drive the UI update rate.
        self._drain_timer.setInterval(250)
        self._drain_timer.timeout.connect(self._drain_live_frames)
        self.live_error.connect(self._on_live_error)

        from canlab.settings_dialog import SettingsDialog, settings
        _st = settings()
        self._state.rest_api_port = int(_st.value(SettingsDialog.S_REST_PORT, 8765, int))
        self._state.vehicle_profile = _st.value(SettingsDialog.S_PROFILE, "generic", str)
        self._state.active_backend = _st.value(SettingsDialog.S_BACKEND, "python-can", str)
        self._state.panda_safety_model = _st.value(
            SettingsDialog.S_PANDA_SAFETY, "SAFETY_NOOUTPUT", str)
        self._state.store.set_cap(int(_st.value(SettingsDialog.S_FRAME_CAP, 500_000, int)))

        self.ai_tab.set_api_key(self._api_key)
        self.ai_tab.set_ai_config(
            provider=load_ai_provider(),
            model=load_ai_model(),
            groq_key=load_groq_key(),
            api_key=self._api_key,
            openai_key=load_openai_key(),
        )
        self._load_plugins()
        if _st.value(SettingsDialog.S_MCP_AUTOSTART, False, bool):
            self._start_mcp(quiet=True)

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        """Three groups: what you open, the bus you are on, what is running.

        Everything here is also in the menus. The toolbar carries only what a
        session actually reaches for, so the row fits without items falling off
        the end, and the three running-state buttons sit together on the right
        where state belongs rather than mixed in among the verbs.
        """
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(9, 9))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self._toolbar = tb

        def act(text, slot, tip=""):
            a = QAction(text, self)
            a.triggered.connect(slot)
            a.setToolTip(tip)
            tb.addAction(a)
            return a

        def menu_button(text, tip, entries):
            """One button that drops a menu, so a family of related actions
            costs one slot in the row instead of four."""
            a = QAction(text, self)
            a.setToolTip(tip)
            menu = QMenu(self)
            for label, slot in entries:
                if slot is None:
                    menu.addSeparator()
                else:
                    menu.addAction(label).triggered.connect(slot)
            a.setMenu(menu)
            tb.addAction(a)
            btn = tb.widgetForAction(a)
            if btn is not None:
                btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            return a, menu

        # ── files in, files out ───────────────────────────────────────────────
        menu_button("Open", "Open a capture or a saved project", [
            ("Open Log…", self._open_log),
            ("Open openpilot .rlog…", self._open_rlog),
            (None, None),
            ("Open Project…", self._open_project),
        ])
        act("Save", self._save_project, "Save the .canlab project")
        menu_button("Export", "Write the signal definitions out", [
            ("Export DBC…", self._export_dbc),
            ("Export openpilot DBC…", self._export_openpilot_dbc),
            ("Export Wireshark Lua…", self._export_lua),
            (None, None),
            ("Export decoded time-series…", self._export_timeseries),
        ])
        tb.addSeparator()

        # ── the bus ───────────────────────────────────────────────────────────
        lbl = QLabel(" Bus ")
        lbl.setFont(mono_font(8))
        lbl.setStyleSheet(f"color:{COLORS['dim']}; background:transparent")
        tb.addWidget(lbl)
        self.adapter_combo = QComboBox()
        self.adapter_combo.setFont(mono_font(8))
        self.adapter_combo.setFixedHeight(22)
        self.adapter_combo.setFixedWidth(150)
        self._refresh_adapter_combo()
        self.adapter_combo.activated.connect(self._on_adapter_picked)
        tb.addWidget(self.adapter_combo)
        self._act_connect = act("Connect", self._toggle_connection, "")
        self._update_connect_action()
        tb.addSeparator()

        # ── what is running ───────────────────────────────────────────────────
        # Pushed to the right: these report state and are toggled occasionally,
        # unlike the verbs on the left.
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background:transparent")
        tb.addWidget(spacer)
        tb.addSeparator()

        # The transmit gate is the one control that can put frames on a wire,
        # so armed is red and unmissable rather than another word in a row.
        self._act_arm = QAction("ARM TX", self)
        self._act_arm.setCheckable(True)
        self._act_arm.toggled.connect(self._toggle_arm)
        tb.addAction(self._act_arm)
        self._act_rest = act("REST", self._toggle_rest_api, "")
        self._act_mcp = act("MCP", self._toggle_mcp, "")
        _, self._plugins_menu = menu_button("Plugins", "Plugins found in ~/.canlab/plugins", [])
        self._plugins_menu.aboutToShow.connect(self._fill_plugins_menu)

        self._set_pill(self._act_arm, False, "ARM TX", COLORS["error"],
                       "Bus transmit is disarmed: nothing can be sent. Click to arm (Ctrl+E).")
        self._set_pill(self._act_rest, False, "REST", COLORS["green"],
                       "REST API server is stopped. Click to start it.")
        self._set_pill(self._act_mcp, False, "MCP", COLORS["green"],
                       "MCP server is stopped. Click to let an assistant "
                       "(Claude, ChatGPT, Codex) work on this capture.")

    def _set_pill(self, action, on: bool, text: str, color: str, tip: str) -> None:
        """Show a running/stopped state as a coloured dot and a tinted button."""
        action.setText(text)
        action.setToolTip(tip)
        action.setIcon(dot_icon(color if on else COLORS["dim"]))
        btn = self._toolbar.widgetForAction(action)
        if btn is None:
            return
        if not on:
            btn.setStyleSheet("")
            return
        c = QColor(color)
        rgb = f"{c.red()},{c.green()},{c.blue()}"
        btn.setStyleSheet(
            f"QToolButton {{ color:{color}; background:rgba({rgb},30);"
            f" border:1px solid rgba({rgb},120); border-radius:3px; padding:2px 6px; }}"
            f"QToolButton:hover {{ background:rgba({rgb},55); color:{color}; }}")

    def _update_connect_action(self) -> None:
        on = bool(self._state.is_connected)
        self._act_connect.setText("Disconnect" if on else "Connect")
        self._act_connect.setIcon(dot_icon(COLORS["green"] if on else COLORS["dim"]))
        self._act_connect.setToolTip(
            "Recording from the bus. Click to disconnect (Ctrl+D)." if on
            else "Open the selected adapter and start recording (Ctrl+D).")
        # Switching adapters mid-capture would not take effect until the next
        # connect, so the picker is closed rather than silently ignored.
        self.adapter_combo.setEnabled(not on)
        self.adapter_combo.setToolTip(
            "Disconnect first to switch adapter." if on
            else "The adapter Connect opens. Manage them in Settings > CAN ADAPTERS.")

    def _fill_plugins_menu(self) -> None:
        from canlab.core.plugin_loader import discover_plugins
        menu = self._plugins_menu
        menu.clear()
        self._plugins = discover_plugins()
        if not self._plugins:
            menu.addAction("No plugins in ~/.canlab/plugins").setEnabled(False)
        for p in self._plugins:
            mark = "on " if p.get("enabled") else "off"
            err = f"   [{p.get('error')}]" if p.get("error") else ""
            menu.addAction(f"{mark}  {p['name']} v{p['version']}{err}").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Manage plugins…").triggered.connect(
            lambda: self._open_settings(tab="PLUGINS"))

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menubar(self):
        mb = self.menuBar()

        # File menu. Shortcuts use QKeySequence.StandardKey where one exists so
        # they follow the platform (Cmd on macOS, Ctrl elsewhere).
        file_menu = mb.addMenu("File")
        for text, slot, key in [
            ("Open Log…",           self._open_log,      QKeySequence.StandardKey.Open),
            ("Open .rlog…",         self._open_rlog,     None),
            ("Save Project…",       self._save_project,  QKeySequence.StandardKey.Save),
            ("Open Project…",       self._open_project,  "Ctrl+Shift+O"),
        ]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key is not None:
                a.setShortcut(key)
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
        a = QAction("Import DBC…", self)
        a.triggered.connect(self._import_dbc)
        file_menu.addAction(a)
        file_menu.addSeparator()
        self._recent_menu = file_menu.addMenu("Recent Logs")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        a = QAction("Quit", self)
        a.setShortcut(QKeySequence.StandardKey.Quit)
        a.triggered.connect(self.close)
        file_menu.addAction(a)

        self._build_view_menu(mb)

        # Tools menu
        tools_menu = mb.addMenu("Tools")
        for text, slot in [
            ("Run AI RE",                self._run_ai_re),
            ("Code Gen",                 self._generate_code),
            ("Auto-discover OBD-II PIDs…", self._obd_discover),
            ("ML Signal Intelligence…",  self._open_ml_intel),
            ("CAN Gateway…",             self._open_gateway),
            ("Match against opendbc…",   self._match_opendbc),
            ("Export decoded time-series…", self._export_timeseries),
            ("Detect multiplexed signals…", self._detect_mux),
            ("Calibrate signal from reference CSV…", self._calibrate_ref),
            ("Trim capture…",            self._trim_capture),
            ("MCP server: start / stop",  self._toggle_mcp),
            ("Connect an assistant over MCP…", self._open_mcp_settings),
        ]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            tools_menu.addAction(a)

        # Settings menu
        settings_menu = mb.addMenu("Settings")
        a = QAction("Preferences…", self)
        a.triggered.connect(self._open_settings)
        a.setShortcut(QKeySequence.StandardKey.Preferences)
        settings_menu.addAction(a)

    # ── Recent logs ───────────────────────────────────────────────────────────

    RECENT_KEY = "files/recent"
    RECENT_MAX = 8

    def _recent_paths(self) -> list:
        from PyQt6.QtCore import QSettings
        stored = QSettings("CanLab", "CanLab").value(self.RECENT_KEY, [])
        if isinstance(stored, str):          # a one-item list comes back bare
            stored = [stored]
        return [p for p in (stored or []) if isinstance(p, str)]

    def _remember_recent(self, path: str) -> None:
        """Most recent first, no duplicates, capped."""
        import os
        from PyQt6.QtCore import QSettings

        path = os.path.abspath(path)
        paths = [p for p in self._recent_paths() if os.path.abspath(p) != path]
        paths.insert(0, path)
        QSettings("CanLab", "CanLab").setValue(
            self.RECENT_KEY, paths[:self.RECENT_MAX])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        import os

        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        menu.clear()
        paths = self._recent_paths()
        if not paths:
            empty = menu.addAction("Nothing opened yet")
            empty.setEnabled(False)
            return
        for i, path in enumerate(paths, 1):
            # The file name is what identifies it; the directory is the tooltip.
            action = menu.addAction(f"&{i}  {os.path.basename(path)}")
            action.setToolTip(path)
            action.setEnabled(os.path.exists(path))
            action.triggered.connect(
                lambda _=False, p=path: self._load_log_file(p))
        menu.addSeparator()
        clear = menu.addAction("Clear List")
        clear.triggered.connect(self._clear_recent)

    def _clear_recent(self) -> None:
        from PyQt6.QtCore import QSettings
        QSettings("CanLab", "CanLab").remove(self.RECENT_KEY)
        self._rebuild_recent_menu()

    # ── Window geometry ───────────────────────────────────────────────────────

    GEOMETRY_KEY = "window/geometry"

    def _restore_geometry(self) -> None:
        """Reopen at the size and place the user left it, maximised first time."""
        from PyQt6.QtCore import QSettings
        saved = QSettings("CanLab", "CanLab").value(self.GEOMETRY_KEY)
        restored = False
        if saved is not None:
            try:
                restored = bool(self.restoreGeometry(saved))
            except (TypeError, ValueError):
                restored = False
        if restored:
            self.show()
        else:
            self.showMaximized()

    def _update_title(self) -> None:
        """Name the open capture in the title bar, so the taskbar says which."""
        if self._loaded_name:
            self.setWindowTitle(f"{self._loaded_name} - CanLab")
        else:
            self.setWindowTitle("CanLab")

    def _save_geometry(self) -> None:
        from PyQt6.QtCore import QSettings
        QSettings("CanLab", "CanLab").setValue(
            self.GEOMETRY_KEY, self.saveGeometry())

    def _build_view_menu(self, mb) -> None:
        """Navigation and bus shortcuts, in a menu so they can be discovered.

        The application had exactly one shortcut, Preferences. These are the
        ones a desktop tool is expected to have. They live in a menu rather
        than as bare key bindings because Qt then shows each key next to its
        item, which is the only way a user finds out they exist.
        """
        view_menu = mb.addMenu("View")

        # Alt rather than Ctrl for the tab numbers, so Ctrl+number stays free
        # inside the tables and text fields. Alt+0 is the tenth, as in browsers.
        for i in range(self.tabs.count()):
            label = self.tabs.tabText(i).replace("&", "&&")
            action = QAction(label, self)
            if i < 10:
                action.setShortcut(QKeySequence(f"Alt+{(i + 1) % 10}"))
            action.triggered.connect(
                lambda _=False, index=i: self.tabs.setCurrentIndex(index))
            view_menu.addAction(action)

        view_menu.addSeparator()
        for text, sequence, handler in [
            ("Next Tab",         "Ctrl+Tab",       lambda: self._step_tab(1)),
            ("Previous Tab",     "Ctrl+Shift+Tab", lambda: self._step_tab(-1)),
            ("Find in Frames",   QKeySequence.StandardKey.Find,
             self._focus_frame_filter),
        ]:
            action = QAction(text, self)
            action.setShortcut(sequence if isinstance(sequence, str)
                               else QKeySequence(sequence))
            action.triggered.connect(handler)
            view_menu.addAction(action)

        view_menu.addSeparator()
        for text, sequence, handler in [
            ("Undo Signal Edit", QKeySequence.StandardKey.Undo, self._undo_dbc),
            ("Redo Signal Edit", QKeySequence.StandardKey.Redo, self._redo_dbc),
        ]:
            action = QAction(text, self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(handler)
            view_menu.addAction(action)

        view_menu.addSeparator()
        for text, sequence, handler in [
            ("Next Workspace",     "Ctrl+PgDown", lambda: self.workspace_bar.step_workspace(1)),
            ("Previous Workspace", "Ctrl+PgUp",   lambda: self.workspace_bar.step_workspace(-1)),
            ("Connect / Disconnect Bus", "Ctrl+D", self._toggle_connection),
            ("Arm / Disarm TX",          "Ctrl+E", lambda: self._act_arm.trigger()),
        ]:
            action = QAction(text, self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(handler)
            view_menu.addAction(action)

    def _undo_dbc(self) -> None:
        if self._state.undo_dbc():
            self.statusBar().showMessage("Undid the last signal edit.", 2500)
        else:
            self.statusBar().showMessage("Nothing to undo.", 2500)

    def _redo_dbc(self) -> None:
        if self._state.redo_dbc():
            self.statusBar().showMessage("Redid the signal edit.", 2500)
        else:
            self.statusBar().showMessage("Nothing to redo.", 2500)

    def _step_tab(self, delta: int) -> None:
        count = self.tabs.count()
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % count)

    def _focus_frame_filter(self) -> None:
        """Ctrl+F goes to the frame filter, switching to FRAMES if needed."""
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).startswith("FRAMES"):
                self.tabs.setCurrentIndex(i)
                break
        line = getattr(self.frames_tab, "filter_id", None)
        if line is not None:
            line.setFocus()
            line.selectAll()

    def _toggle_connection(self) -> None:
        """Ctrl+D connects or disconnects the bus, whichever applies."""
        if self._state.is_connected:
            self._disconnect_can()
        else:
            self._connect_can()

    # ── Central layout ────────────────────────────────────────────────────────

    def _build_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        main_lay = QHBoxLayout(body)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        self.id_panel = IDPanel()
        main_lay.addWidget(self.id_panel)

        self.tabs = QTabWidget()
        # Fifteen tab labels in one row need 1162 px, which forced the whole
        # window to a 1662 px minimum: it would not fit a 1366x768 laptop at
        # all. Let the bar scroll and elide instead, so the window can shrink
        # to what a tab page actually needs.
        # Scroll buttons rather than eliding: a truncated "SIG..." is worse
        # than an arrow, and the bar only scrolls when there is genuinely no
        # room. Either way the bar stops dictating the window's minimum width.
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setUsesScrollButtons(True)

        # Core tabs (0–5)
        self.frames_tab  = FramesTab()
        self.sniffer_tab = SnifferTab()
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
        self.tabs.addTab(self.sniffer_tab,      "SNIFFER")
        self.tabs.addTab(self.signals_tab,      "SIGNALS")
        self.tabs.addTab(self.plot_tab,         "PLOT")
        self.tabs.addTab(self.ai_tab,           "AI ENGINE")
        self.tabs.addTab(self.dbc_tab,          "DBC BUILDER")
        self.tabs.addTab(self.codegen_tab,      "CODE GEN")
        self.tabs.addTab(self.intelligence_tab, "INTELLIGENCE")
        self.tabs.addTab(self.injection_tab,    "INJECTION")
        self.tabs.addTab(self.diagnostics_tab,  "DIAGNOSTICS")
        self.tabs.addTab(self.dashboard_tab,    "DASHBOARD")
        self.tabs.addTab(self.auto_re_tab,      "AUTO-RE")
        self.tabs.addTab(self.timeline_tab,     "TIMELINE")
        self.tabs.addTab(self.obd_tab,          "OBD-II")
        self.tabs.addTab(self.ml_intel_tab,     "ML INTEL")
        self.tabs.addTab(self.gateway_tab,      "GATEWAY")

        main_lay.addWidget(self.tabs, stretch=1)

        self.inspector = InspectorPanel()
        main_lay.addWidget(self.inspector)

        # The workspace bar replaces the tab bar rather than sitting beside
        # it. Hide the bar only now that every page exists, because addTab
        # shows it again. Everything else about the tab widget is unchanged:
        # count(), tabText(), setCurrentIndex() and currentChanged do not care
        # whether the bar is visible, which is what lets the shortcuts, the
        # View menu, the acceptance scripts and both demo recorders keep
        # driving it exactly as before.
        self.workspace_bar = WorkspaceBar(self.tabs, self)
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.workspace_bar)
        outer.addWidget(body, stretch=1)

        # Connect last: the first addTab already emitted currentChanged while
        # the bar did not exist yet, so sync once explicitly afterwards.
        self.tabs.currentChanged.connect(self.workspace_bar.select_index)
        self.workspace_bar.select_index(self.tabs.currentIndex(), instant=True)

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
        self._state.bus_load_update.connect(self._on_bus_load_update)
        self.id_panel.analyze_requested.connect(self._analyze_id)
        self.id_panel.plot_requested.connect(self._plot_id)
        self.inspector.send_to_ai.connect(self._analyze_id)

    # ── File actions ──────────────────────────────────────────────────────────

    def _open_log(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CAN Log", "",
            "CAN Logs (*.csv *.log *.pcap *.pcapng *.blf *.asc *.mf4 *.mdf);;"
            "Vector BLF (*.blf);;Vector ASC (*.asc);;MDF4 (*.mf4 *.mdf);;"
            "pcap (*.pcap *.pcapng);;All Files (*)"
        )
        if path:
            self._load_log_file(path)

    def _load_log_file(self, path: str):
        try:
            df = parse_log_file(path)
            self._remember_recent(path)
            self._loaded_name = os.path.basename(path)
            self._update_title()
            if df.empty:
                QMessageBox.warning(self, "Empty", "No frames found in file.")
                return
            self._state.load_frames(df, os.path.basename(path))
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", str(e))

    def _import_dbc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import DBC", "", "DBC (*.dbc);;All Files (*)"
        )
        if path:
            self._load_dbc_file(path)

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
        from canlab.core.project import save_project
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "project.canlab", "CANLAB Project (*.canlab)"
        )
        if not path:
            return
        try:
            save_project(self._state, path)
            self.statusBar().showMessage(f"Project saved: {path}", 4000)
            self._loaded_name = os.path.basename(path)
            self._update_title()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _open_project(self):
        from canlab.core.project import load_project
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "CANLAB Project (*.canlab)"
        )
        if not path:
            return
        try:
            load_project(self._state, path)
            self.statusBar().showMessage(f"Project loaded: {path}", 4000)
            self._loaded_name = os.path.basename(path)
            self._update_title()
            self.lbl_total_frames.animate_to(len(self._state.frames_df))
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

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
                from canlab.core.panda_backend import PandaBus, is_available
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

        try:
            bus = injected_bus or self._open_bus(iface, channel, bitrate,
                                                 fd, data_bitrate,
                                                 self._can_settings.get("extra"))
        except Exception as e:
            from canlab.core.adapters import Adapter, _HINTS
            text = f"{type(e).__name__}: {e}"
            hint = next((h for needle, h in _HINTS if needle.lower() in text.lower()), "")
            QMessageBox.critical(
                self, "CAN Error",
                f"Could not open {self._can_settings.get('name') or channel} "
                f"({Adapter.from_dict(self._can_settings).describe()}):\n{text}"
                + (f"\n\nHint: {hint}" if hint else "")
                + "\n\nSettings > CAN ADAPTERS can detect and test adapters.")
            return

        hubs = [self._make_hub(bus, channel, bitrate, 0)]
        # Extra buses configured in Settings each get their own hub and bus index.
        for i, cfg in enumerate(self._multibus_config, start=1):
            name = cfg.get("name") or cfg.get("channel", "?")
            try:
                extra = self._open_bus(cfg.get("interface", "socketcan"),
                                       cfg.get("channel", "can0"),
                                       int(cfg.get("bitrate", 500000)))
            except Exception as e:
                self.statusBar().showMessage(f"Bus {name}: {e}", 5000)
                continue
            hubs.append(self._make_hub(extra, name,
                                       int(cfg.get("bitrate", 500000)), i))

        self._hubs = hubs
        for hub in self._hubs:
            hub.start()
        self._state.bus_hub      = self._hubs[0]
        self._state.can_bus      = self._hubs[0]   # gated .send for REST /inject
        self._state.is_connected = True
        self._drain_timer.start()

        self._update_connect_action()
        self._state.can_connected.emit(True)

    # ── Adapters ──────────────────────────────────────────────────────────────

    def _refresh_adapter_combo(self):
        """Saved adapters from Settings; the current one selected."""
        from canlab.core.adapters import adapters_from_json
        from canlab.settings_dialog import SettingsDialog, settings
        st = settings()
        self._adapters = adapters_from_json(st.value(SettingsDialog.S_ADAPTERS, "[]", str))
        combo = self.adapter_combo
        combo.blockSignals(True)
        combo.clear()
        for a in self._adapters:
            # The name alone in the box, the whole configuration on hover:
            # the full string was being cut off mid-word in the toolbar.
            combo.addItem(f"{a.name} · {a.channel}", a.name)
            combo.setItemData(combo.count() - 1, a.describe(), Qt.ItemDataRole.ToolTipRole)
        if not self._adapters:
            combo.addItem(f"{self._can_settings.get('interface')} "
                          f"{self._can_settings.get('channel')}", "")
        combo.addItem("Manage adapters…", "__manage__")
        idx = combo.findData(self._can_settings.get("name", ""))
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _on_adapter_picked(self, index: int):
        key = self.adapter_combo.itemData(index)
        if key == "__manage__":
            self._refresh_adapter_combo()          # back to the current one
            self._open_settings(tab="CAN ADAPTERS")
            return
        for a in self._adapters:
            if a.name == key:
                self._can_settings = {"interface": a.interface, "channel": a.channel,
                                      "bitrate": a.bitrate, "fd": a.fd,
                                      "data_bitrate": a.data_bitrate,
                                      "extra": dict(a.extra), "name": a.name}
                from canlab.settings_dialog import SettingsDialog, settings
                st = settings()
                st.setValue(SettingsDialog.S_ADAPTER_DEFAULT, a.name)
                st.setValue(SettingsDialog.S_INTERFACE, a.interface)
                st.setValue(SettingsDialog.S_CHANNEL, a.channel)
                st.setValue(SettingsDialog.S_BITRATE, str(a.bitrate))
                st.setValue(SettingsDialog.S_FD, a.fd)
                st.setValue(SettingsDialog.S_FD_BITRATE, str(a.data_bitrate))
                import json
                st.setValue(SettingsDialog.S_EXTRA, json.dumps(a.extra))
                self.statusBar().showMessage(f"Adapter: {a.name} ({a.describe()})", 4000)
                return

    def _open_bus(self, interface: str, channel: str, bitrate: int,
                  fd: bool = False, data_bitrate=None, extra: dict | None = None):
        from canlab.core.adapters import Adapter
        adapter = Adapter(name=channel, interface=interface, channel=str(channel),
                          bitrate=int(bitrate), fd=bool(fd),
                          data_bitrate=int(data_bitrate or 2_000_000), extra=dict(extra or {}))
        return can.interface.Bus(**adapter.bus_kwargs())

    def _make_hub(self, bus, name: str, bitrate: int, index: int):
        from canlab.core.bus_hub import BusHub
        return BusHub(
            bus, name=name, bus_index=index, bitrate=bitrate,
            on_error=self.live_error.emit,
            on_load=self._state.bus_load_update.emit,
            on_trigger=lambda rule, msg: self._state.trigger_fired.emit(rule, msg),
            triggers_getter=lambda: self._state.triggers,
        )

    def _drain_live_frames(self):
        rows = []
        for hub in self._hubs:
            rows.extend(hub.drain())
        if rows:
            self._live_frame_count += len(rows)
            self._state.append_rows(rows)

    def _disconnect_can(self):
        self._drain_timer.stop()
        self._drain_live_frames()       # flush the tail so no frames are lost
        self._state.drop_bus_views()
        for hub in self._hubs:
            hub.shutdown()
        self._hubs = []
        self._state.bus_hub      = None
        self._state.can_bus      = None
        self._state.is_connected = False
        self._update_connect_action()
        self._state.can_connected.emit(False)

    def _on_live_error(self, err: str):
        QMessageBox.critical(self, "CAN Error", err)
        self._disconnect_can()

    # ── REST API ──────────────────────────────────────────────────────────────

    def _match_opendbc(self):
        if self._state.frames_df.empty:
            QMessageBox.information(self, "opendbc", "Load a capture first.")
            return
        ids = set(self._state.frames_df["ID"].unique().tolist())
        self.statusBar().showMessage("Matching against opendbc (fetching index)…")
        from canlab.ui.compute_worker import ComputeWorker

        def _work():
            from canlab.core.opendbc_matcher import refresh_index, match_capture
            refresh_index()                      # fetch+cache (network, best-effort)
            return match_capture(ids, top_k=8)

        self._opendbc_worker = ComputeWorker(_work)
        self._opendbc_worker.done.connect(self._on_opendbc_done)
        self._opendbc_worker.failed.connect(
            lambda e: QMessageBox.warning(self, "opendbc", f"Match failed: {e}"))
        self._opendbc_worker.start()

    def _on_opendbc_done(self, matches):
        self.statusBar().clearMessage()
        if not matches:
            QMessageBox.information(
                self, "opendbc",
                "No matches (index empty or no overlap). Requires network access "
                "to fetch the opendbc library on first run.")
            return
        from canlab.ui.opendbc_dialog import OpendbcMatchDialog
        dlg = OpendbcMatchDialog(matches, self)
        if dlg.exec() and dlg.chosen:
            self._apply_opendbc(dlg.chosen, only_seen=dlg.only_seen)

    def _apply_opendbc(self, dbc_name: str, only_seen: bool = True) -> None:
        """Load a matched opendbc database straight into the signal list.

        The match already said this DBC explains the capture; making the user
        go and find the file by hand was the missing step. Signals for messages
        not on this bus are skipped by default, since an OEM database carries
        hundreds of them and they would only clutter the builder.
        """
        from canlab.core.canid import normalize_id
        from canlab.core.dbc_manager import load_dbc
        from canlab.core.opendbc_matcher import CACHE_DIR
        path = CACHE_DIR / dbc_name
        if not path.exists():
            QMessageBox.warning(self, "opendbc", f"{dbc_name} is not in the cache.")
            return
        try:
            incoming = load_dbc(str(path))
        except Exception as exc:
            QMessageBox.critical(self, "opendbc", f"Could not read {dbc_name}:\n{exc}")
            return
        seen = {normalize_id(i) for i in self._state.frames_df["ID"].unique()} \
            if only_seen and not self._state.frames_df.empty else None
        have = {(normalize_id(s.get("message_id", "")), s.get("signal_name"))
                for s in self._state.dbc_signals}
        added, skipped = [], 0
        for sig in incoming:
            mid = normalize_id(sig.get("message_id", ""))
            if seen is not None and mid not in seen:
                skipped += 1
                continue
            if (mid, sig.get("signal_name")) in have:
                continue
            added.append(sig)
        if not added:
            QMessageBox.information(self, "opendbc",
                                    "Nothing new to add from that database.")
            return
        self._state.replace_dbc_signals(list(self._state.dbc_signals) + added)
        msgs = len({s["message_id"] for s in added})
        self.statusBar().showMessage(
            f"opendbc: applied {len(added)} signals across {msgs} messages from "
            f"{dbc_name}" + (f" ({skipped} for messages not on this bus skipped)"
                             if skipped else ""), 8000)

    def _export_timeseries(self):
        if self._state.frames_df.empty or not self._state.dbc_signals:
            QMessageBox.information(
                self, "Export", "Need a loaded capture and DBC signals first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export decoded time-series", "signals.csv",
            "CSV (*.csv);;Parquet (*.parquet)")
        if not path:
            return
        try:
            from canlab.core.timeseries_export import export_timeseries
            n = export_timeseries(self._state.frames_df, self._state.dbc_signals, path)
            QMessageBox.information(self, "Export", f"Wrote {n} rows to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Export", str(e))

    def _detect_mux(self):
        if self._state.frames_df.empty:
            QMessageBox.information(self, "Multiplexers", "Load a capture first.")
            return
        from canlab.core.mux_detector import detect_all_multiplexers
        res = detect_all_multiplexers(self._state.frames_df)
        if not res:
            QMessageBox.information(self, "Multiplexers",
                                    "No multiplexed messages detected.")
            return
        lines = []
        for can_id, r in sorted(res.items()):
            modes = ", ".join(f"{v}:{r['modes'][v]}" for v in sorted(r["modes"]))
            lines.append(f"0x{can_id}  selector=B{r['mux_byte']}  "
                         f"score={r['score']}\n    modes {modes}")
        QMessageBox.information(self, "Multiplexed signals", "\n".join(lines))

    def _calibrate_ref(self):
        if self._state.frames_df.empty:
            QMessageBox.information(self, "Calibrate", "Load a capture first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Reference CSV (columns: timestamp,value)", "",
            "CSV (*.csv);;All Files (*)")
        if not path:
            return
        try:
            ref = pd.read_csv(path)
            cols = [c.lower() for c in ref.columns]
            tcol = ref.columns[cols.index("timestamp")] if "timestamp" in cols else ref.columns[0]
            vcol = ref.columns[cols.index("value")] if "value" in cols else ref.columns[1]
            from canlab.core.reference_calibrate import calibrate_against_reference
            cand = calibrate_against_reference(
                self._state.frames_df,
                ref[tcol].to_numpy(), ref[vcol].to_numpy(), top_k=8)
        except Exception as e:
            QMessageBox.warning(self, "Calibrate", f"Failed: {e}")
            return
        if not cand:
            QMessageBox.information(self, "Calibrate", "No candidate signal found.")
            return
        lines = [f"[{c['verdict']}] 0x{c['id']} bit{c['start_bit']} len{c['length']} "
                 f"{c['byte_order']}  scale={c['scale']} offset={c['offset']}  "
                 f"R2={c['r2']} (n={c['n']})" for c in cand]
        QMessageBox.information(self, "Reference calibration (ranked)",
                                "\n".join(lines))

    def _toggle_arm(self, checked: bool):
        from canlab.core.safety import set_armed
        if checked:
            ok = QMessageBox.warning(
                self, "Arm Bus Transmit",
                "Arming lets CanLab transmit on the connected bus — injection, "
                "replay, fuzzing, gateway forwarding AND every diagnostic request "
                "(UDS, OBD-II, XCP, DoIP). Nothing is sent while disarmed.\n\n"
                "Only arm on an isolated bench setup — never on a vehicle you are "
                "driving. Arm transmit now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                self._act_arm.setChecked(False)   # reverts text via toggled
                return
        set_armed(checked)
        self._set_pill(self._act_arm, checked, "ARM TX", COLORS["error"],
                       "Bus transmit is ARMED: every send is live. Click to disarm (Ctrl+E)."
                       if checked else
                       "Bus transmit is disarmed: nothing can be sent. Click to arm (Ctrl+E).")
        self.statusBar().showMessage(
            "Bus transmit ARMED — every send is live" if checked
            else "Bus transmit disarmed — nothing can be sent", 3000
        )

    def _toggle_rest_api(self):
        if not self._state.rest_api_running:
            self._start_rest_api()
        else:
            self._stop_rest_api()

    def _start_rest_api(self):
        from canlab.core.rest_api import RestAPIServer
        try:
            self._rest_api_server = RestAPIServer(
                state_getter=get_state,
                port=self._state.rest_api_port,
            )
            self._rest_api_server.start()
            self._state.rest_api_running = True
            self._set_pill(self._act_rest, True, f"REST :{self._state.rest_api_port}",
                           COLORS["green"],
                           f"REST API running on 127.0.0.1:{self._state.rest_api_port}. "
                           "Click to stop it.")
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

    def _trim_capture(self):
        """Cut the loaded capture down to a window, a set of IDs or one bus."""
        from canlab.ui.trim_dialog import TrimDialog
        df = self._state.frames_snapshot()
        if df is None or df.empty:
            QMessageBox.information(self, "Trim capture", "Open a capture first.")
            return
        dlg = TrimDialog(df, self)
        if not dlg.exec():
            return
        kept = dlg.result_frames
        if kept is None or kept.empty:
            return
        if dlg.replace_loaded:
            self._state.load_frames(kept, f"{self._loaded_name or 'capture'} (trimmed)")
            self.statusBar().showMessage(
                f"Trimmed to {len(kept)} frames. Reopen the file to get the rest back.",
                6000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save trimmed capture", "", "SavvyCAN CSV (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        from canlab.cli import write_savvycan_csv
        write_savvycan_csv(kept, path)
        self.statusBar().showMessage(f"Wrote {len(kept)} frames to {path}", 5000)

    # ── MCP server ────────────────────────────────────────────────────────────

    def _toggle_mcp(self):
        if self._mcp_service is None:
            self._start_mcp()
        else:
            self._stop_mcp()

    def _start_mcp(self, quiet: bool = False):
        from canlab.settings_dialog import SettingsDialog, settings
        st = settings()
        cfg = {"port": int(st.value(SettingsDialog.S_MCP_PORT, 8766, int)),
               "allow_remote": st.value(SettingsDialog.S_MCP_REMOTE, False, bool),
               "token": st.value(SettingsDialog.S_MCP_TOKEN, "", str)}
        try:
            from canlab.core.mcp_service import AppBackend, McpService
            from canlab.core.mcp_tools import CanLabTools
            from canlab.ui.gui_invoke import GuiInvoker
        except ImportError as e:
            QMessageBox.warning(self, "MCP", f"The MCP SDK is not installed ({e}).\n\n"
                                "Install it with: pip install -e \".[mcp]\"")
            return
        if not hasattr(self, "_gui_invoker"):
            self._gui_invoker = GuiInvoker(self)
        tools = CanLabTools(AppBackend(self._state, self._gui_invoker))
        svc = McpService(tools, host="0.0.0.0" if cfg["allow_remote"] else "127.0.0.1",
                         port=cfg["port"], token=cfg["token"],
                         allow_remote=cfg["allow_remote"])
        try:
            svc.start()
        except Exception as e:
            if quiet:
                self.statusBar().showMessage(f"MCP server did not start: {e}", 8000)
            else:
                QMessageBox.critical(self, "MCP", f"Could not start the MCP server on port "
                                     f"{cfg['port']}:\n{e}")
            return
        self._mcp_service = svc
        self._set_pill(self._act_mcp, True, f"MCP :{cfg['port']}", COLORS["green"],
                       f"MCP server running at {svc.url}. Settings > MCP shows what to "
                       "paste into Claude Code, Claude Desktop, Codex or ChatGPT.")
        self.statusBar().showMessage(
            f"MCP server at {svc.url}. Settings > MCP shows what to paste into "
            "Claude Code, Claude Desktop, Codex or ChatGPT.", 8000)

    def _stop_mcp(self):
        if self._mcp_service is not None:
            self._mcp_service.stop()
            self._mcp_service = None
        self._set_pill(self._act_mcp, False, "MCP", COLORS["green"],
                       "MCP server is stopped. Click to let an assistant "
                       "(Claude, ChatGPT, Codex) work on this capture.")

    def _open_mcp_settings(self):
        self._open_settings(tab="MCP")

    def _stop_rest_api(self):
        if self._rest_api_server:
            self._rest_api_server.stop()
            self._rest_api_server = None
        self._state.rest_api_running = False
        self._set_pill(self._act_rest, False, "REST", COLORS["green"],
                       "REST API server is stopped. Click to start it.")

    # ── Plugins ───────────────────────────────────────────────────────────────

    def _load_plugins(self):
        from canlab.core.plugin_loader import discover_plugins, activate_plugins
        self._plugins = discover_plugins()
        activated = activate_plugins(self._plugins, self)
        if activated:
            self.statusBar().showMessage(
                f"Plugins loaded: {', '.join(activated)}", 5000
            )

    # ── Bus load status bar ───────────────────────────────────────────────────

    def _on_bus_load_update(self, load: float):
        self.load_bar.setValue(int(load * 100))

    # ── Toolbar actions ───────────────────────────────────────────────────────

    def _goto(self, tab) -> None:
        """Show a tab by identity rather than by number.

        These were nine hardcoded indices, and inserting SNIFFER at position 1
        shifted every tab after it without moving them: Tools > CAN Gateway
        opened ML INTEL, Export DBC opened AI ENGINE, and the ID panel's
        Analyze with AI opened PLOT. The stale comments (`# OBD-II tab` beside
        index 12, which is TIMELINE) are what gave it away. indexOf cannot go
        stale.
        """
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setCurrentIndex(index)

    def _run_ai_re(self):
        self._goto(self.ai_tab)
        self.ai_tab._add_all_unknown()
        self.ai_tab._run_queue()

    def _export_dbc(self):
        self._goto(self.dbc_tab)
        self.dbc_tab._export_dbc()

    def _generate_code(self):
        self._goto(self.codegen_tab)

    def _obd_discover(self):
        self._goto(self.obd_tab)
        self.obd_tab._discover_pids()

    def _open_ml_intel(self):
        self._goto(self.ml_intel_tab)

    def _open_gateway(self):
        self._goto(self.gateway_tab)

    def _open_settings(self, tab: str = ""):
        dlg = SettingsDialog(self)
        if tab:
            dlg.show_tab(tab)
        if dlg.exec():
            self._api_key      = dlg.get_api_key()
            self._can_settings = dlg.get_can_settings()
            self._state.rest_api_port = dlg.get_rest_api_port()
            self.ai_tab.set_api_key(self._api_key)
            self.ai_tab.set_ai_config(
                provider=dlg.get_ai_provider(),
                model=dlg.get_ai_model(),
                groq_key=dlg.get_groq_key(),
                api_key=self._api_key,
                openai_key=dlg.get_openai_key(),
            )
            self._multibus_config = dlg.get_multibus_config()
            self._refresh_adapter_combo()
            if self._mcp_service is not None:
                # The port or token may have changed; the assistant reconnects.
                self._stop_mcp()
                self._start_mcp(quiet=True)

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
            from canlab.core.dbc_manager import export_opendbc
            from canlab.core.vehicle_profile import active_profile, message_meta
            ids = {s.get("message_id", "") for s in self._state.dbc_signals}
            meta = message_meta(active_profile(self._state), ids)
            dbc_str = export_opendbc(self._state.dbc_signals, meta)
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
            from canlab.core.lua_exporter import signals_to_lua_dissector
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
            from canlab.core.can_matrix_parser import parse_can_matrix
            from canlab.core.dbc_manager import get_db
            sigs = parse_can_matrix(path)
            for sig in sigs:
                self._state.add_dbc_signal(sig)
            get_db(self._state)
            self.statusBar().showMessage(
                f"CAN Matrix: imported {len(sigs)} signals from {os.path.basename(path)}", 5000
            )
            self._goto(self.dbc_tab)
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    def _analyze_id(self, hex_id: str):
        self._goto(self.ai_tab)
        self.ai_tab.queue_id(hex_id)
        self.ai_tab._load_id(hex_id)

    def _plot_id(self, hex_id: str):
        self._goto(self.plot_tab)
        self.plot_tab._highlight_id(hex_id)

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_id_selected(self, hex_id: str):
        self.lbl_selected_id.setText(f"ID: 0x{hex_id}")

    def _on_frames_loaded(self, count: int):
        self.lbl_total_frames.animate_to(count)

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
        self._save_geometry()
        from canlab.core.safety import set_armed
        set_armed(False)                       # stops every registered TX worker
        self._stop_rest_api()
        self._stop_mcp()
        if self._hubs:
            self._disconnect_can()
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if hasattr(tab, "cleanup"):
                try:
                    tab.cleanup()
                except Exception:
                    pass
        event.accept()


def _saved_can_settings() -> dict:
    """The CAN interface the user last chose (these used to reset every launch)."""
    from canlab.settings_dialog import SettingsDialog, settings
    st = settings()
    return {
        "interface": st.value(SettingsDialog.S_INTERFACE, "socketcan", str),
        "channel": st.value(SettingsDialog.S_CHANNEL, "can0", str),
        "bitrate": int(st.value(SettingsDialog.S_BITRATE, 500000, int)),
        "fd": st.value(SettingsDialog.S_FD, False, bool),
        "data_bitrate": int(st.value(SettingsDialog.S_FD_BITRATE, 2000000, int)),
        "extra": _json_dict(st.value(SettingsDialog.S_EXTRA, "{}", str)),
        "name": st.value(SettingsDialog.S_ADAPTER_DEFAULT, "", str),
    }


def _json_dict(text) -> dict:
    import json
    try:
        d = json.loads(text or "{}")
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _saved_multibus() -> list:
    import json
    from canlab.settings_dialog import SettingsDialog, settings
    try:
        return json.loads(settings().value(SettingsDialog.S_MULTIBUS, "[]", str))
    except (ValueError, TypeError):
        return []


def _sep() -> QLabel:
    lbl = QLabel("|")
    lbl.setStyleSheet(f"color:{COLORS['border']}; padding:0 4px;")
    return lbl
