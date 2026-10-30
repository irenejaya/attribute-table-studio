# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Find and Replace in the Attribute Explorer (Ctrl+F / Ctrl+H).

A :class:`Matcher` decides whether a cell matches (its text as the table
shows it) and makes the replacement text.  :func:`search` runs in a
background job: it reads the shown records' visible columns from a feature
source and returns the matching cells in table order (row by row, left to
right), each encoded as ``row * len(cols) + position of its column``.
NULL cells never match.
"""

import re
from array import array

from . import fields as F

# $1 .. $99, $& (or $0) and $$ in a replacement, as in VS Code
_DOLLAR = re.compile(r"\$(\$|&|\d{1,2})")


class Matcher:
    """*text* found anywhere in a cell (or as the whole cell), ignoring case
    unless *case*; with *regex* the text is a regular expression.  Raises
    ``re.error`` for an invalid expression."""

    def __init__(self, text, case=False, whole=False, regex=False):
        self.text = text
        self.whole = whole
        self.regex = regex
        pattern = text if regex else re.escape(text)
        self._re = re.compile(pattern, 0 if case else re.IGNORECASE)

    def _match(self, text):
        return self._re.fullmatch(text) if self.whole else self._re.search(text)

    def matches(self, text):
        return self._match(text) is not None

    def replace(self, text, replacement):
        """The cell's text with every match replaced (the whole cell with
        Match whole cell); with a regular expression, $1 .. $99 put back a
        group, $& (or $0) the whole match and $$ a dollar sign."""
        if self.regex:
            def expand(m):
                return _expand(replacement, m)
        else:
            def expand(_m):
                return replacement
        if self.whole:
            m = self._re.fullmatch(text)
            return text if m is None else expand(m)
        return self._re.sub(expand, text)


def _expand(template, m):
    groups = m.re.groups

    def one(token):
        t = token.group(1)
        if t == "$":
            return "$"
        if t == "&":
            return m.group(0)
        if int(t) <= groups:
            return m.group(int(t)) or ""
        if len(t) == 2 and int(t[0]) <= groups:   # $12 with one group: $1, then "2"
            return (m.group(int(t[0])) or "") + t[1]
        return token.group(0)
    return _DOLLAR.sub(one, template)


def search(source, fids, fields, cols, matcher, job, step=2000):
    """Matching cells of the records *fids* (in table order) in the columns
    *cols* (logical indexes, left to right as shown): a sorted array of
    ``row * len(cols) + position``.  *fields* are the layer's QgsFields by
    logical index; *source* a QgsVectorLayerFeatureSource."""
    from qgis.core import QgsFeatureRequest       # the matcher works without QGIS
    from .compat import NO_GEOMETRY
    from .scan import FID_FILTER_LIMIT
    rows = {fid: i for i, fid in enumerate(fids)}
    n = len(cols)
    hits = array("q")
    if not rows or not n:
        return hits
    req = QgsFeatureRequest()
    req.setFlags(NO_GEOMETRY)
    req.setSubsetOfAttributes(sorted(set(cols)))
    if len(rows) <= FID_FILTER_LIMIT:
        req.setFilterFids(list(rows))
    total, done = len(rows), 0
    for feat in source.getFeatures(req):
        row = rows.get(feat.id())
        if row is None:
            continue
        attrs = feat.attributes()
        base = row * n
        for k, col in enumerate(cols):
            value = attrs[col]
            if not F.is_null(value) and matcher.matches(F.cell_text(fields[col], value)):
                hits.append(base + k)
        done += 1
        if done % step == 0:
            job.check()
            job.report(100.0 * done / total)
    return array("q", sorted(hits))
