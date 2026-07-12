import keyring
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QGroupBox, QGridLayout, QFileDialog, QMessageBox,
    QSpinBox, QListWidget, QListWidgetItem, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton, QButtonGroup,
)
from theme import COLORS, mono_font

KEYRING_SERVICE    = "canlab"
KEYRING_API_KEY    = "anthropic_api_key"
KEYRING_GH_TOKEN   = "github_token"
KEYRING_GROQ_KEY   = "groq_api_key"
KEYRING_AI_PROVIDER = "ai_provider"
KEYRING_AI_MODEL    = "ai_model"

# Provider → available models
AI_MODELS = {
    "Anthropic": [
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-haiku-4-5-20251001",
    ],
    "Groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ],
}


def save_api_key(key: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_API_KEY, key)


def load_api_key() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_API_KEY) or ""
    except Exception:
        return ""


def save_gh_token(token: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_GH_TOKEN, token)


def load_gh_token() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_GH_TOKEN) or ""
    except Exception:
        return ""


def save_groq_key(key: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_GROQ_KEY, key)


def load_groq_key() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_GROQ_KEY) or ""
    except Exception:
        return ""


def save_ai_provider(provider: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_AI_PROVIDER, provider)


def load_ai_provider() -> str:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_AI_PROVIDER) or "Anthropic"
    except Exception:
        return "Anthropic"


def save_ai_model(model: str):
    keyring.set_password(KEYRING_SERVICE, KEYRING_AI_MODEL, model)


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

    def _build_ui(self):
        lay = QVBoxLayout(self)
        tabs = QTabWidget()

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
        self.model_combo.setFont(mono_font(9))
        model_g_lay.addWidget(self.model_combo, 1, 1)
        hint_model = QLabel(
            "Groq default: llama-3.3-70b-versatile  ·  Anthropic default: claude-sonnet-5"
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

        # ── CAN Interface ─────────────────────────────────────────────────────
        can_tab = QWidget()
        can_lay = QVBoxLayout(can_tab)
        can_grp = QGroupBox("Default CAN Interface")
        can_g_lay = QGridLayout(can_grp)
        can_g_lay.addWidget(QLabel("Interface:"), 0, 0)
        self.iface_combo = QComboBox()
        self.iface_combo.addItems(["socketcan", "pcan", "kvaser", "virtual"])
        can_g_lay.addWidget(self.iface_combo, 0, 1)
        can_g_lay.addWidget(QLabel("Channel:"), 1, 0)
        self.channel_edit = QLineEdit("can0")
        can_g_lay.addWidget(self.channel_edit, 1, 1)
        can_g_lay.addWidget(QLabel("Bitrate:"), 2, 0)
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["500000", "250000", "1000000"])
        can_g_lay.addWidget(self.bitrate_combo, 2, 1)
        can_g_lay.addWidget(QLabel("Enable CAN FD:"), 3, 0)
        self.chk_canfd = QCheckBox("CAN FD (up to 64 bytes, 8 Mbps)")
        can_g_lay.addWidget(self.chk_canfd, 3, 1)
        can_g_lay.addWidget(QLabel("FD Data Bitrate:"), 4, 0)
        self.fd_bitrate_combo = QComboBox()
        self.fd_bitrate_combo.addItems(["2000000", "4000000", "8000000"])
        can_g_lay.addWidget(self.fd_bitrate_combo, 4, 1)
        can_lay.addWidget(can_grp)
        can_lay.addStretch()
        tabs.addTab(can_tab, "CAN INTERFACE")

        # ── GitHub ────────────────────────────────────────────────────────────
        gh_tab = QWidget()
        gh_lay = QVBoxLayout(gh_tab)
        gh_grp = QGroupBox("GitHub Repository")
        gh_g_lay = QGridLayout(gh_grp)
        gh_g_lay.addWidget(QLabel("Default Repo URL:"), 0, 0)
        self.gh_url_edit = QLineEdit()
        self.gh_url_edit.setPlaceholderText("https://github.com/owner/repo")
        gh_g_lay.addWidget(self.gh_url_edit, 0, 1)
        gh_lay.addWidget(gh_grp)
        token_grp = QGroupBox("GitHub Token")
        token_g_lay = QGridLayout(token_grp)
        token_g_lay.addWidget(QLabel("Token:"), 0, 0)
        self.gh_token_edit = QLineEdit()
        self.gh_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.gh_token_edit.setPlaceholderText("ghp_… (optional)")
        token_g_lay.addWidget(self.gh_token_edit, 0, 1)
        btn_show_tok = QPushButton("Show")
        btn_show_tok.setCheckable(True)
        btn_show_tok.toggled.connect(lambda v: self.gh_token_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if v else QLineEdit.EchoMode.Password
        ))
        token_g_lay.addWidget(btn_show_tok, 0, 2)
        hint = QLabel(
            "A token raises GitHub API rate limit from 60 to 5000 req/hr."
        )
        hint.setFont(mono_font(8))
        hint.setObjectName("label_dim")
        hint.setWordWrap(True)
        token_g_lay.addWidget(hint, 1, 0, 1, 3)
        gh_lay.addWidget(token_grp)

        comm_grp = QGroupBox("Community Profiles")
        comm_g_lay = QGridLayout(comm_grp)
        comm_g_lay.addWidget(QLabel("Profiles URL:"), 0, 0)
        self.community_url_edit = QLineEdit()
        self.community_url_edit.setPlaceholderText(
            "https://raw.githubusercontent.com/.../profiles.json"
        )
        comm_g_lay.addWidget(self.community_url_edit, 0, 1)
        hint_comm = QLabel("JSON array of vehicle profiles. Leave blank to use built-in defaults.")
        hint_comm.setFont(mono_font(8))
        hint_comm.setObjectName("label_dim")
        hint_comm.setWordWrap(True)
        comm_g_lay.addWidget(hint_comm, 1, 0, 1, 2)
        gh_lay.addWidget(comm_grp)
        gh_lay.addStretch()
        tabs.addTab(gh_tab, "GITHUB")

        # ── Cache ─────────────────────────────────────────────────────────────
        cache_tab = QWidget()
        cache_lay = QVBoxLayout(cache_tab)
        cache_grp = QGroupBox("Cache")
        cache_g_lay = QGridLayout(cache_grp)
        default_cache = str(Path.home() / ".canlab" / "cache")
        cache_g_lay.addWidget(QLabel("Cache Dir:"), 0, 0)
        self.cache_dir_edit = QLineEdit(default_cache)
        cache_g_lay.addWidget(self.cache_dir_edit, 0, 1)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._browse_cache)
        cache_g_lay.addWidget(btn_browse, 0, 2)
        btn_clear = QPushButton("Clear Cache")
        btn_clear.clicked.connect(self._clear_cache)
        cache_g_lay.addWidget(btn_clear, 1, 1)
        cache_lay.addWidget(cache_grp)
        cache_lay.addStretch()
        tabs.addTab(cache_tab, "CACHE")

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
        plug_lay.addWidget(QLabel("Plugins are loaded from  ~/.canlab/plugins/*.py", font=mono_font(8)))
        self.plugins_list = QListWidget()
        self.plugins_list.setFont(mono_font())
        plug_lay.addWidget(self.plugins_list)
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

    def _on_provider_changed(self, provider: str):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(AI_MODELS.get(provider, []))
        self.model_combo.blockSignals(False)

    def _load_values(self):
        self.api_key_edit.setText(load_api_key())
        self.gh_token_edit.setText(load_gh_token())
        self.groq_key_edit.setText(load_groq_key())

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

        # Community URL default
        from core.state import get_state
        state = get_state()
        self.community_url_edit.setText(
            getattr(state, "community_profiles_url", "")
        )
        # Backend
        backend = getattr(state, "active_backend", "python-can")
        self.radio_panda.setChecked(backend == "panda")
        self.radio_pycan.setChecked(backend != "panda")
        self.panda_safety_combo.setCurrentText(
            getattr(state, "panda_safety_model", "SAFETY_NOOUTPUT")
        )

    def _save(self):
        api_key  = self.api_key_edit.text().strip()
        gh_token = self.gh_token_edit.text().strip()
        groq_key = self.groq_key_edit.text().strip()
        if api_key:
            save_api_key(api_key)
        save_gh_token(gh_token)
        if groq_key:
            save_groq_key(groq_key)
        save_ai_provider(self.provider_combo.currentText())
        save_ai_model(self.model_combo.currentText())

        # Persist new settings to AppState
        from core.state import get_state
        state = get_state()
        comm_url = self.community_url_edit.text().strip()
        if comm_url:
            state.community_profiles_url = comm_url

        state.active_backend = "panda" if self.radio_panda.isChecked() else "python-can"
        state.panda_safety_model = self.panda_safety_combo.currentText()
        state.canfd_enabled  = self.chk_canfd.isChecked()
        state.canfd_toggled.emit(state.canfd_enabled)

        self.accept()

    def _browse_cache(self):
        path = QFileDialog.getExistingDirectory(self, "Select Cache Directory")
        if path:
            self.cache_dir_edit.setText(path)

    def _clear_cache(self):
        import shutil
        cache = Path(self.cache_dir_edit.text())
        if cache.exists():
            shutil.rmtree(cache)
            cache.mkdir(parents=True, exist_ok=True)
            QMessageBox.information(self, "Cleared", "Cache cleared.")

    def _refresh_plugins(self):
        from core.plugin_loader import discover_plugins
        plugins = discover_plugins()
        self.plugins_list.clear()
        if not plugins:
            self.plugins_list.addItem("No plugins found.")
        for p in plugins:
            status = "✓" if p.get("enabled") else "✗"
            err    = f"  ERROR: {p.get('error','')}" if p.get("error") else ""
            self.plugins_list.addItem(
                f"{status}  {p['name']}  v{p['version']}  —  {p['path']}{err}"
            )

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

    def get_gh_token(self) -> str:
        return self.gh_token_edit.text().strip()

    def get_can_settings(self) -> dict:
        return {
            "interface":   self.iface_combo.currentText(),
            "channel":     self.channel_edit.text(),
            "bitrate":     int(self.bitrate_combo.currentText()),
            "fd":          self.chk_canfd.isChecked(),
            "data_bitrate": int(self.fd_bitrate_combo.currentText()),
        }

    def get_github_url(self) -> str:
        return self.gh_url_edit.text().strip()

    def get_rest_api_port(self) -> int:
        return self.rest_port_spin.value()

    def get_groq_key(self) -> str:
        return self.groq_key_edit.text().strip()

    def get_ai_provider(self) -> str:
        return self.provider_combo.currentText()

    def get_ai_model(self) -> str:
        return self.model_combo.currentText()
