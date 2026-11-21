# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Compact record lists for big layers (no QGIS needed, unit tested).

The Attribute Explorer keeps the ids of the records it shows as 8-byte
numbers (``array('q')``) instead of a Python list: about 80 MB at 10
million records instead of about 400 MB.  :class:`RowIndex` finds a
record's row without a dictionary of every record (which alone took
another gigabyte at 10 million): when the ids ascend (the usual order) it
searches the list itself; otherwise it builds a sorted copy with the row
of each id once it is first needed.

numpy does the searching when QGIS has it (it nearly always does); plain
Python works the same, slower.
"""

import bisect
from array import array

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:                 # pragma: no cover - QGIS ships numpy
    np = None
    HAS_NUMPY = False

USE_NUMPY = HAS_NUMPY            # tests switch this off to check plain Python


def _np():
    return np if USE_NUMPY else None


def ids(values=()):
    """Feature ids as a compact list (an ``array('q')`` is kept as is)."""
    if isinstance(values, array) and values.typecode == "q":
        return values
    xp = _np()
    if xp is not None and isinstance(values, xp.ndarray):
        return array("q", values.astype(xp.int64, copy=False).tobytes())
    return array("q", values)


def view(values):
    """numpy view of a compact list (no copy), or None without numpy."""
    xp = _np()
    if xp is None or not len(values):
        return None
    return xp.frombuffer(values, dtype=xp.int64)


def ascending(values):
    """True when the ids strictly ascend."""
    n = len(values)
    if n < 2:
        return True
    a = view(values)
    if a is not None:
        return bool((a[1:] > a[:-1]).all())
    return all(values[i] < values[i + 1] for i in range(n - 1))


def take(values, order):
    """``values`` in the order of the row numbers *order*, compact."""
    a = view(values)
    if a is not None:
        return ids(a[np.asarray(order, dtype=np.int64)])
    return array("q", (values[i] for i in order))


def sort_ids(values):
    """The ids ascending (feature id order), compact."""
    a = view(values)
    if a is not None:
        return ids(np.sort(a))
    return array("q", sorted(values))


class RowIndex:
    """Rows of feature ids in a compact list, built when first needed."""

    def __init__(self, values):
        self.values = values
        self._ready = False
        self._ascending = True
        self._keys = None            # sorted ids (when the list is not)
        self._rows = None            # the row of each sorted id
        self._dict = None            # plain Python fallback for any order

    def _build(self):
        if self._ready:
            return
        self._ready = True
        self._ascending = ascending(self.values)
        if self._ascending:
            return
        a = view(self.values)
        if a is not None:
            order = np.argsort(a, kind="stable")
            self._keys = a[order]
            self._rows = order
        else:
            self._dict = {f: i for i, f in enumerate(self.values)}

    def row(self, fid):
        """Row of *fid*, or None when it is not in the list."""
        try:
            fid = int(fid)
        except (TypeError, ValueError):
            return None
        self._build()
        values = self.values
        if self._ascending:
            i = bisect.bisect_left(values, fid)
            return i if i < len(values) and values[i] == fid else None
        if self._dict is not None:
            return self._dict.get(fid)
        keys = self._keys
        i = int(np.searchsorted(keys, fid))
        if i < len(keys) and keys[i] == fid:
            return int(self._rows[i])
        return None

    def rows(self, fids):
        """Rows of those of *fids* that are in the list (any order)."""
        if not fids or not len(self.values):
            return []
        self._build()
        xp = _np()
        if xp is None or self._dict is not None:
            out = []
            for f in fids:
                r = self.row(f)
                if r is not None:
                    out.append(r)
            return out
        q = np.fromiter((int(f) for f in fids), dtype=np.int64, count=len(fids))
        keys = view(self.values) if self._ascending else self._keys
        pos = np.searchsorted(keys, q)
        inside = pos < len(keys)
        pos_in = pos[inside]
        found = keys[pos_in] == q[inside]
        pos_found = pos_in[found]
        rows = pos_found if self._ascending else self._rows[pos_found]
        return rows.tolist()

    def count(self, fids):
        """How many of *fids* are in the list."""
        return len(self.rows(fids))

    def first(self, fids):
        """The smallest row of *fids* in the list, or None."""
        rows = self.rows(fids)
        return min(rows) if rows else None
