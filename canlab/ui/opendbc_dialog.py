"""Pick a matched opendbc database and apply it."""
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                             QListWidget, QListWidgetItem, QVBoxLayout)

from canlab.theme import mono_font


class OpendbcMatchDialog(QDialog):
    """Matches ranked by ID overlap, with an Apply button.

    ``chosen`` is the DBC file name after accept, ``only_seen`` whether to
    restrict the import to messages present in the capture.
    """

    def __init__(self, matches: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("opendbc matches")
        self.setMinimumWidth(560)
        self.chosen: str | None = None
        self.only_seen = True

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Ranked by how many of this capture's IDs each "
                             "database explains. Apply loads its signals into "
                             "the DBC builder."))
        self._list = QListWidget()
        self._list.setFont(mono_font())
        for m in matches:
            cov = m.get("coverage")
            cov_txt = f"  covers {cov:.0%} of the bus" if cov is not None else ""
            item = QListWidgetItem(
                f"{m['score'] * 100:5.1f}%  {m['dbc']:<28} "
                f"{len(m.get('matched_ids', []))} IDs match, "
                f"{m.get('message_count', 0)} msgs{cov_txt}")
            item.setData(256, m["dbc"])
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        lay.addWidget(self._list)

        self._only = QCheckBox("Only messages seen in this capture")
        self._only.setChecked(True)
        lay.addWidget(self._only)

        buttons = QDialogButtonBox()
        apply = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        apply.setObjectName("btn_green")
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def accept(self):
        item = self._list.currentItem()
        self.chosen = item.data(256) if item else None
        self.only_seen = self._only.isChecked()
        super().accept()
