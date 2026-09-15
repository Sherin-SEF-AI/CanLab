"""The contracts the interface has to keep while it is being rebuilt.

Two of these exist because the thing they check was already broken:

The nine hardcoded `setCurrentIndex` calls were written when FRAMES was index
0 and SIGNALS was index 1. Inserting SNIFFER at index 1 shifted every tab after
it and moved none of them, so Tools > CAN Gateway opened ML INTEL, Export DBC
opened AI ENGINE, and the ID panel's Analyze with AI opened PLOT. Nothing
noticed, because every one of those handlers then drove the correct tab object
directly and the user only saw the wrong page.

`CountUpLabel` stepped by `max(1, diff // 6)`. Counting down, `diff` is
negative and that expression is 1, so the value walked away from its target on
every 16 ms tick and the timer never stopped. The status bar counted upward
forever showing a number that was false.

The rest pin what a UI rebuild must not break.
"""
import time

import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QMenu, QTabWidget

EXPECTED_TABS = [
    "FRAMES", "SNIFFER", "SIGNALS", "PLOT", "AI ENGINE", "DBC BUILDER",
    "CODE GEN", "INTELLIGENCE", "INJECTION", "DIAGNOSTICS", "DASHBOARD",
    "AUTO-RE", "TIMELINE", "OBD-II", "ML INTEL", "GATEWAY",
]

# Attribute names asserted by other tests, the acceptance scripts and both
# demo recorders. Renaming any of them breaks something that will not say so.
REQUIRED_ATTRS = [
    "tabs", "id_panel", "inspector", "adapter_combo",
    "_act_arm", "_act_rest", "_act_mcp", "_can_settings",
    "frames_tab", "sniffer_tab", "signals_tab", "plot_tab", "ai_tab",
    "dbc_tab", "codegen_tab", "intelligence_tab", "injection_tab",
    "diagnostics_tab", "dashboard_tab", "auto_re_tab", "timeline_tab",
    "obd_tab", "ml_intel_tab", "gateway_tab",
]


@pytest.fixture(scope="module")
def window(qcore):
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    yield w
    w.close()
    w.deleteLater()
    qcore.processEvents()


# ── navigation ───────────────────────────────────────────────────────────────

def test_every_menu_action_opens_the_tab_it_names(window, qcore, monkeypatch):
    """The regression that started this file: nine hardcoded indices.

    Only the navigation is exercised. Each handler's real work is stubbed out,
    because running it for real starts AI workers and opens modal dialogs that
    never return under an offscreen test.
    """
    for owner, name in [
        (window.ai_tab, "_add_all_unknown"), (window.ai_tab, "_run_queue"),
        (window.ai_tab, "queue_id"), (window.ai_tab, "_load_id"),
        (window.dbc_tab, "_export_dbc"), (window.obd_tab, "_discover_pids"),
        (window.plot_tab, "_highlight_id"),
    ]:
        if hasattr(owner, name):
            monkeypatch.setattr(owner, name, lambda *a, **k: None)

    cases = [
        ("_run_ai_re", "ai_tab", ()),
        ("_export_dbc", "dbc_tab", ()),
        ("_generate_code", "codegen_tab", ()),
        ("_obd_discover", "obd_tab", ()),
        ("_open_ml_intel", "ml_intel_tab", ()),
        ("_open_gateway", "gateway_tab", ()),
        ("_analyze_id", "ai_tab", ("0A6",)),
        ("_plot_id", "plot_tab", ("0A6",)),
    ]
    for method, attr, args in cases:
        window.tabs.setCurrentIndex(0)
        getattr(window, method)(*args)
        qcore.processEvents()
        assert window.tabs.currentWidget() is getattr(window, attr), (
            f"{method} opened {window.tabs.tabText(window.tabs.currentIndex())!r}, "
            f"not {attr}")


def test_goto_resolves_by_identity_not_by_number(window):
    """indexOf cannot drift when a tab is inserted; a literal can."""
    for attr in ("frames_tab", "gateway_tab", "obd_tab", "dbc_tab"):
        tab = getattr(window, attr)
        window._goto(tab)
        assert window.tabs.currentWidget() is tab


def test_no_hardcoded_tab_index_remains():
    """Guard the mechanism, not just today's symptom."""
    import pathlib
    import re
    src = pathlib.Path("canlab/mainwindow.py").read_text()
    literal = re.findall(r"tabs\.setCurrentIndex\(\s*\d+\s*\)", src)
    assert not literal, f"hardcoded tab indices are back: {literal}"


# ── the shape the tests and recorders depend on ──────────────────────────────

def test_tabs_is_a_plain_tab_widget_in_a_fixed_order(window):
    # test_smoke_mainwindow does findChild(type(window.tabs)) to reach the
    # inner QTabWidget of DIAGNOSTICS. A subclass here returns None there.
    assert type(window.tabs) is QTabWidget
    assert window.tabs.count() == 16
    # Compare without decoration: six labels currently carry a trailing star,
    # which is cosmetic and due to be dropped. What must not move is the order
    # and identity, because Alt+1..0 and both recorders index into it.
    labels = [window.tabs.tabText(i).replace("★", "").strip() for i in range(16)]
    assert labels == EXPECTED_TABS


def test_the_attribute_names_other_code_reaches_for_still_exist(window):
    missing = [a for a in REQUIRED_ATTRS if not hasattr(window, a)]
    assert not missing, f"missing: {missing}"


def test_recorders_can_still_address_every_tab_by_first_word(window):
    """Both demo recorders do tabText(i).split()[0] and raise KeyError on a
    miss. Duplicate first words would make a lookup ambiguous."""
    firsts = [window.tabs.tabText(i).split()[0] for i in range(window.tabs.count())]
    duplicated = {f for f in firsts if firsts.count(f) > 1}
    assert not duplicated, f"ambiguous for the recorders: {duplicated}"


def test_shortcuts_stay_discoverable_and_unique(window):
    holders = [window] + window.menuBar().findChildren(QMenu)
    actions = [a for h in holders for a in h.actions() if not a.shortcut().isEmpty()]
    seqs = [a.shortcut().toString() for a in actions]
    assert len(seqs) >= 18, f"only {len(seqs)} shortcuts found"
    duplicated = {s for s in seqs if seqs.count(s) > 1}
    assert not duplicated, f"duplicate shortcuts: {duplicated}"


def test_the_window_still_fits_a_laptop(window, qcore):
    """The budget every layout change spends from."""
    window.show()
    for i in range(window.tabs.count()):
        window.tabs.setCurrentIndex(i)
        qcore.processEvents()
    floor = window.minimumSizeHint()
    assert floor.width() <= 1440, f"minimum width {floor.width()} px"
    assert floor.height() <= 900, f"minimum height {floor.height()} px"


# ── the theme's public surface ───────────────────────────────────────────────

def test_theme_exports_what_the_recorders_and_tabs_import():
    from canlab import theme
    for name in ("QSS", "COLORS", "mono_font", "ui_font", "dot_icon", "desc_label"):
        assert hasattr(theme, name), f"canlab.theme lost {name}"
    # Every key read anywhere in the widget layer, including the four that
    # drive the generated-code syntax highlighter.
    for key in ("bg", "panel_bg", "border", "text", "dim", "green", "amber",
                "error", "accent", "selection_bg", "row_alt",
                "keyword", "string", "comment", "function"):
        assert key in theme.COLORS, f"COLORS lost {key!r}"


def test_every_colour_lookup_in_the_app_resolves():
    """COLORS["fg"] was read in intelligence_tab and is not a key, so the
    change-on-action delta table raised KeyError every time it rendered:
    dict.get(k, default) evaluates its default eagerly."""
    import pathlib
    import re

    from canlab.theme import COLORS
    used = set()
    for path in pathlib.Path("canlab").rglob("*.py"):
        used |= set(re.findall(r'COLORS\[[\'"]([a-z_]+)[\'"]\]', path.read_text()))
    missing = sorted(used - set(COLORS))
    assert not missing, f"COLORS lookups that would raise KeyError: {missing}"


# ── the counter ──────────────────────────────────────────────────────────────

def test_the_frame_counter_converges_in_both_directions(qcore):
    from canlab.ui.animations import CountUpLabel
    lbl = CountUpLabel("0", suffix="frames")
    lbl._show(500_000)

    def settle(ms=700):
        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            qcore.processEvents()
            time.sleep(0.005)

    lbl.animate_to(5_000)
    settle()
    assert lbl._current == 5_000, f"counting down landed on {lbl._current}"

    lbl.animate_to(120_000)
    settle()
    assert lbl._current == 120_000

    lbl.animate_to(0)
    settle()
    assert lbl._current == 0
    lbl.deleteLater()


def test_the_frame_counter_survives_being_re_targeted_mid_flight(qcore):
    from canlab.ui.animations import CountUpLabel
    lbl = CountUpLabel("0", suffix="frames")
    lbl._show(100_000)
    for target in (50_000, 900, 73_400, 12):
        lbl.animate_to(target)          # each cancels the one before
        qcore.processEvents()
    end = time.monotonic() + 0.8
    while time.monotonic() < end:
        qcore.processEvents()
        time.sleep(0.005)
    assert lbl._current == 12
    assert lbl.text() == "12 frames"
    lbl.deleteLater()
