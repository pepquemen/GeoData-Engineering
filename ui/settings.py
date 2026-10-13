# -*- coding: utf-8 -*-
"""
ui/settings.py
--------------
User preferences, persisted with QSettings under the plugin's own group
(so they survive QGIS restarts and live in the QGIS profile).
"""

from qgis.PyQt.QtCore import QSettings

GROUP = 'data_engineering_toolkit/'

DEFAULTS = {
    'theme': 'auto',          # auto | light | dark
    'zoom_mode': 'zoom',      # zoom | pan | none
    'flash': True,            # flash features after selecting them
    'outlier_k': 1.5,         # Tukey fence multiplier (1.5 or 3.0)
    'auto_refresh': False,    # re-profile automatically after edits
    'selected_only': False,   # profile only the selected features
}


def _coerce(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.lower() in ('1', 'true', 'yes')
        return bool(value)
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


def get(key):
    value = QSettings().value(GROUP + key, DEFAULTS[key])
    return _coerce(key, value)


def set(key, value):  # noqa: A001 - mirrors QSettings naming
    QSettings().setValue(GROUP + key, value)
