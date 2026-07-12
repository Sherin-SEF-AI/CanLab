import re
import pandas as pd
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QTextEdit, QProgressBar, QGroupBox, QLineEdit,
    QTabWidget,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QBrush
from theme import COLORS, mono_font
from core.state import get_state
from core.ai_client import AIWorker
from ui.animations import SpinnerWidget, ButtonPulse, TypewriterCursor, flash_widget

BYTE_COLS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]
SPARKLINE_COLORS = ["#00ff88","#ffb300","#00aaff","#ff6b6b","#cc88ff",
                    "#ff9944","#44ffcc","#ff44aa"]


class AIEngineTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._state      = get_state()
        self._api_key    = ""
        self._groq_key   = ""
        self._provider   = "Anthropic"
        self._model      = "claude-sonnet-4-6"
        self._queue:     list  = []
        self._worker     = None
        self._current_id = ""
        self._event_correlations: list = []
        self._nl_worker  = None
        self._btn_pulse  = None   # ButtonPulse — set after _build_ui
        self._tw_cursor  = None   # TypewriterCursor — set after _build_ui
        self._build_ui()
        # Initialise animations (widgets must exist first)
        self._btn_pulse = ButtonPulse(self.btn_analyze)
        self._tw_cursor = TypewriterCursor(self.response_text)
        self._state.id_selected.connect(self._load_id)
        self._state.repo_loaded.connect(self._on_repo_loaded)
        self._state.anomaly_requested.connect(self._on_anomaly_requested)
        # Load persisted memory
        from core.ai_memory import load_memory
        self._state.ai_memory = load_memory()

    def set_api_key(self, key: str):
        self._api_key = key

    def set_ai_config(self, provider: str, model: str,
                      groq_key: str = "", api_key: str = ""):
        self._provider = provider
        self._model    = model
        self._groq_key = groq_key
        if api_key:
            self._api_key = api_key
        self._update_provider_ui()

    def _update_provider_ui(self):
        provider = self._provider
        model    = self._model
        color    = COLORS["green"] if provider == "Groq" else COLORS["amber"]
        self.lbl_provider_badge.setText(f"AI: {provider} / {model}")
        self.lbl_provider_badge.setStyleSheet(
            f"color:{color}; background:{COLORS['panel_bg']}; "
            f"border:1px solid {color}; border-radius:3px; padding:1px 4px;"
        )
        self.btn_analyze.setText(f"Analyze with {provider}")
        self.lbl_response_header.setText(f"{provider.upper()} RESPONSE")

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: Queue ───────────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(210)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(4, 4, 4, 4)
        left_lay.setSpacing(4)

        self.lbl_repo_badge = QLabel("No repo loaded")
        self.lbl_repo_badge.setFont(mono_font(8))
        self.lbl_repo_badge.setObjectName("label_dim")
        self.lbl_repo_badge.setWordWrap(True)
        left_lay.addWidget(self.lbl_repo_badge)

        self.lbl_provider_badge = QLabel("AI: Anthropic / claude-sonnet-4-6")
        self.lbl_provider_badge.setFont(mono_font(7))
        self.lbl_provider_badge.setStyleSheet(
            f"color:{COLORS['amber']}; background:{COLORS['panel_bg']}; "
            f"border:1px solid {COLORS['amber']}; border-radius:3px; padding:1px 4px;"
        )
        self.lbl_provider_badge.setWordWrap(True)
        left_lay.addWidget(self.lbl_provider_badge)

        lbl_q = QLabel("ANALYSIS QUEUE")
        lbl_q.setObjectName("label_dim")
        lbl_q.setFont(mono_font(8))
        left_lay.addWidget(lbl_q)

        self.queue_list = QListWidget()
        self.queue_list.setFont(mono_font())
        left_lay.addWidget(self.queue_list)

        self.btn_add_sel = QPushButton("Add Selected ID")
        self.btn_add_sel.clicked.connect(self._add_selected)
        left_lay.addWidget(self.btn_add_sel)

        self.btn_add_unk = QPushButton("Add All UNKNOWN")
        self.btn_add_unk.clicked.connect(self._add_all_unknown)
        left_lay.addWidget(self.btn_add_unk)

        self.btn_add_all = QPushButton("Add All IDs")
        self.btn_add_all.clicked.connect(self._add_all_ids)
        left_lay.addWidget(self.btn_add_all)

        self.btn_run = QPushButton("Run Queue")
        self.btn_run.setObjectName("btn_amber")
        self.btn_run.clicked.connect(self._run_queue)
        left_lay.addWidget(self.btn_run)

        self.queue_progress = QProgressBar()
        self.queue_progress.setVisible(False)
        left_lay.addWidget(self.queue_progress)

        # Memory indicator
        self.lbl_memory = QLabel("Memory: 0 entries")
        self.lbl_memory.setFont(mono_font(8))
        self.lbl_memory.setObjectName("label_dim")
        left_lay.addWidget(self.lbl_memory)
        self._refresh_memory_label()

        splitter.addWidget(left)

        # ── Right: Analysis ───────────────────────────────────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # ── Tab 1: Single ID Analysis ──────────────────────────────────────
        analysis_widget = QWidget()
        aw_lay = QVBoxLayout(analysis_widget)
        aw_lay.setContentsMargins(6, 6, 6, 6)
        aw_lay.setSpacing(4)

        right_splitter = QSplitter(Qt.Orientation.Vertical)

        workspace = QWidget()
        ws_lay = QVBoxLayout(workspace)
        ws_lay.setContentsMargins(0, 0, 0, 0)
        ws_lay.setSpacing(4)

        self.lbl_id_header = QLabel("Select an ID to analyze")
        self.lbl_id_header.setFont(mono_font(10, bold=True))
        self.lbl_id_header.setObjectName("label_green")
        ws_lay.addWidget(self.lbl_id_header)

        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("label_dim")
        self.lbl_stats.setFont(mono_font(8))
        ws_lay.addWidget(self.lbl_stats)

        self.repo_grp = QGroupBox("REPO CONTEXT")
        repo_lay = QVBoxLayout(self.repo_grp)
        repo_lay.setContentsMargins(4, 4, 4, 4)
        self.lbl_repo_info = QLabel("No GitHub repo loaded.")
        self.lbl_repo_info.setFont(mono_font(8))
        self.lbl_repo_info.setObjectName("label_dim")
        self.lbl_repo_info.setWordWrap(True)
        repo_lay.addWidget(self.lbl_repo_info)
        ws_lay.addWidget(self.repo_grp)

        self.raw_preview = QTextEdit()
        self.raw_preview.setReadOnly(True)
        self.raw_preview.setMaximumHeight(110)
        self.raw_preview.setFont(mono_font(8))
        ws_lay.addWidget(self.raw_preview)

        lbl_spark = QLabel("BYTE TIMELINES")
        lbl_spark.setObjectName("label_dim")
        lbl_spark.setFont(mono_font(8))
        ws_lay.addWidget(lbl_spark)

        self.sparkline_widget = pg.PlotWidget()
        self.sparkline_widget.setBackground(COLORS["bg"])
        self.sparkline_widget.setFixedHeight(80)
        self.sparkline_widget.setMouseEnabled(False, False)
        self.sparkline_widget.hideAxis("left")
        self.sparkline_widget.hideAxis("bottom")
        ws_lay.addWidget(self.sparkline_widget)

        lbl_ctx = QLabel("CONTEXT")
        lbl_ctx.setObjectName("label_dim")
        lbl_ctx.setFont(mono_font(8))
        ws_lay.addWidget(lbl_ctx)

        self.context_input = QTextEdit()
        self.context_input.setMaximumHeight(55)
        self.context_input.setFont(mono_font())
        ws_lay.addWidget(self.context_input)

        analyze_row = QHBoxLayout()
        self.btn_analyze = QPushButton("Analyze with AI")
        self.btn_analyze.setObjectName("btn_amber")
        self.btn_analyze.clicked.connect(self._run_analysis)
        analyze_row.addWidget(self.btn_analyze, stretch=1)
        self._spinner = SpinnerWidget(size=22, color=COLORS["amber"])
        self._spinner.hide()
        analyze_row.addWidget(self._spinner)
        ws_lay.addLayout(analyze_row)

        self.lbl_events = QLabel("")
        self.lbl_events.setObjectName("label_amber")
        self.lbl_events.setFont(mono_font(8))
        self.lbl_events.setWordWrap(True)
        ws_lay.addWidget(self.lbl_events)

        right_splitter.addWidget(workspace)

        response_widget = QWidget()
        resp_lay = QVBoxLayout(response_widget)
        resp_lay.setContentsMargins(0, 4, 0, 4)
        resp_lay.setSpacing(4)

        self.lbl_response_header = QLabel("AI RESPONSE")
        self.lbl_response_header.setObjectName("label_dim")
        self.lbl_response_header.setFont(mono_font(8))
        resp_lay.addWidget(self.lbl_response_header)

        self.response_text = QTextEdit()
        self.response_text.setReadOnly(True)
        self.response_text.setFont(mono_font())
        self.response_text.setStyleSheet(
            f"QTextEdit {{ background: {COLORS['panel_bg']}; color: {COLORS['amber']}; "
            f"border: 1px solid {COLORS['border']}; }}"
        )
        resp_lay.addWidget(self.response_text)

        btn_row = QHBoxLayout()
        self.btn_accept_dbc  = QPushButton("Accept → DBC")
        self.btn_accept_name = QPushButton("Accept → Name")
        self.btn_reanalyze   = QPushButton("Re-analyze")
        self.btn_save_memory = QPushButton("Save to Memory")
        for b in [self.btn_accept_dbc, self.btn_accept_name,
                  self.btn_reanalyze, self.btn_save_memory]:
            b.setEnabled(False)
            btn_row.addWidget(b)
        resp_lay.addLayout(btn_row)

        self.btn_accept_dbc.clicked.connect(self._accept_to_dbc)
        self.btn_accept_name.clicked.connect(self._accept_name)
        self.btn_reanalyze.clicked.connect(self._run_analysis)
        self.btn_save_memory.clicked.connect(self._save_to_memory)

        right_splitter.addWidget(response_widget)
        right_splitter.setSizes([320, 380])
        aw_lay.addWidget(right_splitter)
        tabs.addTab(analysis_widget, "SINGLE ID")

        # ── Tab 2: Natural Language Query ──────────────────────────────────
        nl_widget = QWidget()
        nl_lay = QVBoxLayout(nl_widget)
        nl_lay.setContentsMargins(8, 8, 8, 8)
        nl_lay.setSpacing(6)

        nl_lay.addWidget(QLabel("Ask a question about the CAN data:", font=mono_font(9)))

        self.nl_input = QLineEdit()
        self.nl_input.setFont(mono_font())
        self.nl_input.setPlaceholderText(
            "e.g. What signal fires when brake is pressed?"
        )
        self.nl_input.returnPressed.connect(self._run_nl_query)
        nl_lay.addWidget(self.nl_input)

        self.btn_nl_ask = QPushButton("Ask Claude")
        self.btn_nl_ask.setObjectName("btn_amber")
        self.btn_nl_ask.clicked.connect(self._run_nl_query)
        nl_lay.addWidget(self.btn_nl_ask)

        nl_lay.addWidget(QLabel("RESPONSE:", font=mono_font(8)))
        self.nl_response = QTextEdit()
        self.nl_response.setReadOnly(True)
        self.nl_response.setFont(mono_font())
        self.nl_response.setStyleSheet(
            f"QTextEdit {{ background:{COLORS['panel_bg']}; color:{COLORS['amber']}; "
            f"border:1px solid {COLORS['border']}; }}"
        )
        nl_lay.addWidget(self.nl_response)
        tabs.addTab(nl_widget, "NL QUERY")

        # ── Tab 3: Memory Viewer ───────────────────────────────────────────
        mem_widget = QWidget()
        mem_lay = QVBoxLayout(mem_widget)
        mem_lay.setContentsMargins(8, 8, 8, 8)
        mem_lay.setSpacing(6)

        mem_hdr = QHBoxLayout()
        mem_hdr.addWidget(QLabel("ANALYSIS MEMORY", font=mono_font(9)))
        mem_hdr.addStretch()
        btn_clear_mem = QPushButton("Clear Memory")
        btn_clear_mem.clicked.connect(self._clear_memory)
        mem_hdr.addWidget(btn_clear_mem)
        mem_lay.addLayout(mem_hdr)

        self.memory_text = QTextEdit()
        self.memory_text.setReadOnly(True)
        self.memory_text.setFont(mono_font(8))
        mem_lay.addWidget(self.memory_text)
        self._refresh_memory_view()
        tabs.addTab(mem_widget, "MEMORY")

        right_lay.addWidget(tabs)
        splitter.addWidget(right)
        splitter.setSizes([210, 790])
        layout.addWidget(splitter)

    # ── Repo context ──────────────────────────────────────────────────────────

    def _on_repo_loaded(self, info: dict):
        owner = info.get("owner", "")
        repo  = info.get("repo", "")
        desc  = info.get("description", "")
        name  = f"{owner}/{repo}" if owner else repo
        self.lbl_repo_badge.setText(f"REPO: {name}")
        self.lbl_repo_badge.setStyleSheet(f"color:{COLORS['green']}")
        readme = self._state.repo_readme
        from core.event_correlator import parse_annotations
        n_events = len(parse_annotations(readme))
        self.lbl_repo_info.setText(
            f"{name}  —  {desc}\n"
            f"README: {'yes' if readme else 'no'}  |  Events: {n_events}"
        )
        self.lbl_repo_info.setStyleSheet(f"color:{COLORS['green']}")

    # ── Queue management ──────────────────────────────────────────────────────

    def queue_id(self, hex_id: str):
        if not any(self.queue_list.item(i).text().startswith(hex_id)
                   for i in range(self.queue_list.count())):
            item = QListWidgetItem(f"{hex_id}  [waiting]")
            item.setData(Qt.ItemDataRole.UserRole, hex_id)
            item.setForeground(QBrush(QColor(COLORS["dim"])))
            self.queue_list.addItem(item)
            if hex_id not in self._queue:
                self._queue.append(hex_id)

    def _add_selected(self):
        if self._state.selected_id:
            self.queue_id(self._state.selected_id)

    def _add_all_unknown(self):
        if self._state.frames_df.empty:
            return
        from core.signal_analyzer import analyze_id
        for can_id in self._state.get_unique_ids():
            frames = self._state.get_frames_for_id(can_id)
            stats  = analyze_id(frames)
            if stats.get("suspected_type") == "UNKNOWN":
                self.queue_id(can_id)

    def _add_all_ids(self):
        for can_id in self._state.get_unique_ids():
            self.queue_id(can_id)

    def _run_queue(self):
        if not self._queue:
            return
        self.queue_progress.setVisible(True)
        self.queue_progress.setMaximum(len(self._queue))
        self.queue_progress.setValue(0)
        self._process_next_in_queue()

    def _process_next_in_queue(self):
        if not self._queue:
            self.queue_progress.setVisible(False)
            return
        hex_id = self._queue[0]
        self._load_id(hex_id)
        self._run_analysis(from_queue=True)

    # ── ID loading ────────────────────────────────────────────────────────────

    def _load_id(self, hex_id: str):
        if not hex_id:
            return
        try:
            self._load_id_impl(hex_id)
        except Exception as e:
            self._current_id = hex_id
            self.lbl_id_header.setText(f"0x{hex_id}  — load error: {e}")

    def _load_id_impl(self, hex_id: str):
        self._current_id = hex_id
        frames = self._state.get_frames_for_id(hex_id)
        self.lbl_id_header.setText(f"0x{hex_id}  —  {len(frames)} frames")
        if frames.empty:
            return
        total_time = frames["Timestamp"].iloc[-1] - frames["Timestamp"].iloc[0]
        freq = len(frames) / total_time if total_time > 0 else 0
        self.lbl_stats.setText(
            f"Freq: {freq:.1f}Hz  |  Span: {total_time:.1f}s  |  "
            f"Bus: {frames['Bus'].iloc[0] if 'Bus' in frames else '?'}"
        )
        last20 = frames.tail(20)
        lines  = []
        for _, row in last20.iterrows():
            byte_str = " ".join(
                format(int(row[c]), "02X") if pd.notna(row.get(c)) else "--"
                for c in BYTE_COLS
            )
            lines.append(f"[{row.get('Timestamp',0):.3f}] {byte_str}")
        self.raw_preview.setPlainText("\n".join(lines))
        self.sparkline_widget.clear()
        for i, col in enumerate(BYTE_COLS):
            if col not in frames.columns:
                continue
            s = frames[col].dropna()
            if s.empty:
                continue
            color = SPARKLINE_COLORS[i % len(SPARKLINE_COLORS)]
            t = frames.loc[s.index, "Timestamp"].values
            t = t - t[0]
            y = s.values.astype(float) + i * 30
            self.sparkline_widget.plot(t, y, pen=pg.mkPen(color=color, width=1))
        ann = self._state.annotations
        if ann:
            corr = [lbl for lbl, ids in ann.items() if hex_id in ids]
            if corr:
                self._event_correlations = corr
                self.lbl_events.setText("Events: " + " | ".join(corr[:4]))
                self.context_input.setPlainText("; ".join(corr[:3]))
                return
        self._event_correlations = []
        self.lbl_events.setText("")

    # ── Anomaly ───────────────────────────────────────────────────────────────

    def _on_anomaly_requested(self, hex_id: str, frames_df):
        self._load_id(hex_id)
        self.context_input.setPlainText(
            "Anomalous frames detected — please explain what might cause this."
        )
        self._run_analysis()

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _build_ml_insights(self, frames: pd.DataFrame) -> str:
        """Run lightweight ML analysis synchronously and return a summary string."""
        lines = []
        try:
            from core.signal_classifier import classify_frame, classify_message_type
            roles    = classify_frame(frames, self._current_id)
            msg_type = classify_message_type(frames)
            if roles:
                lines.append("Byte roles (ML classifier):")
                for col, info in sorted(roles.items()):
                    if info["role"] != "UNKNOWN":
                        lines.append(
                            f"  {col}: {info['role']} (conf={info['confidence']:.0%}"
                            f", entropy={info['entropy']:.1f}"
                            + (f", {info['detail']}" if info.get("detail") else "")
                            + ")"
                        )
            if msg_type.get("type"):
                period = msg_type.get("period_ms")
                period_str = f"{period} ms" if period is not None else "aperiodic"
                lines.append(
                    f"Message type: {msg_type['type']}  "
                    f"period={period_str}  "
                    f"jitter={msg_type.get('jitter_pct','?')}%  "
                    f"({msg_type.get('class','?')})"
                )
        except Exception:
            pass

        try:
            from core.checksum_guesser import guess_all_bytes
            cs = guess_all_bytes(frames, self._current_id)
            for byte_idx, matches in cs.items():
                if matches:
                    top = matches[0]
                    if top["confidence"] >= 0.80:
                        lines.append(
                            f"Checksum: B{byte_idx} = {top['algorithm']} "
                            f"of remaining bytes (confidence={top['confidence']:.0%})"
                        )
        except Exception:
            pass

        try:
            idx = getattr(self._state, "_embedding_index", {})
            if idx and self._current_id in idx:
                from core.signal_embedding import find_similar
                similar = find_similar(self._current_id, idx, top_k=3)
                if similar:
                    sim_str = ", ".join(
                        f"0x{s['id']} ({s['similarity']:.0%})" for s in similar
                    )
                    lines.append(f"Similar IDs in this log: {sim_str}")
        except Exception:
            pass

        return "\n".join(lines)

    def _advance_queue(self, hex_id: str):
        """Remove hex_id from the queue and process the next one (or finish)."""
        if hex_id in self._queue:
            self._queue.remove(hex_id)
        done = self.queue_progress.maximum() - len(self._queue)
        self.queue_progress.setValue(done)
        if self._queue:
            QTimer.singleShot(500, self._process_next_in_queue)
        else:
            self.queue_progress.setVisible(False)

    def _run_analysis(self, from_queue=False):
        # Remember whether this run came from the batch queue so error/early-exit
        # paths can still advance it (otherwise one failure freezes the queue
        # with the progress bar stuck visible).
        self._current_from_queue = from_queue
        if not self._current_id:
            if from_queue:
                self._advance_queue(self._current_id)
            return
        active_key = self._groq_key if self._provider == "Groq" else self._api_key
        if not active_key:
            self.response_text.setPlainText(
                f"ERROR: No {self._provider} API key configured.\nGo to Settings > API Keys."
            )
            if from_queue:
                self._advance_queue(self._current_id)
            return
        frames  = self._state.get_frames_for_id(self._current_id)
        context = self.context_input.toPlainText()

        # Inject memory context
        from core.ai_memory import get_memory_context
        mem_ctx = get_memory_context(self._state.ai_memory)
        if mem_ctx:
            context = mem_ctx + "\n\n" + context

        # Build ML pre-analysis to supercharge the prompt
        ml_insights = self._build_ml_insights(frames)

        repo_ctx = None
        if self._state.repo_info:
            repo_ctx = dict(self._state.repo_info)
            repo_ctx["readme"] = self._state.repo_readme

        self.response_text.setPlainText("")
        self.btn_analyze.setEnabled(False)
        self.btn_accept_dbc.setEnabled(False)
        self.btn_accept_name.setEnabled(False)
        self.btn_save_memory.setEnabled(False)
        self._spinner.start()
        self._btn_pulse.start()
        self._tw_cursor.start()

        self._worker = AIWorker(
            api_key=self._api_key,
            id_hex=self._current_id,
            frames_df=frames,
            context=context,
            event_correlations=self._event_correlations,
            repo_context=repo_ctx,
            provider=self._provider,
            model=self._model,
            groq_key=self._groq_key,
            ml_insights=ml_insights,
        )
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.finished.connect(lambda r: self._on_finished(r, from_queue))
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self._update_queue_item(self._current_id, "analyzing")

    def _on_chunk(self, text: str):
        # Stop cursor blink while inserting so blocks don't accumulate
        cur = self.response_text.textCursor()
        from PyQt6.QtGui import QTextCursor
        cur.movePosition(QTextCursor.MoveOperation.End)
        # Remove trailing cursor block if present before inserting new text
        txt = self.response_text.toPlainText()
        if txt.endswith(" █"):
            for _ in range(2):
                cur.deletePreviousChar()
        self.response_text.setTextCursor(cur)
        self.response_text.insertPlainText(text)
        self.response_text.ensureCursorVisible()

    def _on_finished(self, full_response: str, from_queue: bool):
        self._spinner.stop()
        self._btn_pulse.stop()
        self._tw_cursor.stop()
        self.btn_analyze.setEnabled(True)
        self.btn_accept_dbc.setEnabled(True)
        self.btn_accept_name.setEnabled(True)
        self.btn_save_memory.setEnabled(True)
        flash_widget(self.response_text, COLORS["green"], 400)
        self._state.signal_analyzed.emit(self._current_id)
        self._state.analyzed_ids[self._current_id] = full_response
        self._update_queue_item(self._current_id, "done")

        # Auto-save to memory if from queue
        if from_queue:
            self._save_to_memory_silent(full_response)

        if from_queue:
            self._advance_queue(self._current_id)

    def _on_error(self, err: str):
        self._spinner.stop()
        self._btn_pulse.stop()
        self._tw_cursor.stop()
        self.response_text.setPlainText(f"ERROR: {err}")
        self.btn_analyze.setEnabled(True)
        flash_widget(self.response_text, COLORS.get("error", "#ff4444"), 600)
        self._update_queue_item(self._current_id, "error")
        # Keep the batch queue moving instead of stalling on one failure.
        if getattr(self, "_current_from_queue", False):
            self._advance_queue(self._current_id)

    # ── Memory ────────────────────────────────────────────────────────────────

    def _save_to_memory(self):
        conclusion = self.response_text.toPlainText().strip()
        if conclusion and self._current_id:
            self._save_to_memory_silent(conclusion)

    def _save_to_memory_silent(self, conclusion: str):
        from core.ai_memory import add_entry
        self._state.ai_memory = add_entry(
            self._state.ai_memory, self._current_id, conclusion
        )
        self._refresh_memory_label()
        self._refresh_memory_view()

    def _clear_memory(self):
        from core.ai_memory import save_memory
        self._state.ai_memory = []
        save_memory([])
        self._refresh_memory_label()
        self._refresh_memory_view()

    def _refresh_memory_label(self):
        n = len(self._state.ai_memory)
        self.lbl_memory.setText(f"Memory: {n} entries")

    def _refresh_memory_view(self):
        if not hasattr(self, "memory_text"):
            return
        entries = self._state.ai_memory
        if not entries:
            self.memory_text.setPlainText("No memories stored.")
            return
        lines = []
        for e in reversed(entries):
            lines.append(
                f"[{e.get('timestamp','?')}]  ID 0x{e.get('id','?')}  ({e.get('source','AI')})"
            )
            lines.append(e.get("conclusion", "")[:300])
            lines.append("")
        self.memory_text.setPlainText("\n".join(lines))

    # ── NL Query ─────────────────────────────────────────────────────────────

    def _run_nl_query(self):
        question = self.nl_input.text().strip()
        if not question:
            return
        active_key = self._groq_key if self._provider == "Groq" else self._api_key
        if not active_key:
            self.nl_response.setPlainText(
                f"ERROR: No {self._provider} API key configured."
            )
            return

        provider_label = self._provider
        self.nl_response.setPlainText(f"Asking {provider_label}…")
        self.btn_nl_ask.setEnabled(False)

        # Build a synthetic "frame" showing unique IDs + their analysis + memory
        from core.ai_memory import get_memory_context
        from core.signal_analyzer import analyze_id

        summary_lines = [f"User question: {question}\n", "=== CAN Data Summary ==="]
        for can_id in self._state.get_unique_ids()[:20]:
            frames = self._state.get_frames_for_id(can_id)
            stats  = analyze_id(frames)
            summary_lines.append(
                f"ID 0x{can_id}: {stats.get('suspected_type','?')} "
                f"freq={stats.get('frequency_hz',0):.1f}Hz  "
                f"frames={stats.get('frame_count',0)}"
            )
        mem_ctx = get_memory_context(self._state.ai_memory)
        if mem_ctx:
            summary_lines.append(mem_ctx)

        if self._state.annotations:
            summary_lines.append("\n=== Annotated Events ===")
            for event, ids in list(self._state.annotations.items())[:10]:
                summary_lines.append(f"{event}: IDs {ids}")

        context_text = "\n".join(summary_lines)

        # Reuse AIWorker with a dummy id_hex
        repo_ctx = None
        if self._state.repo_info:
            repo_ctx = dict(self._state.repo_info)
            repo_ctx["readme"] = self._state.repo_readme

        self._nl_worker = AIWorker(
            api_key=self._api_key,
            id_hex="NL_QUERY",
            frames_df=pd.DataFrame(),
            context=context_text,
            event_correlations=[],
            repo_context=repo_ctx,
            provider=self._provider,
            model=self._model,
            groq_key=self._groq_key,
        )
        self._nl_worker.chunk_received.connect(
            lambda t: self.nl_response.insertPlainText(t)
        )
        self._nl_worker.finished.connect(
            lambda r: self.btn_nl_ask.setEnabled(True)
        )
        self._nl_worker.error.connect(
            lambda e: self.nl_response.setPlainText(f"ERROR: {e}")
        )
        self.nl_response.setPlainText("")
        self._nl_worker.start()

    # ── Queue helpers ─────────────────────────────────────────────────────────

    def _update_queue_item(self, hex_id: str, status: str):
        colors = {
            "waiting":   COLORS["dim"],
            "analyzing": COLORS["amber"],
            "done":      COLORS["green"],
            "error":     COLORS["error"],
        }
        for i in range(self.queue_list.count()):
            item = self.queue_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == hex_id:
                item.setText(f"{hex_id}  [{status}]")
                item.setForeground(QBrush(QColor(colors.get(status, COLORS["text"]))))
                return

    # ── DBC / name accept ─────────────────────────────────────────────────────

    def _accept_to_dbc(self):
        response = self.response_text.toPlainText()
        sig = self._parse_dbc_from_response(response)
        if sig:
            self._state.add_dbc_signal(sig)

    def _accept_name(self):
        response = self.response_text.toPlainText()
        name = self._extract_signal_name(response)
        if name and self._current_id:
            sig = {
                "message_id":   self._current_id,
                "message_name": name,
                "signal_name":  name,
                "start_bit":    0,
                "length":       8,
                "byte_order":   "little",
                "value_type":   "unsigned",
                "scale":        1.0,
                "offset":       0.0,
                "min_val":      0,
                "max_val":      255,
                "unit":         "",
                "description":  f"AI-named from 0x{self._current_id}",
            }
            self._state.add_dbc_signal(sig)

    def _extract_signal_name(self, text: str) -> str:
        m = re.search(r"(?:signal|name)[:\s]+([A-Za-z_][A-Za-z0-9_]*)", text, re.IGNORECASE)
        return m.group(1) if m else f"Signal_{self._current_id}"

    def _parse_dbc_from_response(self, text: str) -> dict | None:
        sig = {
            "message_id":   self._current_id,
            "message_name": f"MSG_{self._current_id}",
            "signal_name":  self._extract_signal_name(text),
            "start_bit":    0,
            "length":       8,
            "byte_order":   "little",
            "value_type":   "unsigned",
            "scale":        1.0,
            "offset":       0.0,
            "min_val":      0,
            "max_val":      255,
            "unit":         "",
            "description":  f"AI-analyzed ID 0x{self._current_id}",
        }
        for field, pat in [
            ("start_bit", r"start.?bit[:\s]+(\d+)"),
            ("length",    r"length[:\s]+(\d+)"),
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                sig[field] = int(m.group(1))
        for field, pat in [
            ("scale",  r"scale[:\s]+([\d.]+)"),
            ("offset", r"offset[:\s]+([\d.eE+-]+)"),
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                sig[field] = float(m.group(1))
        m = re.search(r"unit[:\s]+([A-Za-z°%/]+)", text, re.IGNORECASE)
        if m:
            sig["unit"] = m.group(1)
        return sig
