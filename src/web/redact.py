"""
redact.py — Strip personal data before the user shares a log excerpt
=====================================================================
"Report a problem" opens a prefilled GitHub issue in the user's browser. The
user sees and submits it themselves; this makes sure the text never contains
their home path, user name, e-mail addresses, secrets or full search URLs.
"""

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# name=value / name: value / "name": "value", where the name contains a secret-ish word
_SECRET = re.compile(
    r"""(?i)([\w-]*(?:token|key|secret|password|passwd)[\w-]*|(?<![A-Za-z0-9])(?:[\w-]*[_-])?pass(?![A-Za-z])[\w-]*)(["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,"'&;}]+)""")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_URL = re.compile(r"(https?://[^/\s?#]+)[^\s]*")


def redact(text: str, *, home: str, user: str) -> str:
    if home:
        text = text.replace(home, "~")
    text = _EMAIL.sub("<email>", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} <hidden>", text)
    text = _SECRET.sub(lambda m: f"{m.group(1)}=<hidden>", text)
    text = _URL.sub(lambda m: m.group(1), text)
    if user:
        text = re.sub(rf"(?<![\w.-]){re.escape(user)}(?![\w.-])", "<user>", text)
    return text
