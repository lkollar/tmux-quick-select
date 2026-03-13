#!/usr/bin/env python3
"""tmux-quick-select: WezTerm-style Quick Select overlay for tmux."""

import curses
import logging
import os
import re
import subprocess
import sys
from collections import OrderedDict

LOG_PATH = os.path.expanduser("~/.tmux/quick-select.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)

ALPHABET = "asdfghjklqwertyuiopzxcvbnm"

BUILTIN_PATTERNS = {
    "url": r"https?://[^\s>)\"'\]]+",
    "path": r"(?<![/\w])(?:[.\w\-@~]+)?/(?!/)[.\w\-@/]+",
    "relative-path": r"\.{1,2}/[^\s:]+",
    "git-sha": r"\b[0-9a-f]{7,40}\b",
    "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?::\d+)?\b",
}

# Regex to match ANSI CSI SGR sequences
_ANSI_RE = re.compile(r"\033\[([0-9;]*)m")


# --- ANSI parsing ---

def strip_ansi(text):
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def parse_ansi_line(raw_line):
    """Parse a line with ANSI SGR escapes into a list of cell tuples.

    Returns list of (char, fg, bg, attrs) where:
      fg/bg: -1=default, 0-7=basic, 8-15=bright, 16-255=256color
      attrs: bitmask of curses attributes (A_BOLD, A_DIM, etc.)
    """
    cells = []
    fg, bg = -1, -1
    attrs = 0
    pos = 0

    for m in _ANSI_RE.finditer(raw_line):
        # Emit characters before this escape
        for ch in raw_line[pos:m.start()]:
            cells.append((ch, fg, bg, attrs))
        pos = m.end()

        # Parse SGR codes
        params = m.group(1)
        if not params:
            codes = [0]
        else:
            codes = [int(x) if x else 0 for x in params.split(";")]

        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                fg, bg, attrs = -1, -1, 0
            elif c == 1:
                attrs |= curses.A_BOLD
            elif c == 2:
                attrs |= curses.A_DIM
            elif c == 3:
                attrs |= curses.A_ITALIC
            elif c == 4:
                attrs |= curses.A_UNDERLINE
            elif c == 7:
                attrs |= curses.A_REVERSE
            elif 30 <= c <= 37:
                fg = c - 30
            elif 40 <= c <= 47:
                bg = c - 40
            elif 90 <= c <= 97:
                fg = c - 90 + 8
            elif 100 <= c <= 107:
                bg = c - 100 + 8
            elif c == 39:
                fg = -1
            elif c == 49:
                bg = -1
            elif c == 38 and i + 1 < len(codes) and codes[i + 1] == 5:
                if i + 2 < len(codes):
                    fg = codes[i + 2]
                    i += 2
            elif c == 48 and i + 1 < len(codes) and codes[i + 1] == 5:
                if i + 2 < len(codes):
                    bg = codes[i + 2]
                    i += 2
            i += 1

    # Remaining characters after last escape
    for ch in raw_line[pos:]:
        cells.append((ch, fg, bg, attrs))

    return cells


# --- Curses color pair cache ---

class ColorCache:
    """Manages curses color pair allocation on demand."""

    RESERVED = 4  # pairs 1-3 are reserved (label, highlight, status)

    def __init__(self):
        self._map = {}  # (fg, bg) -> pair_number
        self._next = self.RESERVED
        self._max = 0

    def init(self):
        self._max = curses.COLOR_PAIRS - 1
        logging.debug("ColorCache: max pairs=%d", self._max)

    def get_pair(self, fg, bg):
        """Get or allocate a curses color pair for (fg, bg)."""
        key = (fg, bg)
        if key in self._map:
            return self._map[key]

        if self._next > self._max:
            # Out of pairs — fall back to pair 0 (default)
            return 0

        pair_num = self._next
        self._next += 1

        # Map our fg/bg values to curses color numbers
        # -1 = default, 0-7 = basic colors, 8-15 = bright via basic+BOLD
        # 16-255 = 256-color (curses supports if COLORS >= 256)
        c_fg = fg if fg >= 0 else -1
        c_bg = bg if bg >= 0 else -1

        try:
            curses.init_pair(pair_num, c_fg, c_bg)
            self._map[key] = pair_num
        except curses.error:
            return 0

        return pair_num


# --- Label generation ---

def compute_labels(alphabet, num_matches):
    """WezTerm-style label generation."""
    chars = list(alphabet)
    primary = list(chars)
    secondary = []

    while len(primary) + len(secondary) < num_matches:
        if not primary:
            break
        prefix = primary.pop()
        needed = num_matches - len(primary) - len(secondary)
        prefixed = [prefix + c for c in chars[:needed]]
        secondary = prefixed + secondary

    result = primary[: num_matches - len(secondary)] + secondary
    return result[:num_matches]


# --- tmux helpers ---

def get_tmux_option(name, default=""):
    try:
        val = subprocess.check_output(
            ["tmux", "show-option", "-gqv", name],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return val if val else default
    except subprocess.CalledProcessError:
        return default


def get_disabled_categories():
    disabled = set()
    for name in BUILTIN_PATTERNS:
        if get_tmux_option(f"@quick-select-no-{name}") == "1":
            disabled.add(name)
    return disabled


def build_patterns():
    disabled = get_disabled_categories()
    patterns = [pat for name, pat in BUILTIN_PATTERNS.items()
                if name not in disabled]
    extra = get_tmux_option("@quick-select-patterns")
    if extra:
        patterns.extend(extra.split())
    return patterns


def capture_pane(pane_id):
    """Capture pane with ANSI escapes and joined wrapped lines."""
    logging.debug("capture_pane: pane_id=%s", pane_id)
    output = subprocess.check_output([
        "tmux", "capture-pane",
        "-e",  # include ANSI escape sequences
        "-p",
        "-J",
        "-t", pane_id,
        "-S", "-",
    ]).decode("utf-8", errors="replace")
    logging.debug("capture_pane: got %d bytes, %d lines",
                  len(output), output.count("\n"))
    return output


# --- Pattern matching ---

def find_matches(lines, patterns, height):
    """Find matches bottom-to-top, deduplicating by value.

    Returns:
        unique_matches: list of (row, col, text)
        all_positions: dict text -> [(row, col), ...]
    """
    combined = "|".join(f"(?:{p})" for p in patterns)
    regex = re.compile(combined)

    if len(lines) > height:
        visible_lines = lines[-height:]
    else:
        visible_lines = lines

    unique_matches = []
    all_positions = {}
    seen_values = set()
    for row_idx in range(len(visible_lines) - 1, -1, -1):
        line = visible_lines[row_idx]
        raw = list(regex.finditer(line))
        filtered = []
        occupied_end = 0
        for m in sorted(raw, key=lambda m: m.start()):
            if m.start() < occupied_end:
                continue
            filtered.append(m)
            occupied_end = m.end()
        for m in reversed(filtered):
            text = m.group(0)
            all_positions.setdefault(text, []).append((row_idx, m.start()))
            if text not in seen_values:
                seen_values.add(text)
                unique_matches.append((row_idx, m.start(), text))

    logging.debug("find_matches: %d unique, %d total occurrences",
                  len(unique_matches),
                  sum(len(v) for v in all_positions.values()))
    return unique_matches, all_positions


# --- Clipboard ---

def copy_to_clipboard(text):
    subprocess.run(["tmux", "set-buffer", text], check=False)
    logging.debug("copy_to_clipboard: set tmux buffer")

    override = get_tmux_option("@override_copy_command")
    if override:
        try:
            subprocess.run(
                override, input=text.encode(), check=True,
                capture_output=True, shell=True,
            )
            logging.debug("copy_to_clipboard: used @override_copy_command")
            return
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logging.warning("copy_to_clipboard: override failed: %s", e)

    for cmd in [
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]:
        try:
            subprocess.run(cmd, input=text.encode(), check=True,
                           capture_output=True)
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue


# --- Main ---

def main(stdscr, pane_id, pane_width, pane_height):
    logging.debug("main: pane=%s width=%d height=%d",
                  pane_id, pane_width, pane_height)
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()

    # Reserved color pairs
    curses.init_pair(1, curses.COLOR_YELLOW, curses.COLOR_BLACK)   # labels
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)    # highlights
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)    # status

    color_cache = ColorCache()
    color_cache.init()

    max_y, max_x = stdscr.getmaxyx()
    logging.debug("terminal: max_y=%d max_x=%d colors=%d pairs=%d",
                  max_y, max_x, curses.COLORS, curses.COLOR_PAIRS)
    height = min(pane_height, max_y - 1)

    patterns = build_patterns()
    if not patterns:
        logging.warning("no patterns, exiting")
        return

    # Single capture with ANSI escapes
    raw = capture_pane(pane_id)
    ansi_lines = raw.split("\n")
    if ansi_lines and ansi_lines[-1] == "":
        ansi_lines = ansi_lines[:-1]

    # Strip ANSI for regex matching
    plain_lines = [strip_ansi(line) for line in ansi_lines]

    matches, all_positions = find_matches(plain_lines, patterns, height)
    logging.debug("found %d unique matches", len(matches))
    if not matches:
        stdscr.addstr(max_y - 1, 0, "  No matches found. Press any key.",
                      curses.A_DIM)
        stdscr.refresh()
        stdscr.getkey()
        return

    labels = compute_labels(ALPHABET, len(matches))
    labeled = OrderedDict()
    for label, (row, col, text) in zip(labels, matches):
        labeled[label] = (row, col, text)

    # Build set of highlighted cell positions for fast lookup
    # and label positions
    def build_overlay_sets():
        highlight_cells = set()  # (row, col)
        label_cells = {}  # (row, col) -> label_char
        for label, (_, _, text) in labeled.items():
            for occ_row, occ_col in all_positions.get(text, []):
                for c in range(len(text)):
                    highlight_cells.add((occ_row, occ_col + c))
        for label, (row, col, _) in labeled.items():
            for i, ch in enumerate(label):
                label_cells[(row, col + i)] = ch
        return highlight_cells, label_cells

    highlight_cells, label_cells = build_overlay_sets()

    # Get visible ANSI lines (last `height` lines)
    if len(ansi_lines) > height:
        visible_ansi = ansi_lines[-height:]
    else:
        visible_ansi = ansi_lines

    # Pre-parse all visible lines into cell arrays
    parsed_lines = [parse_ansi_line(line) for line in visible_ansi]

    def draw(typed):
        stdscr.erase()

        # Build set of dimmed labels for this typed state
        dimmed_labels = set()
        if typed:
            for label in labeled:
                if not label.startswith(typed):
                    for row, col, _ in [labeled[label]]:
                        for i in range(len(label)):
                            dimmed_labels.add((row, col + i))

        for row_idx, cells in enumerate(parsed_lines):
            if row_idx >= max_y - 1:
                break

            for col_idx, (ch, fg, bg, cell_attrs) in enumerate(cells):
                if col_idx >= max_x:
                    break

                # Check overlay state for this cell
                is_label = (row_idx, col_idx) in label_cells
                is_highlight = (row_idx, col_idx) in highlight_cells
                is_dimmed_label = (row_idx, col_idx) in dimmed_labels

                if is_label and not is_dimmed_label:
                    # Active label: yellow bold
                    ch = label_cells[(row_idx, col_idx)]
                    attr = curses.color_pair(1) | curses.A_BOLD
                elif is_dimmed_label:
                    # Inactive label: just dim
                    ch = label_cells[(row_idx, col_idx)]
                    attr = curses.A_DIM
                elif is_highlight:
                    # Match highlight: green bold, preserve char
                    attr = curses.color_pair(2) | curses.A_BOLD
                else:
                    # Normal cell: original color, dimmed
                    pair = color_cache.get_pair(fg, bg)
                    attr = curses.color_pair(pair) | cell_attrs | curses.A_DIM

                try:
                    stdscr.addstr(row_idx, col_idx, ch, attr)
                except curses.error:
                    pass

        # Status line
        status = "  tmux-quick-select  [type a label to copy]  ESC to cancel"
        if typed:
            status = f"  tmux-quick-select  [{typed}_]  ESC to cancel"
        try:
            stdscr.addstr(max_y - 1, 0, status.ljust(max_x),
                          curses.color_pair(3))
        except curses.error:
            pass

        stdscr.refresh()

    typed = ""
    draw(typed)

    stdscr.nodelay(False)
    stdscr.timeout(-1)

    while True:
        try:
            ch = stdscr.getkey()
        except curses.error:
            continue

        if ch in ("\x1b", "q"):
            break

        if len(ch) == 1 and ch in ALPHABET:
            typed += ch
            if typed in labeled:
                _, _, text = labeled[typed]
                copy_to_clipboard(text)
                break
            if not any(l.startswith(typed) for l in labeled):
                typed = ""
            draw(typed)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <pane_id> <pane_width> <pane_height>",
              file=sys.stderr)
        logging.error("invalid arguments: argv=%s", sys.argv)
        sys.exit(1)

    try:
        pane_id = sys.argv[1]
        pane_width = int(sys.argv[2])
        pane_height = int(sys.argv[3])
    except ValueError:
        logging.error("error parsing arguments")
        sys.exit(1)

    try:
        curses.wrapper(lambda stdscr: main(stdscr, pane_id, pane_width,
                                           pane_height))
    except Exception:
        logging.exception("fatal error")
        raise
