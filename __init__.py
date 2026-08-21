# -*- coding: utf-8 -*-
"""
Data Engineering Toolkit
-------------------------
QGIS plugin inspired by the "Data Engineering" view in ArcGIS Pro: a
data-profiling panel with interactive feature selection from statistics
and charts.

This module is the mandatory entry point for every QGIS plugin: QGIS
looks here for the classFactory() function.
"""


def classFactory(iface):
    """Entry point QGIS uses to instantiate the plugin.

    :param iface: Reference to the QGIS interface (QgisInterface).
    :type iface: QgsInterface
    """
    from .data_engineering_plugin import DataEngineeringPlugin
    return DataEngineeringPlugin(iface)
