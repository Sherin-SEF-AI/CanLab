import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

GROQ_DEFAULT_MODEL      = "llama-3.3-70b-versatile"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5"
OLLAMA_DEFAULT_MODEL    = "llama3.1"


SYSTEM_PROMPT = """You are an expert automotive CAN bus reverse engineer.
Analyse the CAN frame data given and identify signals. The vehicle is unknown
unless the user says otherwise: do not assume a manufacturer, and say so when
the data is consistent with more than one interpretation.
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
    ml_insights: str = "",
) -> str:
    lines = [f"CAN ID: 0x{id_hex}  ({int(id_hex, 16)} decimal)"]
    lines.append(f"Frame count: {len(frames_df)}")

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

    if context.strip():
        lines.append(f"\nUser context: {context.strip()}")

    return "\n".join(lines)


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
        self.provider           = provider
        self.model              = model or {
            "Groq":   GROQ_DEFAULT_MODEL,
            "Ollama": OLLAMA_DEFAULT_MODEL,
        }.get(provider, ANTHROPIC_DEFAULT_MODEL)
        self.groq_key           = groq_key
        self.ml_insights        = ml_insights
        self._full_response     = ""
        self._stopped           = False

    def stop(self):
        """Ask the worker to stop streaming (the request itself is not cancellable)."""
        self._stopped = True
        self.requestInterruption()
        self.wait(3000)

    def run(self):
        if self.provider == "Groq":
            self._run_groq()
        elif self.provider == "Ollama":
            self._run_ollama()
        else:
            self._run_anthropic()

    def _system_prompt(self) -> str:
        """The base prompt plus the selected profile's framing hint (if any)."""
        try:
            from canlab.core.vehicle_profile import active_profile
            hint = active_profile().ai_hint
        except Exception:
            hint = ""
        return f"{SYSTEM_PROMPT}\n\nVehicle profile: {hint}" if hint else SYSTEM_PROMPT

    def _build_context(self):
        return build_prompt(
            self.id_hex, self.frames_df, self.context,
            ml_insights=self.ml_insights,
        )

    def _run_anthropic(self):
        # Imported here, not at module scope: Ollama and Groq users should not
        # need the Anthropic SDK installed to run the app.
        import anthropic
        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            prompt = self._build_context()
            with client.messages.stream(
                model=self.model,
                max_tokens=4000,
                system=self._system_prompt(),
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    if self._stopped:
                        break
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
                        {"role": "system", "content": self._system_prompt()},
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
                    {"role": "system", "content": self._system_prompt()},
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
