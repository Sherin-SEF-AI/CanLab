"""The OpenAI provider in the AI engine, against a stand-in for the SDK.

No network: a fake ``openai`` module is installed for the test so the worker's
streaming loop, its error mapping, and the tab's key selection are what is
checked.
"""
import sys
import types

import pytest

pytest.importorskip("PyQt6")

import pandas as pd


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content, empty=False):
        self.choices = [] if empty else [_Choice(content)]


class _Completions:
    last_kwargs = None
    fail = None

    def create(self, **kw):
        _Completions.last_kwargs = kw
        if _Completions.fail:
            raise _Completions.fail
        return iter([_Chunk("SIGNAL ", ), _Chunk(None, empty=True), _Chunk("IDENTIFICATION")])


class _Chat:
    completions = _Completions()


class _Client:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.chat = _Chat()


@pytest.fixture
def fake_openai(monkeypatch):
    mod = types.ModuleType("openai")
    mod.OpenAI = _Client

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class NotFoundError(Exception):
        pass
    mod.AuthenticationError = AuthenticationError
    mod.RateLimitError = RateLimitError
    mod.NotFoundError = NotFoundError
    monkeypatch.setitem(sys.modules, "openai", mod)
    _Completions.fail = None
    _Completions.last_kwargs = None
    return mod


def _frames():
    return pd.DataFrame({"Timestamp": [0.0, 0.01, 0.02], "ID": ["1A0"] * 3,
                         **{f"B{i}": [i, i + 1, i + 2] for i in range(8)}})


def test_openai_worker_streams(qcore, fake_openai):
    from canlab.core.ai_client import AIWorker
    w = AIWorker(api_key="", id_hex="1A0", frames_df=_frames(), provider="OpenAI",
                 model="", openai_key="sk-test")
    assert w.model == "gpt-5"
    chunks, done, errors = [], [], []
    w.chunk_received.connect(chunks.append)
    w.finished.connect(done.append)
    w.error.connect(errors.append)
    w.run()                                   # synchronously, on this thread
    assert chunks == ["SIGNAL ", "IDENTIFICATION"] and done == ["SIGNAL IDENTIFICATION"]
    assert not errors
    kw = _Completions.last_kwargs
    assert kw["model"] == "gpt-5" and kw["stream"] is True
    assert "max_completion_tokens" in kw and "max_tokens" not in kw
    assert kw["messages"][0]["role"] == "system" and "CAN ID: 0x1A0" in kw["messages"][1]["content"]


def test_openai_worker_maps_errors(qcore, fake_openai):
    from canlab.core.ai_client import AIWorker
    for exc, text in ((fake_openai.AuthenticationError(), "Invalid OpenAI API key"),
                      (fake_openai.RateLimitError(), "rate limit"),
                      (fake_openai.NotFoundError(), "does not know the model"),
                      (RuntimeError("boom"), "OpenAI error: boom")):
        _Completions.fail = exc
        w = AIWorker(api_key="", id_hex="1A0", frames_df=_frames(), provider="OpenAI",
                     model="gpt-x", openai_key="k")
        errors = []
        w.error.connect(errors.append)
        w.run()
        assert errors and text in errors[0]


def test_ai_tab_picks_the_provider_key(qcore):
    from canlab.tabs.ai_engine_tab import AIEngineTab
    tab = AIEngineTab()
    tab.set_ai_config("OpenAI", "gpt-5", groq_key="g", api_key="a", openai_key="o")
    assert tab._active_key() == "o"
    tab.set_ai_config("Groq", "m", groq_key="g", api_key="a", openai_key="o")
    assert tab._active_key() == "g"
    tab.set_ai_config("Anthropic", "m", groq_key="g", api_key="a", openai_key="o")
    assert tab._active_key() == "a"
    tab.set_ai_config("Ollama", "m")
    assert tab._active_key() == ""
    assert "OpenAI" not in tab.lbl_provider_badge.text()
    tab.deleteLater()
