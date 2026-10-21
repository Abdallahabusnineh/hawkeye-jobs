# PyInstaller spec for the Hawkeye Jobs desktop app.
#
#   pyinstaller packaging/hawkeye.spec --noconfirm     (from the repo root)
#
# Output: dist/Hawkeye Jobs.app (macOS) or dist/Hawkeye Jobs/ (Windows, Linux).
# One folder, not one file: it starts faster and antivirus tools flag it less.

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
NAME = "Hawkeye Jobs"
# macOS release builds: sign every binary with the Developer ID and the Hardened Runtime (needed for
# notarization). Unset → unsigned build (PyInstaller's ad-hoc signature), as for local development.
SIGN_IDENTITY = os.environ.get("MACOS_SIGN_IDENTITY") or None
ENTITLEMENTS = str(ROOT / "packaging" / "macos" / "entitlements.plist") if SIGN_IDENTITY else None
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "version.py").read_text()).group(1)

datas = [(str(SRC / "web" / "static"), "web/static"), (str(ROOT / "packaging" / "icon.png"), "."),
         (str(SRC / "data" / "countries.json"), "data")]
binaries = []
hiddenimports = collect_submodules("uvicorn")

# Packages with data files, native libraries or dynamic imports that the import scan misses.
for package in ("jobspy", "curl_cffi", "webview", "desktop_notifier",
                "undetected_chromedriver", "selenium"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
if sys.platform == "win32":
    hiddenimports += collect_submodules("winrt")
# pystray picks its tray backend at runtime.
hiddenimports += {"darwin": ["pystray._darwin"], "win32": ["pystray._win32"]}.get(
    sys.platform, ["pystray._xorg", "pystray._appindicator", "pystray._gtk"])


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
    codesign_identity=SIGN_IDENTITY,
    entitlements_file=ENTITLEMENTS,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(ROOT / "packaging" / "icon.png"),
        bundle_identifier="io.github.abdallahabusnineh.hawkeyejobs",
        codesign_identity=SIGN_IDENTITY,
        entitlements_file=ENTITLEMENTS,
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
