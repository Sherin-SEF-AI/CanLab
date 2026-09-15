"""Who owns an animation, and who is allowed to free it.

The suite segfaulted roughly one run in three, in whatever test happened to be
running when the event loop next turned. There was no Python traceback, because
there is no Python frame: the crash is a double free in Qt.

Every animation here was created parented to its target *and* started with
DeleteWhenStopped. That gives the object two owners. Destroying the widget
frees the animation, and the stop that destruction triggers frees it again.

The fix is one owner: parented to the target, reused rather than reallocated,
never self-deleting. These tests pin that, and they pin the two behaviours the
old code got right so the fix cannot regress them.
"""
import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QAbstractAnimation, QVariantAnimation
from PyQt6.QtWidgets import QLabel, QWidget

from canlab.ui.animations import CountUpLabel
from canlab.ui.motion import Motion, animate, interpolate


@pytest.fixture
def moving():
    """Turn motion on for a test, since the suite runs headless."""
    was = Motion._headless
    Motion._headless = False
    yield
    Motion._headless = was


# ── one animation per widget and property ────────────────────────────────────

def test_retargeting_reuses_the_same_animation_object(qcore, moving):
    widget = QWidget()
    widget.resize(100, 100)
    first = animate(widget, b"windowOpacity", 0.5)
    second = animate(widget, b"windowOpacity", 1.0)
    assert first is second, "a second call allocated a second animation"
    assert len(widget.findChildren(QAbstractAnimation)) == 1
    widget.deleteLater()
    qcore.processEvents()


def test_the_animation_belongs_to_the_widget_it_animates(qcore, moving):
    widget = QWidget()
    anim = animate(widget, b"windowOpacity", 0.5)
    assert anim.parent() is widget, \
        "an unparented animation outlives the widget it writes to"
    widget.deleteLater()
    qcore.processEvents()


def test_a_finished_callback_does_not_accumulate_across_calls(qcore, moving):
    """Reuse means the previous call's on_done is still connected unless it is
    dropped, which would fire every earlier callback on every later finish."""
    widget = QWidget()
    calls = []
    for i in range(3):
        animate(widget, b"windowOpacity", 0.1 * i, dur=1,
                on_done=lambda n=i: calls.append(n))
    anim = widget.findChild(QAbstractAnimation)
    anim.setCurrentTime(anim.duration())
    anim.stop()
    qcore.processEvents()
    assert calls.count(0) == 0 and calls.count(1) == 0, \
        f"superseded callbacks fired: {calls}"


def test_two_properties_get_two_animations(qcore, moving):
    widget = QWidget()
    animate(widget, b"windowOpacity", 0.5)
    animate(widget, b"geometry", widget.geometry())
    assert len(widget.findChildren(QAbstractAnimation)) == 2
    widget.deleteLater()
    qcore.processEvents()


def test_destroying_the_widget_takes_its_animation_with_it(qcore, moving):
    widget = QWidget()
    animate(widget, b"windowOpacity", 0.0, dur="slow")
    widget.deleteLater()
    qcore.processEvents()
    qcore.processEvents()          # nothing left to write to a dead object


# ── interpolate ──────────────────────────────────────────────────────────────

def test_interpolate_also_reuses_one_animation(qcore, moving):
    owner = QWidget()
    seen = []
    interpolate(owner, 0, 10, seen.append)
    interpolate(owner, 0, 20, seen.append)
    assert len(owner.findChildren(QVariantAnimation)) == 1
    owner.deleteLater()
    qcore.processEvents()


def test_a_superseded_interpolation_stops_writing(qcore, moving):
    """The second call must disconnect the first setter, or a cancelled
    animation keeps driving the value it was cancelled for."""
    owner = QWidget()
    first, second = [], []
    interpolate(owner, 0, 10, first.append, dur="slow")
    interpolate(owner, 0, 20, second.append, dur=1)
    anim = owner.findChild(QVariantAnimation)
    anim.setCurrentTime(anim.duration())
    anim.stop()
    qcore.processEvents()
    assert not first, "the superseded setter was still being driven"
    owner.deleteLater()
    qcore.processEvents()


# ── the count-up label ───────────────────────────────────────────────────────

def test_the_counter_reuses_its_animation(qcore, moving):
    label = CountUpLabel("0", "frames")
    label.animate_to(500)
    label.animate_to(900)
    assert len(label.findChildren(QVariantAnimation)) == 1
    label.deleteLater()
    qcore.processEvents()


def test_the_counter_counts_down_and_arrives(qcore):
    """It used to step by max(1, diff // 6): counting down, that moved one
    away from the target every tick and never stopped. A hundred thousand
    frames to zero would have taken about 27 minutes showing false numbers."""
    label = CountUpLabel("0", "frames")
    label.animate_to(100_000)
    qcore.processEvents()
    assert label._current == 100_000, "headless motion should land instantly"
    label.animate_to(0)
    qcore.processEvents()
    assert label._current == 0
    assert label.text() == "0 frames"
    label.deleteLater()
    qcore.processEvents()


def test_the_counter_lands_on_its_target_when_animated(qcore, moving):
    label = CountUpLabel("0", "frames")
    label.animate_to(4_200)
    anim = label.findChild(QVariantAnimation)
    anim.setCurrentTime(anim.duration())
    anim.stop()
    qcore.processEvents()
    assert label._current == 4_200
    label.deleteLater()
    qcore.processEvents()


def test_animating_to_the_current_value_is_a_no_op(qcore, moving):
    label = CountUpLabel("0", "frames")
    label.animate_to(0)
    anim = label.findChild(QVariantAnimation)
    assert anim is None or anim.state() != QAbstractAnimation.State.Running
    label.deleteLater()
    qcore.processEvents()


def test_a_plain_label_is_untouched(qcore):
    """CountUpLabel is a QLabel; nothing else should have gained an animation."""
    plain = QLabel("x")
    assert plain.findChild(QVariantAnimation) is None
    plain.deleteLater()
    qcore.processEvents()
