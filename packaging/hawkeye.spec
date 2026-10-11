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

datas = [(str(SRC / "web" / "static"), "web/static"), (str(ROOT / "packaging" / "icon.png"), ".")]
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
# pystray picks its tray backend at runtime.
hiddenimports += {"darwin": ["pystray._darwin"], "win32": ["pystray._win32"]}.get(
    sys.platform, ["pystray._xorg", "pystray._appindicator", "pystray._gtk"])

# tls_client ships its Go library for every OS and loads one by name at import. Bundle only this
# OS's files, taken straight from the package folder: the others are not valid binaries here and
# break the build, and the import scan mistakes the .so files for modules (so it may skip them).
import tls_client
_TLS_DIR = Path(tls_client.__file__).parent / "dependencies"
_TLS_EXT = {"darwin": ".dylib", "win32": ".dll"}.get(sys.platform, ".so")
hiddenimports = [h for h in hiddenimports if not h.startswith("tls_client.dependencies.tls-client")]
binaries = [b for b in binaries if "tls-client-" not in Path(b[0]).name]
datas = [d for d in datas if "tls-client-" not in Path(d[0]).name]
datas += [(str(p), "tls_client/dependencies") for p in sorted(_TLS_DIR.glob("tls-client-*" + _TLS_EXT))]

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
