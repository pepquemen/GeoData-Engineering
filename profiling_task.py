# -*- coding: utf-8 -*-
"""
profiling_task.py
------------------
Runs profiling_engine.profile_all_fields() on a background QgsTask
instead of the main GUI thread, so QGIS stays fully responsive - with a
progress bar in the dock - while a heavy layer (e.g. a full cadastral
dataset) is being profiled.

Feature reading happens through a QgsVectorLayerFeatureSource, which is
the QGIS-documented safe way to read features from a layer off the main
thread: the source is cloned on the main thread (in __init__, before the
task is queued) and only ever touched from run() afterwards, so the task
never has to reach back into the live QgsVectorLayer/provider from a
worker thread.
"""

from qgis.core import QgsTask, QgsVectorLayerFeatureSource

from . import profiling_engine


class ProfileLayerTask(QgsTask):

    def __init__(self, layer, field_names, on_finished):
        super().__init__('Data Engineering Toolkit: profiling fields', QgsTask.Flag.CanCancel)
        # QgsVectorLayerFeatureSource must be created on the main thread;
        # it's then safe to pull features from it inside run().
        self.feature_source = QgsVectorLayerFeatureSource(layer)
        self.fields = layer.fields()
        self.total_count = layer.featureCount()
        self.field_names = field_names
        self._on_finished = on_finished

        self.results = None
        self.error = None

    def run(self):
        try:
            self.results = profiling_engine.profile_all_fields(
                self.feature_source.getFeatures,
                self.fields,
                self.field_names,
                self.total_count,
                on_progress=self.setProgress,
                is_canceled=self.isCanceled,
            )
        except Exception as exc:  # defensive: surface the error instead of a silent failure
            self.error = exc
            return False
        return self.results is not None

    def finished(self, success):
        # QGIS's task manager calls this automatically on the MAIN thread
        # once run() returns, whether it succeeded, failed, or the task
        # was canceled - this is the one place in the task where it's
        # safe to touch Qt widgets.
        self._on_finished(self, success)
