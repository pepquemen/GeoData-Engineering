# -*- coding: utf-8 -*-
"""
Test bootstrap.

The repository root IS the plugin package, but the checkout folder may
be called anything (e.g. "GeoData-Engineering", not a valid Python
module name), so the package is loaded explicitly under its real name.

Run with the Python interpreter that has the QGIS bindings, e.g.:
    QT_QPA_PLATFORM=offscreen python -m pytest tests
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = 'data_engineering_toolkit'

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from qgis.core import QgsApplication
    HAS_QGIS = True
except ImportError:  # pragma: no cover
    HAS_QGIS = False


def _load_package():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, os.path.join(ROOT, '__init__.py'), submodule_search_locations=[ROOT])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_QGIS_APP = None


@pytest.fixture(scope='session')
def qgis_app():
    global _QGIS_APP
    if not HAS_QGIS:
        pytest.skip('QGIS Python bindings not available')
    if _QGIS_APP is None:
        _QGIS_APP = QgsApplication([], True)
        _QGIS_APP.initQgis()
    return _QGIS_APP


@pytest.fixture(scope='session')
def plugin(qgis_app):
    pkg = _load_package()
    for sub in ('stats_core', 'profiling_engine', 'profiling_task',
                'selection_helper', 'symbology_helper'):
        importlib.import_module(PACKAGE + '.' + sub)
    return pkg


def load_stats_core():
    """stats_core has no QGIS dependency: load it standalone so its
    tests run even without QGIS installed."""
    name = PACKAGE + '.stats_core'
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, 'stats_core.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
