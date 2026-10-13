# -*- coding: utf-8 -*-
"""
translation.py
--------------
Single translation context for the whole plugin. Source strings are
English; translations live in i18n/data_engineering_toolkit_<lang>.ts
(compiled to .qm with scripts/build_translations.py) and are loaded by
data_engineering_plugin.py according to the QGIS locale.
"""

from qgis.PyQt.QtCore import QCoreApplication

CONTEXT = 'DataEngineeringToolkit'


def tr(text):
    return QCoreApplication.translate(CONTEXT, text)
