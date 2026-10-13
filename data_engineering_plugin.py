# -*- coding: utf-8 -*-
"""
Main plugin class: lifecycle within QGIS (initGui / unload), the
toolbar / menu action and the dock widget, created on demand.
"""

import os.path

from qgis.PyQt.QtCore import QCoreApplication, QSettings, Qt, QTranslator
from qgis.PyQt.QtGui import QIcon

try:  # Qt5: QtWidgets / Qt6: QtGui
    from qgis.PyQt.QtWidgets import QAction
except ImportError:  # pragma: no cover
    from qgis.PyQt.QtGui import QAction

from . import profiling_task
from .translation import tr
from .ui.theme import register_fonts


class DataEngineeringPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.translator = None
        self._install_translator()

        self.action = None
        self.menu = tr('&Data Engineering Toolkit')
        self.toolbar = None
        self.dock_widget = None

    def _install_translator(self):
        locale = (QSettings().value('locale/userLocale', 'en') or 'en')[0:2]
        path = os.path.join(self.plugin_dir, 'i18n', 'data_engineering_toolkit_{}.qm'.format(locale))
        if os.path.exists(path):
            translator = QTranslator()
            if translator.load(path):
                QCoreApplication.installTranslator(translator)
                self.translator = translator

    # ------------------------------------------------------------------
    def initGui(self):
        register_fonts()
        self.toolbar = self.iface.addToolBar(tr('Data Engineering Toolkit'))
        self.toolbar.setObjectName('DataEngineeringToolkit')

        icon = QIcon(os.path.join(self.plugin_dir, 'icons', 'icon.svg'))
        self.action = QAction(icon, tr('Data Engineering Toolkit'), self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setStatusTip(tr('Open the Data Engineering panel'))
        self.action.toggled.connect(self._on_action_toggled)
        self.toolbar.addAction(self.action)
        self.iface.addPluginToMenu(self.menu, self.action)

    def unload(self):
        # Cancel background work first: no task may call back into a
        # widget that is about to be deleted.
        profiling_task.cancel_all()

        if self.dock_widget is not None:
            self.dock_widget.shutdown()
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None

        if self.action is not None:
            self.iface.removePluginMenu(self.menu, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

        if self.toolbar is not None:
            # `del self.toolbar` only dropped the Python reference and left
            # an empty toolbar behind after every plugin reload.
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

        if self.translator is not None:
            QCoreApplication.removeTranslator(self.translator)
            self.translator = None

    # ------------------------------------------------------------------
    def _on_action_toggled(self, checked):
        if checked and self.dock_widget is None:
            from .data_engineering_dock import DataEngineeringDock
            self.dock_widget = DataEngineeringDock(self.iface, parent=self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_widget)
            # Keep the toolbar button in sync when the dock is closed with
            # its own [x] button.
            self.dock_widget.visibilityChanged.connect(self._on_dock_visibility)
        if self.dock_widget is not None:
            self.dock_widget.setVisible(checked)

    def _on_dock_visibility(self, visible):
        """Keeps the toolbar button in sync when the dock is closed with
        its own [x]. Tabified docks also report "not visible" when another
        tab is brought to front, so only a really hidden dock unchecks it."""
        if self.action is None or self.dock_widget is None:
            return
        checked = visible or not self.dock_widget.isHidden()
        if self.action.isChecked() != checked:
            self.action.blockSignals(True)
            self.action.setChecked(checked)
            self.action.blockSignals(False)
