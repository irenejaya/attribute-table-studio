# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Make PNG pictures about four times smaller:  python scripts/shrink_png.py FILE.png ...

Each picture is saved again with 256 colours (screenshots of the plugin look
the same).  Needs Pillow, so it runs with a Python that has it, not
necessarily QGIS's own; tests/qgis/make_help_images.py calls it when it can.
"""

import os
import sys

from PIL import Image


def shrink(path):
    before = os.path.getsize(path)
    image = Image.open(path).convert("RGBA")
    temp = path + ".tmp"
    image.quantize(256, method=Image.Quantize.FASTOCTREE).save(temp, "PNG", optimize=True)
    os.replace(temp, path)                 # never a half-written picture
    return before, os.path.getsize(path)


def main(paths):
    total = [0, 0]
    for path in paths:
        before, after = shrink(path)
        total[0] += before
        total[1] += after
    print(f"{len(paths)} pictures: {total[0] // 1024:,} KB -> {total[1] // 1024:,} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
