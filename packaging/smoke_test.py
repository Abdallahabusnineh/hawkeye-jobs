"""Smoke test for a PyInstaller build (run from the repo root after building).

    python packaging/smoke_test.py                 # the build in dist/
    python packaging/smoke_test.py PATH_TO_APP     # an installed app or an AppImage

1. `--self-test`: bundled data and libraries load (exit code 0).
2. `--no-window`: the app starts, serves the window files, and guards the API.
Uses a throwaway data folder; makes no searches.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

DIST = Path("dist")


def executable() -> Path:
    if sys.platform == "darwin":
        return DIST / "Hawkeye Jobs.app" / "Contents" / "MacOS" / "Hawkeye Jobs"
    if sys.platform == "win32":
        return DIST / "Hawkeye Jobs" / "Hawkeye Jobs.exe"
    return DIST / "Hawkeye Jobs" / "Hawkeye Jobs"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> None:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else executable()
    env = {**os.environ, "HAWKEYE_DATA_DIR": tempfile.mkdtemp(prefix="hawkeye-smoke-"),
           "GMAIL_USER": "", "GMAIL_PASS": ""}

    result = subprocess.run([str(exe), "--self-test"], env=env, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        sys.exit(f"--self-test failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    print("self-test ok")

    port = free_port()
    proc = subprocess.Popen([str(exe), "--no-window", "--port", str(port)], env=env)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while True:
            try:
                if status(base + "/") == 200:
                    break
            except OSError:
                pass
            if proc.poll() is not None:
                sys.exit(f"the app exited early with code {proc.returncode}")
            if time.monotonic() > deadline:
                sys.exit("the app did not start within 120 s")
            time.sleep(1)
        checks = {"/": 200, "/i18n/ar.json": 200, "/vendor/alpine.min.js": 200, "/api/state": 403}
        for path, expected in checks.items():
            got = status(base + path)
            if got != expected:
                sys.exit(f"{path}: expected {expected}, got {got}")
        print("server ok")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
