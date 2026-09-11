"""Import smoke test: every UI module must import cleanly.

Catches syntax errors, bad imports, and undefined module-level names across the
whole tab layer. Requires PyQt6 (and the plotting stack), so it's skipped where
those aren't installed. Modules are imported, not instantiated (no QApplication
needed); run under QT_QPA_PLATFORM=offscreen in CI.
"""
import importlib

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

MODULES = [
    "canlab.theme",
    "canlab.mainwindow",
    "canlab.settings_dialog",
    "canlab.core.canid", "canlab.core.safety", "canlab.core.isotp", "canlab.core.uds", "canlab.core.rest_api",
    "canlab.core.gateway", "canlab.core.replay", "canlab.core.fuzzer", "canlab.core.injection",
    "canlab.core.plugin_loader", "canlab.core.bus_health",
    "canlab.tabs.frames_tab", "canlab.tabs.signals_tab", "canlab.tabs.plot_tab", "canlab.tabs.ai_engine_tab",
    "canlab.tabs.dbc_builder_tab", "canlab.tabs.code_gen_tab", "canlab.tabs.intelligence_tab",
    "canlab.tabs.injection_tab", "canlab.tabs.diagnostics_tab", "canlab.tabs.dashboard_tab",
    "canlab.tabs.auto_re_tab", "canlab.tabs.timeline_tab", "canlab.tabs.obd_dashboard_tab",
    "canlab.tabs.signal_intelligence_tab", "canlab.tabs.gateway_tab",
    "canlab.ui.compute_worker",
]


@pytest.mark.parametrize("modname", MODULES)
def test_module_imports(modname):
    importlib.import_module(modname)
