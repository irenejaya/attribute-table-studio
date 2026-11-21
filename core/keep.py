# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Kept columns: the values of a layer's records, compact, so the statistics
of any part of them (a filter, a View scope, a drill-through) are worked
out without reading the layer again (no QGIS needed, unit tested).

When the Attribute Explorer reads all its layer's records for the
statistics, its builders also remember each record (:mod:`.stats`, keep
mode): a status byte per record (0 a value, 1 NULL, 2 invalid) and the
values of the records with status 0 (8 bytes each); text a code per record
into its distinct values; yes / no a code per record.  A :class:`Store`
holds those columns with the records' feature ids.  For a filter, the
ids the filter leaves are looked up in the store and each column's
statistics are worked out from the kept values with numpy: exactly the
statistics a new read would give (the same functions compute them).

The store lives within a memory budget (:func:`budget`: about 10 % of the
computer's memory unless set in Settings) and belongs to one state of the
layer: any edit, a new filter of the layer itself or a field change makes
it stale, and the next read of all records builds it again.  Without numpy
nothing is kept (every scope is read, as before).
"""

import ctypes
import os
import sys

from . import compact as CP
from . import fields as F
from . import stats as S

AUTO_SHARE = 0.10                # automatic budget: this share of the memory
AUTO_MIN = 256 * 2 ** 20
AUTO_MAX = 8 * 2 ** 30


def total_memory():
    """The computer's memory in bytes, or None when it cannot be told."""
    try:
        if sys.platform.startswith("win"):
            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
            return None
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and size > 0:
            return int(pages * size)
    except (AttributeError, OSError, ValueError):
        pass
    return None


def auto_budget():
    """About 10 % of the computer's memory (at least 256 MB, at most 8 GB)."""
    total = total_memory()
    if not total:
        return 800 * 2 ** 20
    return int(min(max(total * AUTO_SHARE, AUTO_MIN), AUTO_MAX))


def budget(setting_mb=0):
    """Bytes the kept columns may take: *setting_mb* from Settings, 0 for
    automatic."""
    if setting_mb and setting_mb > 0:
        return int(setting_mb) * 2 ** 20
    return auto_budget()


def available():
    """Columns can be kept (numpy works them out)."""
    return CP._np() is not None


def _bytes_view(data):
    np = CP.np
    if not len(data):
        return np.zeros(0, dtype=np.uint8)
    return np.frombuffer(data, dtype=np.uint8)


class KeptColumn:
    """One column of a store: per record a status (or code), plus values."""

    def __init__(self, kind):
        self.kind = kind
        self.status = None           # numbers, dates: uint8 per record
        self.values = None           # numbers, dates: values of status-0 records
        self.codes = None            # text: int32 per record (-1 NULL); yes / no: uint8
        self.labels = None           # text: the distinct values by code
        self.earliest = self.latest = None

    @classmethod
    def from_builder(cls, b):
        np = CP._np()
        if np is None:
            return None
        col = cls(b.kind)
        # views over the builder's own arrays (no copy: the read is done)
        if isinstance(b, S.KeepNumberBuilder):
            col.status = _bytes_view(b.status)
            col.values = S._view(b.values) if len(b.values) else \
                np.zeros(0, dtype=np.float64)
        elif isinstance(b, S.KeepDateBuilder):
            col.status = _bytes_view(b.status)
            col.values = S._view(b.keys) if len(b.keys) else \
                np.zeros(0, dtype=np.float64)
            col.earliest, col.latest = b.earliest, b.latest
        elif isinstance(b, S.KeepBoolBuilder):
            col.codes = _bytes_view(b.codes)
        else:
            col.codes = np.frombuffer(b.codes, dtype=np.int32) if len(b.codes) \
                else np.zeros(0, dtype=np.int32)
            col.labels = [None] * len(b.index)
            for text, code in b.index.items():
                col.labels[code] = text
        return col

    @property
    def records(self):
        return len(self.status) if self.status is not None else len(self.codes)

    @property
    def nbytes(self):
        n = 0
        for a in (self.status, self.values, self.codes):
            if a is not None:
                n += a.nbytes
        if self.labels:
            n += sum(len(t) + 60 for t in self.labels)
        return n

    # ------------------------------------------------------------------
    def profile(self, mask=None, fids=None, fetch=None, bins=S.DEFAULT_BINS,
                freq_limit=S.FREQ_LIMIT):
        """Statistics of the records where *mask* is true (all of them when
        None), as stats.compute_profile gives them.  Dates need *fids* (the
        store's) and *fetch* (fid -> the original value) for the earliest
        and latest values of a part."""
        np = CP.np
        kind = self.kind
        if self.status is not None:
            st = self.status if mask is None else self.status[mask]
            missing = int((st == 1).sum())
            invalid = int((st == 2).sum())
            if mask is None:
                values = self.values
                records = None
            else:
                valid = np.flatnonzero(self.status == 0)
                picked = mask[valid]
                values = self.values[picked]
                records = valid[picked]
            p = S.Profile(kind, int(st.size))
            p.missing, p.invalid, p.valid = missing, invalid, int(values.size)
            if not values.size:
                return p
            if kind in F.NUMERIC:
                S._numbers_from_sorted_np(p, np.sort(values), bins)
                return p
            if records is None:
                p.earliest, p.latest = self.earliest, self.latest
            else:                        # first record holding the lowest / highest
                lo = int(records[int(np.argmin(values))])
                hi = int(records[int(np.argmax(values))])
                p.earliest, p.latest = fetch(int(fids[lo])), fetch(int(fids[hi]))
            a = np.sort(values)
            p.distinct = S._np_distinct(a)
            p.minimum, p.maximum = a[0].item(), a[-1].item()
            p.bins, p.integer_bins = S._np_histogram(a, bins, kind == F.DATE)
            return p
        codes = self.codes if mask is None else self.codes[mask]
        if kind == F.BOOL:
            n = np.bincount(codes, minlength=4)
            b = S.BoolBuilder(kind)
            b.false, b.true, b.missing, b.invalid = (int(x) for x in n[:4])
            return b.profile()
        missing = int((codes == -1).sum())
        counts = np.bincount(codes[codes >= 0], minlength=len(self.labels))
        b = S.TextBuilder(kind)
        b.missing = missing
        b.counts = {self.labels[i]: int(counts[i]) for i in np.flatnonzero(counts)}
        return b.profile(bins, freq_limit)


class Store:
    """Kept columns of one read of all a layer's records."""

    def __init__(self, key, fids):
        self.key = key               # the layer state it belongs to
        self.fids = CP.view(CP.ids(fids))
        if self.fids is None:
            self.fids = CP.np.zeros(0, dtype=CP.np.int64)
        self.columns = {}            # logical -> KeptColumn
        self._sorted = None
        self._order = None
        self._mask_of = None         # (scope ids object, mask)

    @property
    def records(self):
        return int(self.fids.size)

    @property
    def nbytes(self):
        n = self.fids.nbytes
        if self._sorted is not None:
            n += self._sorted.nbytes + self._order.nbytes
        return n + sum(c.nbytes for c in self.columns.values())

    def same_records(self, fids):
        """True when *fids* (a read's ids, in read order) are this store's."""
        a = CP.view(CP.ids(fids))
        if a is None:
            return self.records == 0
        return a.size == self.fids.size and bool((a == self.fids).all())

    def add(self, logical, column):
        if column is not None and column.records == self.records:
            self.columns[logical] = column

    def drop(self, logical):
        self.columns.pop(logical, None)

    def mask(self, scope_fids):
        """Bool per record: is it among *scope_fids*?  None when every record
        is (the read's own scope)."""
        if self._mask_of is not None and self._mask_of[0] is scope_fids:
            return self._mask_of[1]
        np = CP.np
        q = CP.view(CP.ids(scope_fids))
        if q is None:
            q = np.zeros(0, dtype=np.int64)
        if q.size == self.records and (q.size == 0 or self._covers_all(q)):
            mask = None
        else:
            if self._sorted is None:
                self._order = np.argsort(self.fids, kind="stable")
                self._sorted = self.fids[self._order]
            pos = np.searchsorted(self._sorted, q)
            inside = pos < self._sorted.size
            pos = pos[inside]
            found = self._sorted[pos] == q[inside]
            mask = np.zeros(self.records, dtype=bool)
            mask[self._order[pos[found]]] = True
        self._mask_of = (scope_fids, mask)
        return mask

    def _covers_all(self, q):
        np = CP.np
        if self._sorted is None:
            self._order = np.argsort(self.fids, kind="stable")
            self._sorted = self.fids[self._order]
        return bool((np.sort(q) == self._sorted).all())

    def profile(self, logical, scope_fids, fetch=None):
        mask = self.mask(scope_fids)
        return self.columns[logical].profile(mask, self.fids, fetch)

    def sorted_ids(self, logical, scope_fids, desc=False):
        """The records of *scope_fids* sorted by kept column *logical*, as a
        new read sorted by that field would give them (NULL last, ties in
        reading order), or None when the column cannot tell (yes / no
        values, or NaN / unreadable values whose order is the read's)."""
        np = CP.np
        col = self.columns.get(logical)
        if col is None or col.kind == F.BOOL:
            return None
        mask = self.mask(scope_fids)
        records = np.arange(self.records) if mask is None else np.flatnonzero(mask)
        if col.status is not None:
            if (col.status == 2).any():
                return None
            st = col.status[records]
            present, missing = records[st == 0], records[st == 1]
            where = np.cumsum(col.status == 0) - 1      # record -> its value
            keys = col.values[where[present]].astype(np.float64)   # sort_key: float(v)
        else:
            codes = col.codes[records]
            present, missing = records[codes >= 0], records[codes < 0]
            labels = col.labels
            ranked = sorted(range(len(labels)), key=lambda c: (labels[c].lower(), labels[c]))
            rank = np.empty(len(labels), dtype=np.int64)
            rank[np.asarray(ranked, dtype=np.int64)] = np.arange(len(labels))
            keys = rank[codes[codes >= 0]]
        order = np.argsort(-keys if desc else keys, kind="stable")
        return CP.ids(self.fids[np.concatenate([present[order], missing])])
