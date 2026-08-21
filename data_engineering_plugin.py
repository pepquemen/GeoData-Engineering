# -*- coding: utf-8 -*-
"""
Main plugin class: manages the plugin's lifecycle within QGIS
(registering/unregistering with the interface, the menu/toolbar
action) and creates the dock widget on demand.
"""

import os.path

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .data_engineering_dock import DataEngineeringDock


class DataEngineeringPlugin:
    """Main class of the Data Engineering Toolkit plugin."""

    def __init__(self, iface):
        # Reference to the QGIS interface
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        # Translation support (optional, kept ready for future locales)
        locale = QSettings().value('locale/userLocale', 'en')[0:2]
        locale_path = os.path.join(
            self.plugin_dir, 'i18n', f'data_engineering_toolkit_{locale}.qm'
        )
        self.translator = None
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions = []
        self.menu = self.tr('&Data Engineering Toolkit')
        self.toolbar = self.iface.addToolBar('Data Engineering Toolkit')
        self.toolbar.setObjectName('DataEngineeringToolkit')

        self.dock_widget = None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def tr(self, message):
        return QCoreApplication.translate('DataEngineeringPlugin', message)

    def add_action(self, icon_path, text, callback, enabled_flag=True,
                    add_to_menu=True, add_to_toolbar=True, status_tip=None,
                    whats_this=None, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)
        if whats_this is not None:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    # ------------------------------------------------------------------
    # Plugin lifecycle (called by QGIS)
    # ------------------------------------------------------------------
    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icons', 'icon.svg')
        self.add_action(
            icon_path,
            text=self.tr('Data Engineering Toolkit'),
            callback=self.toggle_dock,
            parent=self.iface.mainWindow(),
            status_tip=self.tr('Open the Data Engineering panel'),
        )

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

        if self.dock_widget is not None:
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget.deleteLater()
            self.dock_widget = None

    # ------------------------------------------------------------------
    # Plugin logic
    # ------------------------------------------------------------------
    def toggle_dock(self):
        """Creates the dock the first time it's needed; on subsequent
        calls it just shows/hides it."""
        if self.dock_widget is None:
            self.dock_widget = DataEngineeringDock(self.iface, parent=self.iface.mainWindow())
            # Docked at the bottom of the window, in the same area as the
            # native Attribute Table panel, as requested.
            self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_widget)
        else:
            self.dock_widget.setVisible(not self.dock_widget.isVisible())
