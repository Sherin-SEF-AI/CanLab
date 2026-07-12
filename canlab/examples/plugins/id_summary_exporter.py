"""Example CanLab plugin: a custom exporter.

Adds a "Tools" action that writes a per-ID summary CSV (id, count, mean period,
per-byte entropy) of the loaded capture — a template for your own exporters.
"""
PLUGIN_NAME    = "ID Summary Exporter"
PLUGIN_VERSION = "1.0"


def register(app):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    def _export():
        state = app._state
        df = state.frames_df
        if df is None or df.empty:
            QMessageBox.information(app, "Export", "Load a capture first.")
            return
        path, _ = QFileDialog.getSaveFileName(app, "Save ID summary",
                                              "id_summary.csv", "CSV (*.csv)")
        if not path:
            return
        import numpy as np
        rows = []
        for can_id, grp in df.groupby("ID"):
            ts = grp["Timestamp"].to_numpy()
            period = (ts[-1] - ts[0]) / max(len(ts) - 1, 1) if len(ts) > 1 else 0.0
            ent = []
            for i in range(8):
                s = grp.get(f"B{i}")
                if s is None:
                    continue
                s = s.dropna().astype(int)
                if s.empty:
                    continue
                p = np.bincount(s, minlength=256) / len(s)
                p = p[p > 0]
                ent.append(float(-np.sum(p * np.log2(p))))
            rows.append(f"{can_id},{len(grp)},{period*1000:.2f},{np.mean(ent) if ent else 0:.2f}")
        with open(path, "w") as f:
            f.write("id,count,period_ms,mean_byte_entropy\n")
            f.write("\n".join(rows))
        QMessageBox.information(app, "Export", f"Wrote {len(rows)} IDs to {path}")

    menu = app.menuBar().addMenu("Plugins:Export")
    menu.addAction("Export ID summary…").triggered.connect(_export)
