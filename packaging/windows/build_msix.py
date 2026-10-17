"""Build the Microsoft Store package (MSIX) from the PyInstaller folder.

    python packaging/windows/build_msix.py --out out/HawkeyeJobs-Windows-Store.msix   (Windows, after PyInstaller)
    python packaging/windows/build_msix.py --layout-only build/msix                  (any OS: files only)

The package is unsigned: Partner Center re-signs it with Microsoft's certificate, which is what
lets it run under Smart App Control. To install it locally, sign a copy (see test_msix.ps1).
Needs the Windows SDK (makeappx.exe, makepri.exe) and Pillow.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "msix" / "AppxManifest.xml"
ICON = ROOT / "packaging" / "icon.png"
DIST = ROOT / "dist" / "Hawkeye Jobs"

# name -> (width, height) at scale 100; each is written at these scales, plus the taskbar sizes below.
LOGOS = {"Square44x44Logo": (44, 44), "Square150x150Logo": (150, 150),
         "Wide310x150Logo": (310, 150), "StoreLogo": (50, 50)}
SCALES = (100, 200, 400)
TARGET_SIZES = (16, 24, 32, 48, 256)  # Square44x44Logo at exact pixel sizes (taskbar, Start, Alt+Tab)


def store_version(version: str) -> str:
    """The Store wants four numbers and the last one must be 0: 1.2.0 -> 1.2.0.0."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"expected a version like 1.2.0, got {version!r}")
    return ".".join(parts) + ".0"


def app_version() -> str:
    text = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def _draw(icon: Image.Image, width: int, height: int) -> Image.Image:
    """The icon centred on a transparent canvas (square logos fill it; the wide tile keeps it square)."""
    side = min(width, height)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(icon.resize((side, side), Image.LANCZOS), ((width - side) // 2, (height - side) // 2))
    return canvas


def write_assets(folder: Path) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    icon = Image.open(ICON).convert("RGBA")
    written = []
    for name, (width, height) in LOGOS.items():
        for scale in SCALES:
            path = folder / f"{name}.scale-{scale}.png"
            _draw(icon, width * scale // 100, height * scale // 100).save(path)
            written.append(path)
    for size in TARGET_SIZES:
        for suffix in ("", "_altform-unplated"):
            path = folder / f"Square44x44Logo.targetsize-{size}{suffix}.png"
            _draw(icon, size, size).save(path)
            written.append(path)
    return written


def write_layout(layout: Path, version: str, dist: Path = DIST) -> Path:
    """The folder makeappx packs: the app, AppxManifest.xml and Assets/."""
    if layout.exists():
        shutil.rmtree(layout)
    if dist.is_dir():
        shutil.copytree(dist, layout)
    else:
        layout.mkdir(parents=True)
    manifest = MANIFEST.read_text(encoding="utf-8").replace("{version}", store_version(version))
    (layout / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
    write_assets(layout / "Assets")
    return layout


def _sdk_tool(name: str) -> str:
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    found = sorted(kits.glob(f"10.*/x64/{name}"), key=lambda p: [int(n) for n in p.parent.parent.name.split(".")])
    if not found:
        raise SystemExit(f"{name} not found: install the Windows SDK")
    return str(found[-1])


def _run(argv) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def pack(layout: Path, out: Path, work: Path) -> None:
    makepri, makeappx = _sdk_tool("makepri.exe"), _sdk_tool("makeappx.exe")
    config = work / "priconfig.xml"
    # resources.pri lets Windows pick the right logo size for each screen (the .scale-/.targetsize- files).
    # Index only the manifest and Assets/: makepri reads folder names in the app (e.g. a library's
    # "en" folder) as language qualifiers, and indexing thousands of files is slow.
    resources = work / "pri-project"
    if resources.exists():
        shutil.rmtree(resources)
    shutil.copytree(layout / "Assets", resources / "Assets")
    shutil.copy2(layout / "AppxManifest.xml", resources / "AppxManifest.xml")
    _run([makepri, "createconfig", "/cf", str(config), "/dq", "en-US", "/pv", "10.0.0", "/o"])
    _run([makepri, "new", "/pr", str(resources), "/cf", str(config), "/mn", str(resources / "AppxManifest.xml"),
          "/of", str(layout / "resources.pri"), "/o"])
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([makeappx, "pack", "/d", str(layout), "/p", str(out), "/o", "/h", "SHA256"])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=None, help="app version (default: src/version.py)")
    parser.add_argument("--dist", type=Path, default=DIST)
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "HawkeyeJobs-Windows-Store.msix")
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "msix-work")
    parser.add_argument("--layout-only", type=Path, metavar="FOLDER", help="write the layout here and stop")
    args = parser.parse_args(argv)
    version = args.version or app_version()
    if args.layout_only:
        write_layout(args.layout_only, version, args.dist)
        return
    if not (args.dist / "Hawkeye Jobs.exe").is_file():
        raise SystemExit(f"{args.dist} has no Hawkeye Jobs.exe: run PyInstaller first")
    args.work.mkdir(parents=True, exist_ok=True)
    layout = write_layout(args.work / "layout", version, args.dist)
    pack(layout, args.out, args.work)
    print(f"Wrote {args.out} (version {store_version(version)})")


if __name__ == "__main__":
    main()
