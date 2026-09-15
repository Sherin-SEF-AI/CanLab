"""Collapsible sidebars, the compact widgets, and the command palette.

The panels are here because they were advertised as resizable and never were:
they sat in a plain QHBoxLayout with a minimum and a maximum width, and the
``resize()`` calls in both constructors did nothing, because a widget in a
layout is sized by the layout.
"""
import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QLabel, QSplitter

from canlab.ui.command_palette import collect, score
from canlab.ui.motion import Motion, SplitterAnimator
from canlab.ui.widgets import Section, StatusLabel, Toolstrip, capped, set_status


@pytest.fixture(scope="module")
def window(qcore):
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    w.show()
    # A splitter can only hand out the width it has. At the window's minimum
    # size the panes get squeezed and every width assertion below would be
    # measuring the squeeze rather than the behaviour.
    w.resize(1600, 900)
    qcore.processEvents()
    yield w
    w.close()
    w.deleteLater()
    qcore.processEvents()


# ── side panels ──────────────────────────────────────────────────────────────

def test_the_panels_are_in_a_real_splitter(window):
    assert isinstance(window._split, QSplitter)
    assert window._split.count() == 3
    # The panel objects other code reaches for are unchanged.
    assert window._split.widget(0) is window._id_host
    assert window._id_host.panel is window.id_panel
    assert window._insp_host.panel is window.inspector


def test_a_panel_can_be_dragged_which_it_never_could(window, qcore):
    # setSizes is a request: the splitter also has to place its handles, so
    # the widths come back within a few pixels rather than exactly.
    window.resize(1600, 900)
    qcore.processEvents()
    window._split.setSizes([300, 1, 200])
    qcore.processEvents()
    left, _mid, right = window._split.sizes()
    assert abs(left - 300) <= 8 and abs(right - 200) <= 8


def test_collapse_goes_all_the_way_to_zero_and_comes_back(window, qcore):
    """A splitter clamps a pane to its child's minimumWidth, so without the
    host wrapper this would stop at 150 and then jump."""
    window.resize(1600, 900)
    window._split.setSizes([220, 1, 280])
    qcore.processEvents()
    assert window.panel_open("left")

    window._toggle_panel("left", instant=True)
    qcore.processEvents()
    assert not window.panel_open("left")
    assert window._split.sizes()[0] == 0

    window._toggle_panel("left", instant=True)
    qcore.processEvents()
    assert window.panel_open("left")
    assert abs(window._split.sizes()[0] - 220) <= 8, \
        "the old width was not remembered"


def test_maximise_hides_both_and_restores_both(window, qcore):
    window._split.setSizes([220, 1, 280])
    qcore.processEvents()
    window._toggle_both_panels()
    qcore.processEvents()
    assert not window.panel_open("left") and not window.panel_open("right")
    window._toggle_both_panels()
    qcore.processEvents()
    assert window.panel_open("left") and window.panel_open("right")


def test_the_centre_can_never_be_collapsed(window):
    assert not window._split.isCollapsible(1), "the pages could be hidden entirely"


def test_panel_state_survives_a_save_and_restore(window, qcore):
    import json

    from PyQt6.QtCore import QSettings
    window.resize(1600, 900)
    qcore.processEvents()
    window._split.setSizes([260, 1, 300])
    qcore.processEvents()
    window._toggle_panel("right", instant=True)      # collapse one
    qcore.processEvents()
    window._save_geometry()

    saved = json.loads(QSettings("CanLab", "CanLab").value(window.PANELS_KEY, "{}", str))
    assert saved["left_open"] is True
    assert saved["right_open"] is False
    assert abs(saved["right_width"] - 300) <= 8, \
        "the width to come back to was lost"

    window._toggle_panel("right", instant=True)
    qcore.processEvents()


def test_the_splitter_animator_moves_width_between_panes(qcore):
    sp = QSplitter()
    for _ in range(3):
        sp.addWidget(QLabel("x"))
    sp.resize(600, 100)
    sp.setSizes([200, 200, 200])
    before = sp.sizes()
    anim = SplitterAnimator(sp, 0, 1)
    assert anim.width() == before[0]
    anim.set_width(50)
    sizes = sp.sizes()
    assert sizes[0] == 50
    assert sizes[1] == before[1] + (before[0] - 50), \
        "the difference did not go to the stretching pane"
    assert sum(sizes) == sum(before), "width was created or destroyed"
    sp.deleteLater()


# ── compact widgets ──────────────────────────────────────────────────────────

def test_status_is_a_property_not_a_stylesheet(qcore):
    label = QLabel("x")
    set_status(label, "ok")
    assert label.property("state") == "ok"
    assert label.styleSheet() == "", "state should not be written as a stylesheet"
    set_status(label, "error")
    assert label.property("state") == "error"
    label.deleteLater()


def test_status_label_says_and_recolours(qcore):
    label = StatusLabel("waiting")
    label.say("connected", "ok")
    assert label.text() == "connected"
    assert label.property("state") == "ok"
    assert label.wordWrap(), "long status text must wrap or it widens its tab"
    label.deleteLater()


def test_a_section_folds_and_unfolds(qcore):
    section = Section("GROUP")
    section.add(QLabel("a"), QLabel("b"))
    assert not section.collapsed
    section.set_collapsed(True, instant=True)
    assert section.collapsed and section.body.maximumHeight() == 0
    section.set_collapsed(False, instant=True)
    assert not section.collapsed and section.body.maximumHeight() > 0
    section.deleteLater()


def test_toolstrip_is_one_row_high(qcore):
    from canlab.ui.tokens import HEIGHT
    strip = Toolstrip().add("Label", QLabel("x")).stretch()
    assert strip.height() == HEIGHT["bar"]
    strip.deleteLater()


def test_capped_uses_the_shared_scale(qcore):
    from canlab.ui.tokens import MAX_H
    for name, value in MAX_H.items():
        assert capped(QLabel(), name).maximumHeight() == value


# ── command palette ──────────────────────────────────────────────────────────

def test_the_palette_finds_every_tab_and_pane(window):
    commands = collect(window)
    labels = [c.label for c in commands]
    for name in ("FRAMES", "SNIFFER", "GATEWAY", "DBC BUILDER"):
        assert f"Go to {name}" in labels
    # Sub-tabs too: 33 of them live two levels down.
    assert "Go to INJECTION > TEST SEQUENCE" in labels
    assert "Go to DIAGNOSTICS > XCP" in labels
    assert len(commands) > 60


def test_the_palette_carries_the_menu_actions_and_their_keys(window):
    commands = {c.label: c for c in collect(window)}
    arm = next((c for label, c in commands.items() if "Arm / Disarm" in label), None)
    assert arm is not None and arm.hint == "Ctrl+E"


def test_matching_prefers_word_starts_and_runs(window):
    assert score("", "anything") == 0
    assert score("zzz", "Go to FRAMES") is None
    # A consecutive, word-initial match beats the same letters scattered.
    tight = score("trim", "Tools > Trim capture…")
    loose = score("trim", "Detect multiplexed signals in the traffic frames")
    assert tight is not None and (loose is None or tight > loose)


def test_selecting_a_palette_result_actually_navigates(window, qcore):
    from canlab.ui.command_palette import CommandPalette
    window.tabs.setCurrentIndex(0)
    palette = CommandPalette(window)
    palette.query.setText("Go to GATEWAY")
    qcore.processEvents()
    assert palette.results.count() > 0
    command = palette.results.item(0).data(
        __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ItemDataRole.UserRole)
    command.run()
    qcore.processEvents()
    assert window.tabs.currentWidget() is window.gateway_tab
    palette.deleteLater()


# ── the motion gates ─────────────────────────────────────────────────────────

def test_motion_is_off_when_headless(qcore):
    """The suite and both demo recorders run offscreen, so no test waits on an
    easing curve and no screenshot is caught mid-transition."""
    assert Motion._headless is True
    assert Motion.off() is True


def test_capture_suppresses_decoration_but_not_safety_signals():
    was = Motion.busy
    try:
        Motion._headless = False
        Motion.reduce = False
        Motion.set_capturing(True)
        assert Motion.off() is True, "decorative motion should stand down"
        assert Motion.off(critical=True) is False, \
            "the armed and connected indicators must keep moving"
        Motion.set_capturing(False)
        assert Motion.off() is False
    finally:
        Motion._headless = True
        Motion.busy = was


def test_reduce_motion_is_remembered(window, qcore):
    from PyQt6.QtCore import QSettings
    window._act_reduce_motion.setChecked(True)
    qcore.processEvents()
    assert Motion.reduce is True
    assert QSettings("CanLab", "CanLab").value("ui/reduce_motion", False, bool) is True
    window._act_reduce_motion.setChecked(False)
    qcore.processEvents()
    assert Motion.reduce is False
