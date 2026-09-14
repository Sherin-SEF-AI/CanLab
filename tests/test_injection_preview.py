"""The inject page: the value slider, the frame preview and the transmit log.

The page used to be four rows of controls above nine hundred pixels of black,
and the docstring claimed the space went to a log that did not exist. It does
now, and building it turned up two bugs in the controls above it.
"""
import pytest

pytest.importorskip("PyQt6")

from canlab.core.state import get_state

SIGNAL = {
    "message_id": "9FD0223", "message_name": "WIND_DATA",
    "signal_name": "WIND_SPEED", "start_bit": 8, "length": 16,
    "byte_order": "little", "value_type": "unsigned",
    "scale": 0.01, "offset": 0.0, "min_val": 0.0, "max_val": 100.0,
    "unit": "m/s",
}


@pytest.fixture
def tab(qcore):
    from canlab.tabs.injection_tab import InjectionTab
    state = get_state()
    state.dbc_signals.clear()
    widget = InjectionTab()
    widget.resize(1200, 800)
    widget.show()
    state.add_dbc_signal(dict(SIGNAL))
    qcore.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    state.dbc_signals.clear()
    qcore.processEvents()


# ── the value control ────────────────────────────────────────────────────────

def test_the_first_signal_added_applies_its_range_and_unit(tab):
    """currentIndexChanged cannot fire while the combo's signals are blocked,
    so the very first signal used to leave the box on its default range."""
    assert tab.lbl_unit.text() == "m/s"
    assert tab.val_spin.maximum() == pytest.approx(100.0)


@pytest.mark.parametrize("value", [0.0, 12.5, 88.25, 100.0])
def test_a_typed_value_survives_the_round_trip_through_the_slider(tab, qcore, value):
    """The slider was hardwired to plus or minus 100.00 in hundredths. Typing
    1450 into the box drove it to its stop, and the stop drove the box back
    down to 100: the value you asked for was silently replaced."""
    tab.val_spin.setValue(value)
    qcore.processEvents()
    assert tab.val_spin.value() == pytest.approx(value, abs=0.2)


def test_the_slider_spans_the_signal_not_a_fixed_range(tab, qcore):
    tab.val_slider.setValue(tab.SLIDER_STEPS)
    qcore.processEvents()
    assert tab.val_spin.value() == pytest.approx(tab.val_spin.maximum(), abs=0.2)
    tab.val_slider.setValue(0)
    qcore.processEvents()
    assert tab.val_spin.value() == pytest.approx(tab.val_spin.minimum(), abs=0.2)


def test_a_signal_with_no_declared_span_does_not_divide_by_zero(tab, qcore):
    get_state().add_dbc_signal(
        dict(SIGNAL, signal_name="FLAT", min_val=0.0, max_val=0.0))
    qcore.processEvents()
    tab.sig_combo.setCurrentIndex(tab.sig_combo.count() - 1)
    qcore.processEvents()
    tab.val_spin.setValue(1.0)
    qcore.processEvents()          # no ZeroDivisionError


# ── the frame preview ────────────────────────────────────────────────────────

def test_the_preview_packs_the_value_the_controls_describe(tab, qcore):
    tab.val_spin.setValue(0.77)
    qcore.processEvents()
    packed = [tab.preview_bytes.item(0, c).text()
              for c in range(tab.preview_bytes.columnCount())]
    # 0.77 m/s at 0.01 per bit is 77, which is 0x4D, in byte 1 little-endian.
    assert packed[1] == "4D" and packed[2] == "00"
    assert packed[0] == "00", "the signal must not spill into byte 0"


def test_the_preview_describes_the_layout_in_words(tab, qcore):
    tab.val_spin.setValue(0.77)
    qcore.processEvents()
    text = tab.lbl_preview.text()
    assert "WIND_SPEED" in text
    assert "bits 8..23" in text
    assert "little-endian" in text
    assert "m/s" in text


def test_the_preview_does_not_claim_a_checksum_that_was_never_written(tab, qcore):
    """The generic profile defines neither a counter nor a checksum. Saying
    they were applied, while every byte came back unchanged, is simply false."""
    tab.chk_checksum.setChecked(True)
    qcore.processEvents()
    text = tab.lbl_preview.text()
    assert "wrote" not in text
    assert "profile:" in text


def test_the_preview_says_so_when_nothing_is_selected(qcore):
    from canlab.tabs.injection_tab import InjectionTab
    get_state().dbc_signals.clear()
    widget = InjectionTab()
    widget.show()
    qcore.processEvents()
    assert "Select a signal" in widget.lbl_preview.text()
    assert widget.preview_bytes.item(0, 0).text() == "--"
    widget.deleteLater()
    qcore.processEvents()


# ── the transmit log ─────────────────────────────────────────────────────────

def test_the_log_records_a_send_newest_first(tab):
    tab._log_tx(0x123, b"\xDE\xAD", "sent", True)
    tab._log_tx(0x456, b"\xBE\xEF", "sent", True)
    assert tab.tx_log.rowCount() == 2
    assert tab.tx_log.item(0, 1).text() == "0x456", "newest should be on top"
    assert tab.tx_log.item(1, 2).text() == "DE AD"


def test_the_log_records_a_refusal_as_well_as_a_send(tab):
    tab._log_tx(0x123, b"\x00", "bus is not armed", False)
    assert tab.tx_log.item(0, 3).text() == "bus is not armed"


def test_the_log_cannot_grow_without_limit(tab):
    """A ten millisecond loop would otherwise add a hundred rows a second for
    as long as it runs."""
    for i in range(tab.TX_LOG_ROWS + 25):
        tab._log_tx(i, b"\x01", "sent", True)
    assert tab.tx_log.rowCount() == tab.TX_LOG_ROWS
