# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: NCZ -> DXF donusturucu tek dosya exe.
#
# Build (izole venv, kullanicinin global Store Python'unda eskimis
# 'pathlib' backport paketi PyInstaller ile catisiyor -- bkz. build_exe.ps1):
#   .\.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm NCZ2DXF.spec

a = Analysis(
    ["ncz2dxf.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["ncz_pure_parser", "ncztool"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NCZ2DXF",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
