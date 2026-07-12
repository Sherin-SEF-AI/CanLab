import anthropic
import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

GROQ_DEFAULT_MODEL      = "llama-3.3-70b-versatile"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
OLLAMA_DEFAULT_MODEL    = "llama3.1"


SYSTEM_PROMPT = """You are an expert automotive CAN bus reverse engineer specializing in Hyundai/Kia vehicles.
Analyze CAN frame data and identify signals. The vehicle is a Hyundai Kona.
Known Hyundai CAN characteristics: 500kbps bus speed, little-endian default,
many signals use rolling counters in the upper nibble of byte 0,
checksums often in byte 7. Reference hyundai_kia_generic.dbc patterns.
Format your response with clear sections:

SIGNAL IDENTIFICATION
BYTE MAPPING
SCALING & UNITS
CONFIDENCE (0-100%)
RECOMMENDED DBC ENTRY"""

BYTE_COLS = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]


def build_prompt(
    id_hex: str,
    frames_df: pd.DataFrame,
    context: str = "",
    event_correlations: list = None,
    repo_context: dict = None,
    ml_insights: str = "",
) -> str:
    lines = [f"CAN ID: 0x{id_hex}  ({int(id_hex, 16)} decimal)"]
    lines.append(f"Frame count: {len(frames_df)}")

    # Repo context block — gives Claude vehicle/project knowledge
    if repo_context:
        repo_name = repo_context.get("repo", "")
        repo_desc = repo_context.get("description", "")
        readme_snippet = repo_context.get("readme_snippet", "")
        if repo_name:
            lines.append(f"\nSource repository: {repo_context.get('owner','')}/{repo_name}")
        if repo_desc:
            lines.append(f"Repo description: {repo_desc}")
        if readme_snippet:
            lines.append(f"\nRepository notes (README excerpt):\n{readme_snippet}")

    if not frames_df.empty:
        total_time = frames_df["Timestamp"].iloc[-1] - frames_df["Timestamp"].iloc[0]
        freq = len(frames_df) / total_time if total_time > 0 else 0
        lines.append(f"\nFrequency: {freq:.1f} Hz")

        lines.append("\nByte statistics (min/max/mean/entropy):")
        for col in BYTE_COLS:
            if col not in frames_df.columns:
                continue
            s = frames_df[col].dropna()
            if s.empty:
                continue
            import numpy as np
            counts = np.bincount(s.astype(int), minlength=256)
            probs = counts / counts.sum()
            probs = probs[probs > 0]
            ent = float(-np.sum(probs * np.log2(probs)))
            lines.append(
                f"  {col}: min={int(s.min())} max={int(s.max())} "
                f"mean={s.mean():.1f} entropy={ent:.2f}"
            )

        lines.append("\nLast 20 frames (hex):")
        last20 = frames_df.tail(20)
        for _, row in last20.iterrows():
            byte_str = " ".join(
                format(int(row[col]), "02X") if pd.notna(row.get(col)) else "--"
                for col in BYTE_COLS
            )
            ts = row.get("Timestamp", 0)
            lines.append(f"  [{ts:.3f}] {byte_str}")

    if ml_insights.strip():
        lines.append(
            "\n=== ML PRE-ANALYSIS (factual — derived offline, not inferred) ==="
        )
        lines.append(ml_insights.strip())
        lines.append("=== END ML PRE-ANALYSIS ===")

    if event_correlations:
        lines.append("\nCorrelated events (this ID changed near these timestamps):")
        for evt in event_correlations:
            lines.append(f"  - {evt}")

    if context.strip():
        lines.append(f"\nUser context: {context.strip()}")

    return "\n".join(lines)


def _readme_snippet(readme: str, max_chars: int = 800) -> str:
    """Return the most annotation-rich portion of a README."""
    if not readme:
        return ""
    lines = readme.splitlines()
    # Prefer lines with TS / timestamp markers
    ts_lines = [l for l in lines if "ts" in l.lower() or "timestamp" in l.lower()
                or any(c.isdigit() for c in l[:10])]
    if ts_lines:
        snippet = "\n".join(ts_lines[:40])
    else:
        snippet = "\n".join(lines[:40])
    return snippet[:max_chars]


class AIWorker(QThread):
    chunk_received = pyqtSignal(str)
    finished       = pyqtSignal(str)
    error          = pyqtSignal(str)

    def __init__(
        self,
        api_key:    str,
        id_hex:     str,
        frames_df:  pd.DataFrame,
        context:    str = "",
        event_correlations: list = None,
        repo_context: dict = None,
        provider:   str = "Anthropic",
        model:      str = "",
        groq_key:   str = "",
        ml_insights: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.api_key            = api_key
        self.id_hex             = id_hex
        self.frames_df          = frames_df
        self.context            = context
        self.event_correlations = event_correlations or []
        self.repo_context       = repo_context
        self.provider           = provider
        self.model              = model or {
            "Groq":   GROQ_DEFAULT_MODEL,
            "Ollama": OLLAMA_DEFAULT_MODEL,
        }.get(provider, ANTHROPIC_DEFAULT_MODEL)
        self.groq_key           = groq_key
        self.ml_insights        = ml_insights
        self._full_response     = ""

    def run(self):
        if self.provider == "Groq":
            self._run_groq()
        elif self.provider == "Ollama":
            self._run_ollama()
        else:
            self._run_anthropic()

    def _build_context(self):
        rc = None
        if self.repo_context:
            rc = dict(self.repo_context)
            rc["readme_snippet"] = _readme_snippet(rc.get("readme", ""))
        return build_prompt(
            self.id_hex, self.frames_df,
            self.context, self.event_correlations,
            repo_context=rc,
            ml_insights=self.ml_insights,
        )

    def _run_anthropic(self):
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            prompt = self._build_context()
            with client.messages.stream(
                model=self.model,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    self._full_response += text
                    self.chunk_received.emit(text)
            self.finished.emit(self._full_response)
        except anthropic.AuthenticationError:
            self.error.emit("Invalid Anthropic API key. Check Settings > API Keys.")
        except anthropic.RateLimitError:
            self.error.emit("Anthropic rate limit exceeded. Wait a moment and retry.")
        except Exception as e:
            self.error.emit(str(e))

    def _run_ollama(self):
        """Stream from a local Ollama server (fully offline, no API key)."""
        import os, json, requests
        base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        try:
            prompt = self._build_context()
            resp = requests.post(
                f"{base}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "stream": True,
                },
                stream=True, timeout=180,
            )
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                text = obj.get("message", {}).get("content", "")
                if text:
                    self._full_response += text
                    self.chunk_received.emit(text)
                if obj.get("done"):
                    break
            self.finished.emit(self._full_response)
        except requests.ConnectionError:
            self.error.emit(
                f"Cannot reach Ollama at {base}. Start it with 'ollama serve' "
                f"and pull a model (e.g. 'ollama pull {self.model}')."
            )
        except Exception as e:
            self.error.emit(f"Ollama error: {e}")

    def _run_groq(self):
        try:
            from groq import Groq
            client = Groq(api_key=self.groq_key)
            prompt = self._build_context()
            stream = client.chat.completions.create(
                model=self.model,
                max_tokens=1500,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                stream=True,
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content or ""
                if text:
                    self._full_response += text
                    self.chunk_received.emit(text)
            self.finished.emit(self._full_response)
        except Exception as e:
            err = str(e)
            if "401" in err or "invalid_api_key" in err.lower():
                self.error.emit("Invalid Groq API key. Check Settings > API Keys.")
            elif "429" in err:
                self.error.emit("Groq rate limit exceeded. Wait a moment and retry.")
            else:
                self.error.emit(f"Groq error: {err}")
