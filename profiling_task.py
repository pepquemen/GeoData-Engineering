# -*- coding: utf-8 -*-
"""
profiling_task.py
------------------
Runs profiling_engine.profile_features() on a background QgsTask, so
QGIS stays responsive while a large layer is profiled.

Two PyQGIS pitfalls handled here:

  * Features are read through a QgsVectorLayerFeatureSource created on
    the main thread (in __init__), the documented safe way to read a
    layer from a worker thread.

  * A Python QgsTask subclass must stay referenced from Python until it
    has finished: if the only reference is dropped while the task is
    still running (e.g. the user cancels or switches layer), the Python
    side can be garbage-collected under the C++ task and QGIS crashes.
    Every task registers itself in _RUNNING_TASKS and only leaves it in
    finished(), whatever the outcome.
"""

import time

from qgis.core import QgsTask, QgsVectorLayerFeatureSource

from . import profiling_engine, stats_core
from .compat import TASK_CAN_CANCEL
from .translation import tr

# Strong references to tasks that have been queued and not yet finished.
_RUNNING_TASKS = set()


def cancel_all():
    """Cancels every task started by this plugin (used on unload)."""
    for task in list(_RUNNING_TASKS):
        task.detach()
        task.cancel()


class ProfileLayerTask(QgsTask):

    def __init__(self, layer, field_names, on_finished, selected_only=False,
                 outlier_k=stats_core.DEFAULT_OUTLIER_K):
        super().__init__(
            tr('Data Engineering: profiling "{}"').format(layer.name()),
            TASK_CAN_CANCEL,
        )
        # Everything that touches the live layer happens here, on the
        # main thread.
        self.feature_source = QgsVectorLayerFeatureSource(layer)
        self.fields = layer.fields()
        self.layer_id = layer.id()
        self.layer_name = layer.name()
        self.selected_only = selected_only
        self.filter_fids = list(layer.selectedFeatureIds()) if selected_only else None
        self.total_hint = (len(self.filter_fids) if selected_only else layer.featureCount())
        self.field_names = list(field_names)
        self.outlier_k = outlier_k
        self._on_finished = on_finished

        self.result = None       # LayerProfile
        self.error = None

        _RUNNING_TASKS.add(self)

    @property
    def progress_known(self):
        return self.total_hint is not None and self.total_hint > 0

    def detach(self):
        """Drops the UI callback (the dock is going away)."""
        self._on_finished = None

    def _report(self, pct):
        if pct >= 0:
            self.setProgress(pct)

    def run(self):
        started = time.perf_counter()
        try:
            out = profiling_engine.profile_features(
                self.feature_source.getFeatures,
                self.fields,
                self.field_names,
                total_hint=self.total_hint,
                filter_fids=self.filter_fids,
                on_progress=self._report,
                is_canceled=self.isCanceled,
                outlier_k=self.outlier_k,
            )
        except Exception as exc:  # surfaced in the UI instead of failing silently
            self.error = exc
            return False
        if out is None:
            return False
        count, profiles = out
        self.result = profiling_engine.LayerProfile(
            layer_id=self.layer_id,
            layer_name=self.layer_name,
            feature_count=count,
            selected_only=self.selected_only,
            fields=profiles,
            elapsed=time.perf_counter() - started,
            sample_limit=stats_core.SAMPLE_LIMIT,
        )
        return True

    def finished(self, success):
        # Called by the task manager on the MAIN thread, whatever the
        # outcome (success, failure or cancel).
        try:
            callback = self._on_finished
            self._on_finished = None
            if callback is not None:
                try:
                    callback(self, success)
                except RuntimeError:
                    # The dock's C++ side was deleted (plugin unloaded).
                    pass
        finally:
            _RUNNING_TASKS.discard(self)
