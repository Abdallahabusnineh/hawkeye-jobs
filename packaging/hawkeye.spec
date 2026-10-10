# PyInstaller spec for the Hawkeye Jobs desktop app.
#
#   pyinstaller packaging/hawkeye.spec --noconfirm     (from the repo root)
#
# Output: dist/Hawkeye Jobs.app (macOS) or dist/Hawkeye Jobs/ (Windows, Linux).
# One folder, not one file: it starts faster and antivirus tools flag it less.

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
NAME = "Hawkeye Jobs"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "version.py").read_text()).group(1)

datas = [(str(SRC / "web" / "static"), "web/static")]
binaries = []
hiddenimports = collect_submodules("uvicorn")

# Packages with data files, native libraries or dynamic imports that the import scan misses.
for package in ("jobspy", "tls_client", "babel", "webview", "desktop_notifier",
                "undetected_chromedriver", "selenium"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
if sys.platform == "win32":
    hiddenimports += collect_submodules("winrt")

# tls_client ships its Go library for every OS; keep only the one this machine loads (the others
# are not valid binaries here and break the build). Its .so files also look like modules to the scan.
_TLS_EXT = {"darwin": ".dylib", "win32": ".dll"}.get(sys.platform, ".so")
hiddenimports = [h for h in hiddenimports if not h.startswith("tls_client.dependencies.tls-client")]
_tls = [(src, dest) for src, dest in binaries + datas if "tls-client-" in Path(src).name]
binaries = [b for b in binaries if b not in _tls]
datas = [d for d in datas if d not in _tls]
datas += sorted({(src, dest) for src, dest in _tls if src.endswith(_TLS_EXT)})

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(ROOT / "packaging" / "icon.png"),
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(ROOT / "packaging" / "icon.png"),
        bundle_identifier="io.github.abdallahabusnineh.hawkeyejobs",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
