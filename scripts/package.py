# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Build the installable plugin zip:  python scripts/package.py

Creates dist/attribute_table_studio-<version>.zip containing a top-level
'attribute_table_studio' folder, ready for Plugins > Manage and Install Plugins >
Install from ZIP.  Tests, docs and development files are left out.
"""

import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "attribute_table_studio"
INCLUDE = ("__init__.py", "plugin.py", "metadata.txt", "icon.svg", "LICENSE",
           "ADDITIONAL_TERMS.md", "README.md", "core", "ui", "icons")


def version():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    return cfg["general"]["version"]


def main():
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, f"{PACKAGE}-{version()}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in INCLUDE:
            path = os.path.join(ROOT, item)
            if os.path.isfile(path):
                zf.write(path, f"{PACKAGE}/{item}")
                continue
            for folder, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    if name.endswith((".pyc", ".pyo")):
                        continue
                    full = os.path.join(folder, name)
                    rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                    zf.write(full, f"{PACKAGE}/{rel}")
    print(target)


if __name__ == "__main__":
    main()
