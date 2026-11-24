# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Python side of agent-grange-viz ("agent-grange-viz-core").
#
# Built by scripts/build_app.sh into dist/python/agent-grange-viz-core/ (onedir). The Swift
# app copies that folder into agent-grange-viz.app/Contents/Resources and runs
# agent-grange-viz-core inside its SwiftTerm view. Nothing is signed here: build_app.sh
# signs the whole bundle inside-out afterwards (scripts/sign_app.sh).
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [(os.path.join(ROOT, "VERSION"), ".")], [], []
# textual and rich ship data files and import modules lazily. They are all the app carries
# of anybody else's: the grange and the Docker socket are read with the standard library.
for pkg in ("textual", "rich"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# The app's own modules lie flat beside main.py. PyInstaller finds the ones main.py imports,
# wherever the import is written; naming every module that is there as well means one that
# is only ever reached by its name in a string (a source chosen at run time) is not left out.
OURS = ("apppaths", "settings", "model", "theme", "fmt", "stack", "panels", "news", "farms", "hints",
        "grangeapi", "sidecar", "live", "demo", "demosim")
hiddenimports += [name for name in OURS if os.path.exists(os.path.join(ROOT, name + ".py"))]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="agent-grange-viz-core",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="agent-grange-viz-core")
