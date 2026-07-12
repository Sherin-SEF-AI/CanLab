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
    "theme",
    "mainwindow",
    "settings_dialog",
    "core.canid", "core.safety", "core.isotp", "core.uds", "core.rest_api",
    "core.gateway", "core.replay", "core.fuzzer", "core.injection",
    "core.plugin_loader", "core.community_sync", "core.bus_health",
    "tabs.frames_tab", "tabs.signals_tab", "tabs.plot_tab", "tabs.ai_engine_tab",
    "tabs.dbc_builder_tab", "tabs.code_gen_tab", "tabs.intelligence_tab",
    "tabs.injection_tab", "tabs.diagnostics_tab", "tabs.dashboard_tab",
    "tabs.auto_re_tab", "tabs.timeline_tab", "tabs.obd_dashboard_tab",
    "tabs.signal_intelligence_tab", "tabs.gateway_tab",
    "ui.compute_worker",
]


@pytest.mark.parametrize("modname", MODULES)
def test_module_imports(modname):
    importlib.import_module(modname)
