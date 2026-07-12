"""Example CanLab plugin: adds a menu action.

Copy this file into ~/.canlab/plugins/ and it will appear in the Plugins menu.
Plugins are only executed when you enable/activate them (see PLUGINS.md).
"""
PLUGIN_NAME    = "Hello World"
PLUGIN_VERSION = "1.0"


def register(app):
    """Called with the MainWindow instance when the plugin is activated."""
    from PyQt6.QtWidgets import QMessageBox

    def _say_hello():
        state = app._state
        n = len(state.frames_df) if state.frames_df is not None else 0
        QMessageBox.information(app, "Hello from a plugin",
                                f"CanLab currently has {n} frames loaded.")

    # Add a toolbar/menu action. MainWindow exposes menuBar(); attach anywhere.
    menu = app.menuBar().addMenu("Hello")
    act = menu.addAction("Say hello")
    act.triggered.connect(_say_hello)
