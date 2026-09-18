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
MAGENTA = "\033[95m"


def color_question(text: str) -> str:
    return f"{BOLD}{CYAN}{text}{RESET}"


def color_selected(text: str) -> str:
    return f"{BOLD}{GREEN}{text}{RESET}"


def color_hint(text: str) -> str:
    return f"{DIM}{text}{RESET}"


def color_error(text: str) -> str:
    return f"{RED}{text}{RESET}"


def color_search_border(text: str) -> str:
    return f"{BOLD}{MAGENTA}{text}{RESET}"


def confirmed_line(names: list) -> str:
    return color_selected("Selected: " + ", ".join(str(name) for name in names))


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
    elif key == "none":
        selected = [False] * n
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


@dataclass
class SearchSelectState:
    items: list
    query: str = ""
    cursor: int = 0
    selected: list = None
    done: bool = False
    error: str = ""

    def __post_init__(self):
        if self.selected is None:
            self.selected = [False] * len(self.items)

    def visible_indexes(self) -> list:
        needle = self.query.lower()
        if not needle:
            return list(range(len(self.items)))
        return [i for i, label in enumerate(self.items) if needle in str(label).lower()]

    def indexes(self) -> list:
        return [i for i, on in enumerate(self.selected) if on]


def apply_search_key(state: SearchSelectState, key: str) -> SearchSelectState:
    query = state.query
    cursor = state.cursor
    selected = list(state.selected)
    error = ""
    done = False

    if key.startswith("type:"):
        query += key[5:]
        cursor = 0
    elif key == "backspace":
        query = query[:-1]
        cursor = 0
    elif key == "clear":
        query = ""
        cursor = 0

    visible = [
        i for i, label in enumerate(state.items)
        if (not query.lower() or query.lower() in str(label).lower())
    ]
    if visible:
        cursor = max(0, min(cursor, len(visible) - 1))
    else:
        cursor = 0

    if key == "up" and visible:
        cursor = (cursor - 1) % len(visible)
    elif key == "down" and visible:
        cursor = (cursor + 1) % len(visible)
    elif key == "space" and visible:
        selected[visible[cursor]] = not selected[visible[cursor]]
    elif key == "all" and visible:
        for idx in visible:
            selected[idx] = True
    elif key == "enter":
        if any(selected):
            done = True
        elif visible:
            selected[visible[cursor]] = True
            done = True
        else:
            error = "Select at least one country with space, or type to search."

    return SearchSelectState(
        items=list(state.items),
        query=query,
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
    if ch.lower() == "d":
        return "none"
    return ch.lower()


def _read_search_key() -> str:
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
    if ch == "*":
        return "all"
    if ch in ("\x7f", "\x08"):
        return "backspace"
    if ch == "\x15":
        return "clear"
    if ch.isprintable():
        return f"type:{ch}"
    return "ignore"


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
        color_hint("↑↓ move  ·  space select  ·  a all  ·  d remove all  ·  enter confirm"),
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


def _search_window(visible: list, cursor: int, limit: int = 12) -> list:
    if not visible:
        return []
    start = max(0, cursor - limit // 3)
    end = min(len(visible), start + limit)
    start = max(0, end - limit)
    return visible[start:end]


def _pad_visible(text: str, width: int) -> str:
    if len(text) > width:
        text = "…" + text[-(width - 1):]
    return text + " " * (width - len(text))


def _search_box_lines(query: str, width: int = 46) -> list:
    """Draw a magenta-bordered search box around the current query."""
    inner_w = max(16, width - 4)
    if query:
        inner = _pad_visible(query + "█", inner_w)
        inner_colored = inner
    else:
        inner = _pad_visible("Type a country name…", inner_w)
        inner_colored = color_hint(inner)
    label = " Search "
    dash_count = max(1, width - 3 - len(label))
    top = "╭─" + label + "─" * dash_count + "╮"
    mid = color_search_border("│ ") + inner_colored + color_search_border(" │")
    bot = "╰" + "─" * (width - 2) + "╯"
    return [
        color_search_border(top),
        mid,
        color_search_border(bot),
    ]


def _selected_names(state: SearchSelectState) -> list:
    return [state.items[i] for i, on in enumerate(state.selected) if on]


def _selected_summary_line(state: SearchSelectState, limit: int = 12) -> str:
    names = [str(name) for name in _selected_names(state)]
    if not names:
        return color_hint("Selected: none")
    extra = ""
    if len(names) > limit:
        extra = f"  (+{len(names) - limit} more)"
        names = names[:limit]
    return color_selected("Selected: " + ", ".join(names) + extra)


def _search_lines(title: str, state: SearchSelectState) -> list:
    visible = state.visible_indexes()
    match_word = "match" if len(visible) == 1 else "matches"
    lines = [
        "",
        color_question(title),
        color_hint("All countries  ·  type to filter  ·  space select  ·  * visible  ·  enter confirm"),
        *_search_box_lines(state.query),
        _selected_summary_line(state),
        color_hint(f"{len(visible)} {match_word}"),
    ]
    if state.error:
        lines.append(color_error(state.error))
    if not visible:
        lines.append(color_hint("No countries match."))
        return lines
    window = _search_window(visible, state.cursor)
    for orig_i in window:
        on = state.selected[orig_i]
        is_cur = visible[state.cursor] == orig_i if visible else False
        mark = "[x]" if on else "[ ]"
        arrow = "❯" if is_cur else " "
        row = f" {arrow} {mark} {state.items[orig_i]}"
        lines.append(color_selected(row) if on or is_cur else row)
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

    indexes = _run_raw(loop)
    print()
    print(confirmed_line([items[i] for i in indexes]))
    return indexes


def pick_searchable(title: str, items: list) -> list:
    """Filterable checkbox list. Returns selected 0-based indexes."""
    state = SearchSelectState(items=list(items))

    def loop():
        nonlocal state
        height = 0
        height = _draw(_search_lines(title, state), height)
        while not state.done:
            key = _read_search_key()
            if key == "ignore":
                continue
            state = apply_search_key(state, key)
            height = _draw(_search_lines(title, state), height)
        return state.indexes()

    indexes = _run_raw(loop)
    print()
    print(confirmed_line([items[i] for i in indexes]))
    return indexes


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
