# -*- coding: utf-8 -*-
"""
Background jobs on the QGIS task manager.

A :class:`Job` runs ``fn(job)`` in a worker thread and calls ``on_done(job)``
on the GUI thread afterwards (``job.result`` / ``job.error`` /
``job.cancelled``).  :class:`JobRunner` keeps the Python references alive,
tags every job with a generation number and cancels superseded work, so a
stale result can never overwrite a newer one.

Rules for ``fn``: never touch widgets or a QgsVectorLayer; read features
from a ``QgsVectorLayerFeatureSource`` created on the GUI thread, call
``job.check()`` regularly and ``job.report(percent)`` for progress.
"""

import traceback

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import QgsApplication, QgsTask

from .compat import TASK_QUIET


class JobCancelled(Exception):
    pass


class Job(QgsTask):
    def __init__(self, description, fn, on_done, flags=TASK_QUIET):
        super().__init__(description, flags)
        self._fn = fn
        self._on_done = on_done
        self.result = None
        self.error = None
        self.cancelled = False
        self.generation = 0
        self.tag = None

    # called from the worker thread ------------------------------------
    def check(self):
        if self.isCanceled():
            raise JobCancelled()

    def report(self, percent):
        self.setProgress(max(0.0, min(100.0, float(percent))))

    def run(self):
        try:
            self.result = self._fn(self)
            return True
        except JobCancelled:
            self.cancelled = True
            return False
        except Exception:  # reported on the GUI thread
            self.error = traceback.format_exc()
            return False

    # called on the GUI thread -------------------------------------------
    def finished(self, ok):
        if not ok and self.error is None:
            self.cancelled = True
        if self._on_done is not None:
            self._on_done(self)


class JobRunner(QObject):
    """Owns the jobs of one tool window."""

    progress = pyqtSignal(float)
    busyChanged = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs = set()
        self.generation = 0

    def new_generation(self):
        """Invalidate everything running; returns the new generation."""
        self.generation += 1
        self.cancel_all()
        return self.generation

    def is_current(self, job):
        return job.generation == self.generation and not job.cancelled

    def start(self, description, fn, on_done, tag=None, flags=TASK_QUIET):
        job = Job(description, fn, None, flags)
        job.generation = self.generation
        job.tag = tag

        def done(j, cb=on_done):
            try:
                self._jobs.discard(j)
                if not self._jobs:
                    self.busyChanged.emit(False)
                cb(j)
            except RuntimeError:
                # the tool window was closed while the job ran
                pass

        job._on_done = done
        job.progressChanged.connect(self.progress.emit)
        was_idle = not self._jobs
        self._jobs.add(job)
        QgsApplication.taskManager().addTask(job)
        if was_idle:
            self.busyChanged.emit(True)
        return job

    def cancel_all(self, tag=None):
        for job in list(self._jobs):
            if tag is None or job.tag == tag:
                job.cancel()

    def running(self, tag=None):
        return any(tag is None or j.tag == tag for j in self._jobs)

    @property
    def busy(self):
        return bool(self._jobs)
