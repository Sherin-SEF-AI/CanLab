"""The workspace bar, and the tab widget it drives without owning.

The bar is a sibling of the QTabWidget, not a subclass of it, and it both
drives and follows ``setCurrentIndex``. Everything that used to move the
current tab still does: the Alt+N actions, Ctrl+Tab, Ctrl+F, the View menu,
the acceptance scripts and both demo recorders. These tests exist to make sure
the bar keeps following them rather than quietly becoming the only way to
navigate.
"""
import pytest

pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QTabWidget

from canlab.ui.workspace_bar import WORKSPACES, WorkspaceBar, _first_word


@pytest.fixture(scope="module")
def window(qcore):
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    w.show()
    qcore.processEvents()
    yield w
    w.close()
    w.deleteLater()
    qcore.processEvents()


def settle(qcore, turns: int = 6):
    """Let deferred layout and mark placement run."""
    import time
    for _ in range(turns):
        qcore.processEvents()
        time.sleep(0.01)


# ── grouping ─────────────────────────────────────────────────────────────────

def test_every_tab_has_exactly_one_workspace(window):
    bar = window.workspace_bar
    homes = bar._index_of_ws
    assert len(homes) == window.tabs.count() == 16
    covered = sorted(i for ws in range(len(WORKSPACES)) for i in bar.tabs_in(ws))
    assert covered == list(range(16)), "a tab is in no workspace or in two"


def test_an_unmapped_tab_is_refused_rather_than_hidden(qcore):
    """A tab with no workspace would be unreachable from the bar, so the bar
    raises at construction instead of silently dropping it."""
    tabs = QTabWidget()
    from PyQt6.QtWidgets import QWidget
    tabs.addTab(QWidget(), "FRAMES")
    tabs.addTab(QWidget(), "SOMETHING NEW")
    with pytest.raises(ValueError, match="no workspace"):
        WorkspaceBar(tabs)
    tabs.deleteLater()


def test_the_grouping_keys_match_what_the_recorders_use(window):
    """Both the bar and docs/demo/record.py key off the first word of a tab
    label. If they ever diverge, one of them silently addresses the wrong
    page."""
    for i in range(window.tabs.count()):
        word = _first_word(window.tabs.tabText(i))
        assert window.workspace_bar._index_of_ws[i] is not None
        assert word == window.tabs.tabText(i).split()[0]


# ── following the tab widget ─────────────────────────────────────────────────

def test_selecting_a_tab_in_another_workspace_switches_workspace(window, qcore):
    """The ordinary case, not an edge case: Alt+1 is FRAMES in CAPTURE and
    Alt+5 is AI ENGINE in DETECT."""
    window.tabs.setCurrentIndex(0)
    settle(qcore)
    assert window.workspace_bar._active_ws == 0

    window.tabs.setCurrentIndex(4)          # AI ENGINE, in DETECT
    settle(qcore)
    assert window.workspace_bar._active_ws == 2
    assert window.tabs.currentWidget() is window.ai_tab

    window.tabs.setCurrentIndex(15)         # GATEWAY, in BUS
    settle(qcore)
    assert window.workspace_bar._active_ws == 4


def test_the_bar_shows_a_button_for_the_current_tab(window, qcore):
    for index in (0, 4, 8, 13, 15):
        window.tabs.setCurrentIndex(index)
        settle(qcore)
        bar = window.workspace_bar
        assert index in bar._tab_buttons, "current tab has no button in its row"
        assert bar._tab_buttons[index].isChecked()


def test_the_marks_follow_the_buttons(window, qcore):
    """Both indicators are positioned from real geometry. They used to be
    placed before the freshly rebuilt row had any, which parked them at x=0."""
    for index in (8, 0, 4, 13, 2):
        window.tabs.setCurrentIndex(index)
        settle(qcore)
        bar = window.workspace_bar
        ws_button = bar._ws_buttons[bar._active_ws]
        tab_button = bar._tab_buttons[index]
        assert bar._ws_mark.geometry().x() == ws_button.x()
        assert bar._tab_mark.geometry().x() == tab_button.x()
        assert bar._tab_mark.geometry().width() == tab_button.width()


def test_choosing_a_workspace_opens_its_first_tab(window, qcore):
    bar = window.workspace_bar
    for ws in range(len(WORKSPACES)):
        bar._on_workspace_clicked(ws)
        settle(qcore)
        assert window.tabs.currentIndex() == bar.tabs_in(ws)[0]
        assert bar._active_ws == ws


def test_workspace_cycling_wraps_both_ways(window, qcore):
    bar = window.workspace_bar
    bar._on_workspace_clicked(0)
    settle(qcore)
    bar.step_workspace(-1)
    settle(qcore)
    assert bar._active_ws == len(WORKSPACES) - 1
    bar.step_workspace(1)
    settle(qcore)
    assert bar._active_ws == 0


def test_a_tab_button_does_not_clear_its_workspace_button(window, qcore):
    """Both kinds of button share the bar as a parent. With Qt's auto
    exclusion that makes them one group, so choosing a tab would uncheck the
    workspace it belongs to."""
    window.tabs.setCurrentIndex(9)          # DIAGNOSTICS, in BUS
    settle(qcore)
    bar = window.workspace_bar
    assert bar._tab_buttons[9].isChecked()
    assert bar._ws_buttons[bar._active_ws].isChecked(), \
        "selecting a tab cleared its own workspace button"


# ── the tab widget is still the tab widget ───────────────────────────────────

def test_the_tab_bar_is_hidden_but_the_tab_widget_is_untouched(window):
    assert not window.tabs.tabBar().isVisible()
    assert type(window.tabs) is QTabWidget
    assert window.tabs.count() == 16
    assert window.tabs.tabText(0) == "FRAMES"


def test_programmatic_selection_still_drives_everything(window, qcore):
    """What Alt+N, Ctrl+Tab, Ctrl+F, the recorders and the acceptance scripts
    all do."""
    seen = []
    window.tabs.currentChanged.connect(seen.append)
    for index in (3, 11, 6):
        window.tabs.setCurrentIndex(index)
        settle(qcore)
        assert window.tabs.currentIndex() == index
    assert seen == [3, 11, 6]


def test_the_bar_costs_one_row(window):
    from canlab.ui.tokens import HEIGHT
    assert window.workspace_bar.height() == HEIGHT["bar"]
    # It must not widen the window: the strip scrolls instead.
    assert window.workspace_bar.minimumSizeHint().width() <= 1
