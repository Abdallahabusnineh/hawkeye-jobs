"""Terminal pickers: arrows to move, space to select, enter to confirm."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"


def color_question(text: str) -> str:
    return f"{BOLD}{CYAN}{text}{RESET}"


def color_selected(text: str) -> str:
    return f"{BOLD}{GREEN}{text}{RESET}"


def color_hint(text: str) -> str:
    return f"{DIM}{text}{RESET}"


def color_error(text: str) -> str:
    return f"{RED}{text}{RESET}"


try:
    import termios
    import tty
except ImportError:  # Windows
    termios = None
    tty = None


@dataclass
class MultiSelectState:
    items: list
    cursor: int = 0
    selected: list = None
    done: bool = False
    error: str = ""

    def __post_init__(self):
        if self.selected is None:
            self.selected = [False] * len(self.items)

    def indexes(self) -> list:
        return [i for i, on in enumerate(self.selected) if on]


@dataclass
class SingleSelectState:
    items: list
    cursor: int = 0
    done: bool = False
    choice: int = field(default=-1)


def apply_multiselect_key(state: MultiSelectState, key: str) -> MultiSelectState:
    n = len(state.items)
    if n == 0:
        return state
    cursor = state.cursor
    selected = list(state.selected)
    error = ""
    done = False

    if key == "up":
        cursor = (cursor - 1) % n
    elif key == "down":
        cursor = (cursor + 1) % n
    elif key == "space":
        selected[cursor] = not selected[cursor]
    elif key == "all":
        selected = [True] * n
    elif key == "enter":
        if any(selected):
            done = True
        else:
            error = "Select at least one with space, or press a for all."
    return MultiSelectState(
        items=list(state.items),
        cursor=cursor,
        selected=selected,
        done=done,
        error=error,
    )


def apply_singleselect_key(state: SingleSelectState, key: str) -> SingleSelectState:
    n = len(state.items)
    if n == 0:
        return state
    cursor = state.cursor
    done = False
    choice = -1

    if key == "up":
        cursor = (cursor - 1) % n
    elif key == "down":
        cursor = (cursor + 1) % n
    elif key in ("enter", "space"):
        done = True
        choice = cursor
    elif len(key) == 1:
        for i, item in enumerate(state.items):
            if str(item).lower().startswith(key.lower()):
                return SingleSelectState(items=list(state.items), cursor=i, done=True, choice=i)
    return SingleSelectState(items=list(state.items), cursor=cursor, done=done, choice=choice)


def _read_key() -> str:
    ch = sys.stdin.read(1)
    if ch == "\x03":
        raise KeyboardInterrupt
    if ch == "\x1b":
        nxt = sys.stdin.read(1)
        if nxt == "[":
            arrow = sys.stdin.read(1)
            return {"A": "up", "B": "down"}.get(arrow, "esc")
        return "esc"
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch.lower() == "a":
        return "all"
    return ch.lower()


def _draw(lines: list, prev_count: int) -> int:
    if prev_count:
        sys.stdout.write(f"\x1b[{prev_count}F")
    for line in lines:
        sys.stdout.write("\x1b[2K\r" + line + "\n")
    extra = prev_count - len(lines)
    for _ in range(max(0, extra)):
        sys.stdout.write("\x1b[2K\n")
    if extra > 0:
        sys.stdout.write(f"\x1b[{extra}F")
    sys.stdout.flush()
    return len(lines)


def _multi_lines(title: str, state: MultiSelectState) -> list:
    lines = [
        "",
        color_question(title),
        color_hint("↑↓ move  ·  space select  ·  a all  ·  enter confirm"),
    ]
    if state.error:
        lines.append(color_error(state.error))
    for i, item in enumerate(state.items):
        on = state.selected[i]
        mark = "[x]" if on else "[ ]"
        arrow = "❯" if i == state.cursor else " "
        row = f" {arrow} {mark} {item}"
        lines.append(color_selected(row) if on else row)
    return lines


def _single_lines(title: str, state: SingleSelectState) -> list:
    lines = [
        "",
        color_question(title),
        color_hint("↑↓ move  ·  enter confirm"),
    ]
    for i, item in enumerate(state.items):
        on_cursor = i == state.cursor
        arrow = "❯" if on_cursor else " "
        row = f" {arrow} {item}"
        lines.append(color_selected(row) if on_cursor else row)
    return lines


def _run_raw(draw_loop):
    if termios is None or not sys.stdin.isatty():
        raise RuntimeError("interactive picker needs a TTY")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        return draw_loop()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def pick_many(title: str, items: list) -> list:
    """Checkbox list. Returns selected 0-based indexes."""
    state = MultiSelectState(items=list(items))

    def loop():
        nonlocal state
        height = 0
        height = _draw(_multi_lines(title, state), height)
        while not state.done:
            state = apply_multiselect_key(state, _read_key())
            height = _draw(_multi_lines(title, state), height)
        return state.indexes()

    return _run_raw(loop)


def pick_one(title: str, items: list, default: int = 0) -> int:
    """Radio list. Returns the chosen 0-based index."""
    cursor = default if 0 <= default < len(items) else 0
    state = SingleSelectState(items=list(items), cursor=cursor)

    def loop():
        nonlocal state
        height = 0
        height = _draw(_single_lines(title, state), height)
        while not state.done:
            state = apply_singleselect_key(state, _read_key())
            height = _draw(_single_lines(title, state), height)
        return state.choice

    return _run_raw(loop)
