import json
import logging

import keyring
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QGroupBox, QGridLayout, QSpinBox, QListWidget, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton, QButtonGroup,
)
from canlab.theme import mono_font

log = logging.getLogger(__name__)

KEYRING_SERVICE    = "canlab"
KEYRING_API_KEY    = "anthropic_api_key"
KEYRING_GROQ_KEY   = "groq_api_key"
KEYRING_OPENAI_KEY = "openai_api_key"
KEYRING_AI_PROVIDER = "ai_provider"
KEYRING_AI_MODEL    = "ai_model"

# Provider → available models
AI_MODELS = {
    "Anthropic": [
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-haiku-4-5",
    ],
    "OpenAI": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-4.1",
    ],
    "Groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ],
    "Ollama": [
        "llama3.1",
        "llama3.2",
        "qwen2.5",
        "mistral",
        "gemma2",
    ],
}


def settings() -> QSettings:
    """Everything that is not a secret lives here, so it survives a restart.

    Only API keys go to the OS keyring; interface, bitrate, REST port, vehicle
    profile and the plugin allow-list used to be held in memory and were lost
    on every exit.
    """
    return QSettings("CanLab", "CanLab")


def _keyring_set(key: str, value: str) -> bool:
    """Store a secret, tolerating a missing or locked keyring backend."""
    try:
        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception:
        log.warning("keyring unavailable; %s kept for this session only", key,
                    exc_info=True)
        return False


def save_api_key(key: str):
    _keyring_set(KEYRING_API_KEY, key)


def load_api_key() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_API_KEY) or ""
    except Exception:
        return ""


def save_groq_key(key: str):
    _keyring_set(KEYRING_GROQ_KEY, key)


def load_groq_key() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_GROQ_KEY) or ""
    except Exception:
        return ""


def save_openai_key(key: str):
    _keyring_set(KEYRING_OPENAI_KEY, key)


def load_openai_key() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_OPENAI_KEY) or ""
    except Exception:
        return ""


def save_ai_provider(provider: str):
    _keyring_set(KEYRING_AI_PROVIDER, provider)


def load_ai_provider() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_AI_PROVIDER) or "Anthropic"
    except Exception:
        return "Anthropic"


def save_ai_model(model: str):
    _keyring_set(KEYRING_AI_MODEL, model)


def load_ai_model() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_AI_MODEL) or "claude-sonnet-5"
    except Exception:
        return "claude-sonnet-5"


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 480)
        self._build_ui()
        self._load_values()

    def show_tab(self, title: str) -> None:
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == title:
                self._tabs.setCurrentIndex(i)
                return

    def _build_ui(self):
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        self._tabs = tabs

        # ── API Keys ──────────────────────────────────────────────────────────
        api_tab = QWidget()
        api_lay = QVBoxLayout(api_tab)
        api_lay.setSpacing(8)

        # Anthropic
        api_grp = QGroupBox("Anthropic API")
        api_g_lay = QGridLayout(api_grp)
        api_g_lay.addWidget(QLabel("API Key:"), 0, 0)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-ant-…")
        api_g_lay.addWidget(self.api_key_edit, 0, 1)
        btn_show = QPushButton("Show")
        btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda v: self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if v else QLineEdit.EchoMode.Password
        ))
        api_g_lay.addWidget(btn_show, 0, 2)
        api_lay.addWidget(api_grp)

        # Groq
        groq_grp = QGroupBox("Groq API")
        groq_g_lay = QGridLayout(groq_grp)
        groq_g_lay.addWidget(QLabel("API Key:"), 0, 0)
        self.groq_key_edit = QLineEdit()
        self.groq_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_key_edit.setPlaceholderText("gsk_…")
        groq_g_lay.addWidget(self.groq_key_edit, 0, 1)
        btn_show_groq = QPushButton("Show")
        btn_show_groq.setCheckable(True)
        btn_show_groq.toggled.connect(lambda v: self.groq_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if v else QLineEdit.EchoMode.Password
        ))
        groq_g_lay.addWidget(btn_show_groq, 0, 2)
        hint_groq = QLabel("Get your key at console.groq.com")
        hint_groq.setFont(mono_font(8))
        hint_groq.setObjectName("label_dim")
        groq_g_lay.addWidget(hint_groq, 1, 0, 1, 3)
        api_lay.addWidget(groq_grp)

        # OpenAI (the ChatGPT models)
        oai_grp = QGroupBox("OpenAI API")
        oai_g_lay = QGridLayout(oai_grp)
        oai_g_lay.addWidget(QLabel("API Key:"), 0, 0)
        self.openai_key_edit = QLineEdit()
        self.openai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_edit.setPlaceholderText("sk-…")
        oai_g_lay.addWidget(self.openai_key_edit, 0, 1)
        btn_show_oai = QPushButton("Show")
        btn_show_oai.setCheckable(True)
        btn_show_oai.toggled.connect(lambda v: self.openai_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if v else QLineEdit.EchoMode.Password))
        btn_show_oai.setFixedWidth(60)
        oai_g_lay.addWidget(btn_show_oai, 0, 2)
        hint_oai = QLabel("Get your key at platform.openai.com. Any model id can be typed below.")
        hint_oai.setFont(mono_font(8))
        hint_oai.setObjectName("label_dim")
        oai_g_lay.addWidget(hint_oai, 1, 0, 1, 3)
        api_lay.addWidget(oai_grp)

        # Active provider + model
        model_grp = QGroupBox("Active AI Provider")
        model_g_lay = QGridLayout(model_grp)
        model_g_lay.addWidget(QLabel("Provider:"), 0, 0)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(list(AI_MODELS.keys()))
        self.provider_combo.setFont(mono_font(9))
        model_g_lay.addWidget(self.provider_combo, 0, 1)
        model_g_lay.addWidget(QLabel("Model:"), 1, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)      # newer model ids than the list knows
        self.model_combo.setFont(mono_font(9))
        model_g_lay.addWidget(self.model_combo, 1, 1)
        hint_model = QLabel(
            "Defaults: Anthropic claude-sonnet-5, OpenAI gpt-5, Groq llama-3.3-70b-versatile"
        )
        hint_model.setFont(mono_font(7))
        hint_model.setObjectName("label_dim")
        hint_model.setWordWrap(True)
        model_g_lay.addWidget(hint_model, 2, 0, 1, 2)
        api_lay.addWidget(model_grp)

        # Wire provider → model list update
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        self._on_provider_changed(self.provider_combo.currentText())

        api_lay.addStretch()
        tabs.addTab(api_tab, "API KEYS")

        # ── CAN adapters ──────────────────────────────────────────────────────
        can_tab = QWidget()
        can_lay = QVBoxLayout(can_tab)
        can_lay.addWidget(QLabel(
            "Hardware adapters the application can connect to. The default one is "
            "what Connect CAN opens; the toolbar switches between them. Test opens "
            "the adapter and listens for a second, it never transmits.",
            font=mono_font(8), wordWrap=True))
        self.adapter_table = QTableWidget(0, 5)
        self.adapter_table.setHorizontalHeaderLabels(["Name", "Backend", "Channel", "Bitrate", "Default"])
        self.adapter_table.setFont(mono_font())
        self.adapter_table.verticalHeader().setDefaultSectionSize(22)
        self.adapter_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.adapter_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.adapter_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.adapter_table.doubleClicked.connect(lambda *_: self._adapter_edit())
        can_lay.addWidget(self.adapter_table)
        row1 = QHBoxLayout()
        for text, slot in (("Detect connected…", self._adapter_detect),
                           ("Add…", self._adapter_add), ("Edit…", self._adapter_edit),
                           ("Remove", self._adapter_remove), ("Test", self._adapter_test),
                           ("Set as default", self._adapter_set_default)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row1.addWidget(b)
        row1.addStretch()
        can_lay.addLayout(row1)
        self.lbl_adapter_test = QLabel("")
        self.lbl_adapter_test.setFont(mono_font(8))
        self.lbl_adapter_test.setWordWrap(True)
        can_lay.addWidget(self.lbl_adapter_test)
        can_lay.addStretch()
        tabs.addTab(can_tab, "CAN ADAPTERS")
        self._adapters: list = []
        self._adapter_default: str = ""
        self._adapter_worker = None

        # ── Vehicle profile ───────────────────────────────────────────────────
        veh_tab = QWidget()
        veh_lay = QVBoxLayout(veh_tab)
        veh_grp = QGroupBox("Vehicle Profile")
        veh_g = QGridLayout(veh_grp)
        veh_g.addWidget(QLabel("Profile:"), 0, 0)
        self.profile_combo = QComboBox()
        from canlab.core.vehicle_profile import list_profiles
        for pid, pname in list_profiles():
            self.profile_combo.addItem(pname, pid)
        veh_g.addWidget(self.profile_combo, 0, 1)
        hint_veh = QLabel(
            "Sets where injected frames carry their rolling counter and checksum, "
            "which algorithm computes the checksum, the metadata attached to an "
            "openpilot export, and the framing hint given to the AI.\n\n"
            "'Generic' assumes nothing: no counter and no checksum are added. "
            "A profile describes framing only — it never identifies your vehicle."
        )
        hint_veh.setFont(mono_font(8))
        hint_veh.setObjectName("label_dim")
        hint_veh.setWordWrap(True)
        veh_g.addWidget(hint_veh, 1, 0, 1, 2)
        veh_lay.addWidget(veh_grp)
        veh_lay.addStretch()
        tabs.addTab(veh_tab, "VEHICLE")

        # ── REST API ──────────────────────────────────────────────────────────
        rest_tab = QWidget()
        rest_lay = QVBoxLayout(rest_tab)
        rest_grp = QGroupBox("REST API Server")
        rest_g_lay = QGridLayout(rest_grp)
        rest_g_lay.addWidget(QLabel("Port:"), 0, 0)
        self.rest_port_spin = QSpinBox()
        self.rest_port_spin.setRange(1024, 65535)
        self.rest_port_spin.setValue(8765)
        rest_g_lay.addWidget(self.rest_port_spin, 0, 1)
        rest_g_lay.addWidget(QLabel("Bind:"), 1, 0)
        self.rest_host_edit = QLineEdit("127.0.0.1")
        rest_g_lay.addWidget(self.rest_host_edit, 1, 1)
        hint_rest = QLabel(
            "Toggle REST API from the toolbar. Exposes /frames /signals /inject endpoints.\n"
            "Requires: pip install fastapi uvicorn"
        )
        hint_rest.setFont(mono_font(8))
        hint_rest.setObjectName("label_dim")
        hint_rest.setWordWrap(True)
        rest_g_lay.addWidget(hint_rest, 2, 0, 1, 2)
        rest_lay.addWidget(rest_grp)
        rest_lay.addStretch()
        tabs.addTab(rest_tab, "REST API")

        # ── MCP server (assistants: Claude, ChatGPT, Codex) ───────────────────
        mcp_tab = QWidget()
        mcp_lay = QVBoxLayout(mcp_tab)
        mcp_grp = QGroupBox("MCP Server (in the application)")
        mcp_g = QGridLayout(mcp_grp)
        mcp_g.addWidget(QLabel("Port:"), 0, 0)
        self.mcp_port_spin = QSpinBox()
        self.mcp_port_spin.setRange(1024, 65535)
        self.mcp_port_spin.setValue(8766)
        mcp_g.addWidget(self.mcp_port_spin, 0, 1)
        self.chk_mcp_autostart = QCheckBox("Start the MCP server when CanLab opens")
        mcp_g.addWidget(self.chk_mcp_autostart, 1, 0, 1, 3)
        self.chk_mcp_remote = QCheckBox("Allow connections from other machines "
                                        "(needed behind a tunnel, for ChatGPT)")
        mcp_g.addWidget(self.chk_mcp_remote, 2, 0, 1, 3)
        mcp_g.addWidget(QLabel("Bearer token:"), 3, 0)
        self.mcp_token_edit = QLineEdit()
        self.mcp_token_edit.setPlaceholderText("empty = no token (loopback only is still safe)")
        self.mcp_token_edit.setFont(mono_font(8))
        mcp_g.addWidget(self.mcp_token_edit, 3, 1)
        btn_tok = QPushButton("Generate")
        btn_tok.setFixedWidth(80)
        btn_tok.clicked.connect(self._mcp_generate_token)
        mcp_g.addWidget(btn_tok, 3, 2)
        hint_mcp = QLabel(
            "An assistant connected here works on the capture you have loaded or "
            "are recording, and signals it defines appear in the DBC Builder. "
            "No MCP tool transmits. Start and stop it from the MCP toolbar toggle."
        )
        hint_mcp.setFont(mono_font(8))
        hint_mcp.setObjectName("label_dim")
        hint_mcp.setWordWrap(True)
        mcp_g.addWidget(hint_mcp, 4, 0, 1, 3)
        mcp_lay.addWidget(mcp_grp)

        client_grp = QGroupBox("Connect an assistant")
        cg = QVBoxLayout(client_grp)
        self.mcp_client_combo = QComboBox()
        self.mcp_client_combo.addItem("Claude Code (command)", "claude_code")
        self.mcp_client_combo.addItem("Claude Desktop (claude_desktop_config.json)", "claude_desktop")
        self.mcp_client_combo.addItem("Codex CLI (~/.codex/config.toml)", "codex")
        self.mcp_client_combo.addItem("ChatGPT (connector)", "chatgpt")
        self.mcp_client_combo.setFont(mono_font(9))
        cg.addWidget(self.mcp_client_combo)
        from PyQt6.QtWidgets import QPlainTextEdit
        self.mcp_snippet = QPlainTextEdit()
        self.mcp_snippet.setReadOnly(True)
        self.mcp_snippet.setFont(mono_font(8))
        self.mcp_snippet.setMinimumHeight(120)
        cg.addWidget(self.mcp_snippet)
        btn_copy = QPushButton("Copy to clipboard")
        btn_copy.clicked.connect(self._mcp_copy_snippet)
        cg.addWidget(btn_copy)
        mcp_lay.addWidget(client_grp)
        mcp_lay.addStretch()
        tabs.addTab(mcp_tab, "MCP")
        for w in (self.mcp_port_spin, ):
            w.valueChanged.connect(self._mcp_refresh_snippet)
        self.mcp_token_edit.textChanged.connect(self._mcp_refresh_snippet)
        self.mcp_client_combo.currentIndexChanged.connect(self._mcp_refresh_snippet)
        self._mcp_refresh_snippet()

        # ── Backend ───────────────────────────────────────────────────────────
        backend_tab = QWidget()
        backend_lay = QVBoxLayout(backend_tab)
        backend_grp = QGroupBox("CAN Backend")
        bg_lay = QGridLayout(backend_grp)
        bg_lay.addWidget(QLabel("Backend:"), 0, 0)
        self.radio_pycan = QRadioButton("python-can (default)")
        self.radio_panda = QRadioButton("comma.ai Panda (USB)")
        self.radio_pycan.setChecked(True)
        self._backend_group = QButtonGroup()
        self._backend_group.addButton(self.radio_pycan, 0)
        self._backend_group.addButton(self.radio_panda, 1)
        bg_lay.addWidget(self.radio_pycan, 0, 1)
        bg_lay.addWidget(self.radio_panda, 1, 1)
        bg_lay.addWidget(QLabel("Panda Safety Mode:"), 2, 0)
        self.panda_safety_combo = QComboBox()
        self.panda_safety_combo.addItems(["SAFETY_NOOUTPUT", "SAFETY_ALLOUTPUT", "SAFETY_ELM327"])
        bg_lay.addWidget(self.panda_safety_combo, 2, 1)
        hint_backend = QLabel(
            "Panda requires: pip install panda --break-system-packages\n"
            "SAFETY_NOOUTPUT: receive only (safe default).\n"
            "SAFETY_ALLOUTPUT: allow all TX (use with care)."
        )
        hint_backend.setFont(mono_font(8))
        hint_backend.setObjectName("label_dim")
        hint_backend.setWordWrap(True)
        bg_lay.addWidget(hint_backend, 3, 0, 1, 2)
        backend_lay.addWidget(backend_grp)
        backend_lay.addStretch()
        tabs.addTab(backend_tab, "BACKEND")

        # ── Multi-Bus ─────────────────────────────────────────────────────────
        mb_tab = QWidget()
        mb_lay = QVBoxLayout(mb_tab)
        mb_lay.addWidget(QLabel(
            "Configure multiple CAN buses to record simultaneously. "
            "Each bus adds a tagged 'Bus' column to captured frames.",
            font=mono_font(8),
        ))
        self.multibus_table = QTableWidget(0, 4)
        self.multibus_table.setHorizontalHeaderLabels(["Name", "Interface", "Channel", "Bitrate"])
        self.multibus_table.setFont(mono_font())
        self.multibus_table.verticalHeader().setDefaultSectionSize(22)
        self.multibus_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        mb_lay.addWidget(self.multibus_table)
        mb_btn_row = QHBoxLayout()
        btn_mb_add = QPushButton("Add Bus")
        btn_mb_add.clicked.connect(self._mb_add_row)
        btn_mb_rem = QPushButton("Remove Selected")
        btn_mb_rem.clicked.connect(self._mb_remove_row)
        mb_btn_row.addWidget(btn_mb_add)
        mb_btn_row.addWidget(btn_mb_rem)
        mb_btn_row.addStretch()
        mb_lay.addLayout(mb_btn_row)
        mb_lay.addStretch()
        tabs.addTab(mb_tab, "MULTI-BUS")

        # ── Plugins ───────────────────────────────────────────────────────────
        plug_tab = QWidget()
        plug_lay = QVBoxLayout(plug_tab)
        plug_lay.addWidget(QLabel(
            "Plugins are loaded from ~/.canlab/plugins/*.py. Tick one to let it "
            "run — an enabled plugin executes with full application privileges.",
            font=mono_font(8), wordWrap=True))
        self.plugins_list = QListWidget()
        self.plugins_list.setFont(mono_font())
        plug_lay.addWidget(self.plugins_list)
        self.plugins_list.itemChanged.connect(self._on_plugin_toggled)
        btn_refresh = QPushButton("Refresh Plugin List")
        btn_refresh.clicked.connect(self._refresh_plugins)
        plug_lay.addWidget(btn_refresh)
        self._refresh_plugins()
        tabs.addTab(plug_tab, "PLUGINS")

        lay.addWidget(tabs)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.setObjectName("btn_green")
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

    def _mcp_generate_token(self):
        import secrets
        self.mcp_token_edit.setText(secrets.token_urlsafe(24))

    def _mcp_refresh_snippet(self, *_):
        from canlab.core.mcp_service import client_snippets
        from canlab.mcp_server import MCP_PATH
        url = f"http://127.0.0.1:{self.mcp_port_spin.value()}{MCP_PATH}"
        key = self.mcp_client_combo.currentData()
        self.mcp_snippet.setPlainText(
            client_snippets(url, self.mcp_token_edit.text().strip()).get(key, ""))

    def _mcp_copy_snippet(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.mcp_snippet.toPlainText())

    def get_mcp_config(self) -> dict:
        return {"port": self.mcp_port_spin.value(),
                "autostart": self.chk_mcp_autostart.isChecked(),
                "allow_remote": self.chk_mcp_remote.isChecked(),
                "token": self.mcp_token_edit.text().strip()}

    def get_openai_key(self) -> str:
        return self.openai_key_edit.text().strip()

    def _on_provider_changed(self, provider: str):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(AI_MODELS.get(provider, []))
        self.model_combo.blockSignals(False)

    # Settings keys (QSettings), kept in one place so save/load cannot drift.
    S_INTERFACE = "can/interface"
    S_CHANNEL = "can/channel"
    S_BITRATE = "can/bitrate"
    S_FD = "can/fd"
    S_FD_BITRATE = "can/data_bitrate"
    S_MULTIBUS = "can/multibus"
    S_EXTRA = "can/extra"                 # backend-specific kwargs of the default adapter
    S_ADAPTERS = "can/adapters"           # every saved adapter, JSON
    S_ADAPTER_DEFAULT = "can/adapter_default"
    S_REST_PORT = "rest/port"
    S_MCP_PORT = "mcp/port"
    S_MCP_AUTOSTART = "mcp/autostart"
    S_MCP_REMOTE = "mcp/allow_remote"
    S_MCP_TOKEN = "mcp/token"
    S_PROFILE = "vehicle/profile"
    S_BACKEND = "backend/kind"
    S_PANDA_SAFETY = "backend/panda_safety"
    S_FRAME_CAP = "frames/cap"

    def _load_persisted(self):
        st = settings()
        from canlab.core.adapters import Adapter, adapters_from_json
        self._adapters = adapters_from_json(st.value(self.S_ADAPTERS, "[]", str))
        self._adapter_default = st.value(self.S_ADAPTER_DEFAULT, "", str)
        if not self._adapters:
            # First run after the upgrade: the single interface that used to be
            # configured becomes the first adapter.
            try:
                extra = json.loads(st.value(self.S_EXTRA, "{}", str))
            except (ValueError, TypeError):
                extra = {}
            legacy = Adapter(
                name="Default", interface=st.value(self.S_INTERFACE, "socketcan", str),
                channel=st.value(self.S_CHANNEL, "can0", str),
                bitrate=int(st.value(self.S_BITRATE, 500000, int)),
                fd=st.value(self.S_FD, False, bool),
                data_bitrate=int(st.value(self.S_FD_BITRATE, 2000000, int)),
                extra=extra if isinstance(extra, dict) else {})
            self._adapters = [legacy]
            self._adapter_default = legacy.name
        if self._adapter_default not in [a.name for a in self._adapters]:
            self._adapter_default = self._adapters[0].name
        self._adapter_refresh_table()
        self.rest_port_spin.setValue(int(st.value(self.S_REST_PORT, 8765, int)))
        self.mcp_port_spin.setValue(int(st.value(self.S_MCP_PORT, 8766, int)))
        self.chk_mcp_autostart.setChecked(st.value(self.S_MCP_AUTOSTART, False, bool))
        self.chk_mcp_remote.setChecked(st.value(self.S_MCP_REMOTE, False, bool))
        self.mcp_token_edit.setText(st.value(self.S_MCP_TOKEN, "", str))
        idx = self.profile_combo.findData(st.value(self.S_PROFILE, "generic", str))
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        panda = st.value(self.S_BACKEND, "python-can", str) == "panda"
        self.radio_panda.setChecked(panda)
        self.radio_pycan.setChecked(not panda)
        self.panda_safety_combo.setCurrentText(
            st.value(self.S_PANDA_SAFETY, "SAFETY_NOOUTPUT", str))
        self.multibus_table.setRowCount(0)   # replace, never append
        try:
            for row in json.loads(st.value(self.S_MULTIBUS, "[]", str)):
                r = self.multibus_table.rowCount()
                self.multibus_table.insertRow(r)
                for ci, key in enumerate(("name", "interface", "channel", "bitrate")):
                    self.multibus_table.setItem(r, ci,
                                                QTableWidgetItem(str(row.get(key, ""))))
        except (ValueError, TypeError):
            log.debug("stored multi-bus config unreadable", exc_info=True)

    def _save_persisted(self):
        st = settings()
        from canlab.core.adapters import adapters_to_json
        st.setValue(self.S_ADAPTERS, adapters_to_json(self._adapters))
        st.setValue(self.S_ADAPTER_DEFAULT, self._adapter_default)
        # The default adapter is mirrored into the single-interface keys, which
        # is what the window reads at start-up.
        d = self.default_adapter()
        st.setValue(self.S_INTERFACE, d.interface)
        st.setValue(self.S_CHANNEL, d.channel)
        st.setValue(self.S_BITRATE, str(d.bitrate))
        st.setValue(self.S_FD, d.fd)
        st.setValue(self.S_FD_BITRATE, str(d.data_bitrate))
        st.setValue(self.S_EXTRA, json.dumps(d.extra))
        st.setValue(self.S_REST_PORT, self.rest_port_spin.value())
        st.setValue(self.S_MCP_PORT, self.mcp_port_spin.value())
        st.setValue(self.S_MCP_AUTOSTART, self.chk_mcp_autostart.isChecked())
        st.setValue(self.S_MCP_REMOTE, self.chk_mcp_remote.isChecked())
        st.setValue(self.S_MCP_TOKEN, self.mcp_token_edit.text().strip())
        st.setValue(self.S_PROFILE, self.profile_combo.currentData() or "generic")
        st.setValue(self.S_BACKEND,
                    "panda" if self.radio_panda.isChecked() else "python-can")
        st.setValue(self.S_PANDA_SAFETY, self.panda_safety_combo.currentText())
        st.setValue(self.S_MULTIBUS, json.dumps(self.get_multibus_config()))
        st.sync()

    def _load_values(self):
        self._load_persisted()
        self.api_key_edit.setText(load_api_key())
        self.groq_key_edit.setText(load_groq_key())
        self.openai_key_edit.setText(load_openai_key())

        # Restore saved provider + model
        saved_provider = load_ai_provider()
        idx = self.provider_combo.findText(saved_provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self._on_provider_changed(saved_provider)
        saved_model = load_ai_model()
        midx = self.model_combo.findText(saved_model)
        if midx >= 0:
            self.model_combo.setCurrentIndex(midx)
        elif saved_model:
            self.model_combo.setCurrentText(saved_model)   # a typed-in model id

        from canlab.core.state import get_state
        state = get_state()
        # Backend
        idx = self.profile_combo.findData(getattr(state, "vehicle_profile", "generic"))
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        backend = getattr(state, "active_backend", "python-can")
        self.radio_panda.setChecked(backend == "panda")
        self.radio_pycan.setChecked(backend != "panda")
        self.panda_safety_combo.setCurrentText(
            getattr(state, "panda_safety_model", "SAFETY_NOOUTPUT")
        )

    def _save(self):
        api_key  = self.api_key_edit.text().strip()
        groq_key = self.groq_key_edit.text().strip()
        openai_key = self.openai_key_edit.text().strip()
        if api_key:
            save_api_key(api_key)
        if groq_key:
            save_groq_key(groq_key)
        if openai_key:
            save_openai_key(openai_key)
        save_ai_provider(self.provider_combo.currentText())
        save_ai_model(self.model_combo.currentText().strip())

        # Persist new settings to AppState
        from canlab.core.state import get_state
        state = get_state()
        self._save_persisted()
        state.vehicle_profile = self.profile_combo.currentData() or "generic"
        state.active_backend = "panda" if self.radio_panda.isChecked() else "python-can"
        state.panda_safety_model = self.panda_safety_combo.currentText()
        state.canfd_enabled  = self.default_adapter().fd
        state.canfd_toggled.emit(state.canfd_enabled)

        self.accept()

    def _refresh_plugins(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidgetItem

        from canlab.core.plugin_loader import discover_plugins
        self.plugins_list.blockSignals(True)
        self.plugins_list.clear()
        plugins = discover_plugins()
        if not plugins:
            self.plugins_list.addItem("No plugins found.")
        for p in plugins:
            err = f"   [{p.get('error')}]" if p.get("error") else ""
            item = QListWidgetItem(f"{p['name']}  v{p['version']}  —  {p['path']}{err}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if p.get("enabled")
                               else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, p["path"])
            self.plugins_list.addItem(item)
        self.plugins_list.blockSignals(False)

    def _on_plugin_toggled(self, item):
        """Enabling a plugin is what allows it to run, so persist it at once."""
        from PyQt6.QtCore import Qt

        from canlab.core.plugin_loader import set_enabled
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            set_enabled(path, item.checkState() == Qt.CheckState.Checked)

    def _mb_add_row(self):
        row = self.multibus_table.rowCount()
        self.multibus_table.insertRow(row)
        defaults = [f"bus{row}", "socketcan", "can0", "500000"]
        for ci, val in enumerate(defaults):
            self.multibus_table.setItem(row, ci, QTableWidgetItem(val))

    def _mb_remove_row(self):
        row = self.multibus_table.currentRow()
        if row >= 0:
            self.multibus_table.removeRow(row)

    def get_multibus_config(self) -> list:
        """Return list of {name, interface, channel, bitrate} dicts."""
        result = []
        for row in range(self.multibus_table.rowCount()):
            def cell(c):
                item = self.multibus_table.item(row, c)
                return item.text() if item else ""
            result.append({
                "name":      cell(0),
                "interface": cell(1),
                "channel":   cell(2),
                "bitrate":   int(cell(3) or "500000"),
            })
        return result

    def get_api_key(self) -> str:
        return self.api_key_edit.text().strip()

    def get_vehicle_profile(self) -> str:
        return self.profile_combo.currentData() or "generic"

    def get_can_settings(self) -> dict:
        """The default adapter, in the shape the window's connect path takes."""
        d = self.default_adapter()
        return {"interface": d.interface, "channel": d.channel, "bitrate": int(d.bitrate),
                "fd": d.fd, "data_bitrate": int(d.data_bitrate), "extra": dict(d.extra),
                "name": d.name}

    # ── CAN adapters ──────────────────────────────────────────────────────────

    def default_adapter(self):
        for a in self._adapters:
            if a.name == self._adapter_default:
                return a
        if self._adapters:
            return self._adapters[0]
        from canlab.core.adapters import Adapter
        return Adapter(name="Default", interface="socketcan", channel="can0")

    def get_adapters(self) -> list:
        return list(self._adapters)

    def _adapter_refresh_table(self):
        t = self.adapter_table
        t.setRowCount(0)
        for a in self._adapters:
            r = t.rowCount()
            t.insertRow(r)
            fd = f" FD {a.data_bitrate // 1000}k" if a.fd else ""
            for ci, val in enumerate((a.name, a.interface, a.channel,
                                      f"{a.bitrate // 1000} kbit/s{fd}",
                                      "yes" if a.name == self._adapter_default else "")):
                t.setItem(r, ci, QTableWidgetItem(val))

    def _adapter_selected(self):
        r = self.adapter_table.currentRow()
        return self._adapters[r] if 0 <= r < len(self._adapters) else None

    def _unique_name(self, name: str) -> str:
        names = {a.name for a in self._adapters}
        base, n = name, 2
        while name in names:
            name = f"{base} ({n})"
            n += 1
        return name

    def _adapter_add(self):
        from canlab.ui.adapter_dialog import AdapterDialog
        dlg = AdapterDialog(parent=self)
        if dlg.exec():
            a = dlg.adapter()
            a.name = self._unique_name(a.name)
            self._adapters.append(a)
            if len(self._adapters) == 1:
                self._adapter_default = a.name
            self._adapter_refresh_table()

    def _adapter_edit(self):
        from canlab.ui.adapter_dialog import AdapterDialog
        a = self._adapter_selected()
        if a is None:
            return
        dlg = AdapterDialog(a, parent=self)
        if dlg.exec():
            new = dlg.adapter()
            if new.name != a.name:
                new.name = self._unique_name(new.name)
            if self._adapter_default == a.name:
                self._adapter_default = new.name
            self._adapters[self._adapters.index(a)] = new
            self._adapter_refresh_table()

    def _adapter_remove(self):
        a = self._adapter_selected()
        if a is None:
            return
        self._adapters.remove(a)
        if self._adapter_default == a.name:
            self._adapter_default = self._adapters[0].name if self._adapters else ""
        self._adapter_refresh_table()

    def _adapter_set_default(self):
        a = self._adapter_selected()
        if a is not None:
            self._adapter_default = a.name
            self._adapter_refresh_table()

    def _adapter_detect(self):
        from canlab.ui.adapter_dialog import DetectDialog
        dlg = DetectDialog(parent=self)
        if dlg.exec():
            for a in dlg.chosen():
                a.name = self._unique_name(a.name)
                self._adapters.append(a)
            if self._adapters and not self._adapter_default:
                self._adapter_default = self._adapters[0].name
            self._adapter_refresh_table()

    def _adapter_test(self):
        a = self._adapter_selected()
        if a is None:
            self.lbl_adapter_test.setText("Select an adapter first.")
            return
        if self._adapter_worker is not None and self._adapter_worker.isRunning():
            return
        from canlab.core.adapters import probe_adapter
        from canlab.ui.adapter_dialog import format_test_result
        from canlab.ui.compute_worker import ComputeWorker
        self.lbl_adapter_test.setText(f"Opening {a.name}...")
        self._adapter_worker = ComputeWorker(probe_adapter, a, listen_s=1.0, parent=self)
        self._adapter_worker.done.connect(
            lambda r: self.lbl_adapter_test.setText(f"{a.name}: {format_test_result(r)}"))
        self._adapter_worker.failed.connect(
            lambda e: self.lbl_adapter_test.setText(f"{a.name}: failed: {e}"))
        self._adapter_worker.start()

    def get_rest_api_port(self) -> int:
        return self.rest_port_spin.value()

    def get_groq_key(self) -> str:
        return self.groq_key_edit.text().strip()

    def get_ai_provider(self) -> str:
        return self.provider_combo.currentText()

    def get_ai_model(self) -> str:
        return self.model_combo.currentText().strip()
