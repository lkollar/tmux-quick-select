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

# Built-in pattern categories: (name, regex)
BUILTIN_PATTERNS = {
    "url": r"https?://[^\s>)\"'\]]+",
    "path": r"(?<![/\w])(?:[.\w\-@~]+)?/(?!/)[.\w\-@/]+",
    "relative-path": r"\.{1,2}/[^\s:]+",
    "git-sha": r"\b[0-9a-f]{7,40}\b",
    "ipv4": r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?::\d+)?\b",
}


def compute_labels(alphabet, num_matches):
    """Generate labels using WezTerm's algorithm.

    Single-char labels are preferred. When more labels are needed,
    single-char labels are stolen from the end of the alphabet to
    create two-char prefixed labels.
    """
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


def get_tmux_option(name, default=""):
    """Read a tmux user option, returning default if unset."""
    try:
        val = subprocess.check_output(
            ["tmux", "show-option", "-gqv", name],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return val if val else default
    except subprocess.CalledProcessError:
        return default


def get_disabled_categories():
    """Return set of built-in category names that the user disabled."""
    disabled = set()
    for name in BUILTIN_PATTERNS:
        opt = f"@quick-select-no-{name}"
        if get_tmux_option(opt) == "1":
            disabled.add(name)
    return disabled


def build_patterns():
    """Build the combined regex pattern list."""
    disabled = get_disabled_categories()
    patterns = []
    for name, pat in BUILTIN_PATTERNS.items():
        if name not in disabled:
            patterns.append(pat)

    extra = get_tmux_option("@quick-select-patterns")
    if extra:
        patterns.extend(extra.split())

    return patterns


def capture_pane(pane_id):
    """Capture pane content with joined wrapped lines."""
    logging.debug("capture_pane: pane_id=%s", pane_id)
    output = subprocess.check_output([
        "tmux", "capture-pane",
        "-p",
        "-J",
        "-t", pane_id,
        "-S", "-",
    ]).decode("utf-8", errors="replace")
    logging.debug("capture_pane: got %d bytes, %d lines",
                  len(output), output.count("\n"))
    return output


def find_matches(lines, patterns, height):
    """Find all pattern matches, bottom-to-top, deduplicating by value.

    Returns:
        unique_matches: list of (row, col, text) — one per unique value,
            bottom-to-top so nearest matches get shortest labels
        all_positions: dict mapping text -> list of (row, col) — every
            occurrence for highlighting
    """
    combined = "|".join(f"(?:{p})" for p in patterns)
    logging.debug("find_matches: combined regex=%s", combined)
    regex = re.compile(combined)

    # Only consider the last `height` lines (the visible area)
    if len(lines) > height:
        visible_lines = lines[-height:]
    else:
        visible_lines = lines

    logging.debug("find_matches: scanning %d visible lines (last of %d total)",
                  len(visible_lines), len(lines))

    # Collect all occurrences and unique matches bottom-to-top.
    # Skip matches that overlap with a prior (higher-priority) match
    # on the same line, so e.g. a URL won't also produce a path match.
    unique_matches = []
    all_positions = {}  # text -> [(row, col), ...]
    seen_values = set()
    for row_idx in range(len(visible_lines) - 1, -1, -1):
        line = visible_lines[row_idx]
        raw = list(regex.finditer(line))
        # Filter overlapping: keep non-overlapping matches left-to-right
        # (earlier patterns in the combined regex win at each position)
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

    logging.debug("find_matches: %d unique matches, %d total occurrences",
                  len(unique_matches), sum(len(v) for v in all_positions.values()))
    return unique_matches, all_positions


def copy_to_clipboard(text):
    """Copy text to tmux buffer and best-effort system clipboard."""
    subprocess.run(["tmux", "set-buffer", text], check=False)
    logging.debug("copy_to_clipboard: set tmux buffer")

    # Check for user-configured copy command (e.g. osc52-copy)
    override = get_tmux_option("@override_copy_command")
    if override:
        try:
            subprocess.run(
                override, input=text.encode(), check=True,
                capture_output=True, shell=True,
            )
            logging.debug("copy_to_clipboard: used @override_copy_command=%s", override)
            return
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logging.warning("copy_to_clipboard: @override_copy_command failed: %s", e)

    for cmd in [
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]:
        try:
            subprocess.run(
                cmd, input=text.encode(), check=True,
                capture_output=True,
            )
            logging.debug("copy_to_clipboard: used %s", cmd[0])
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue


def main(stdscr, pane_id, pane_width, pane_height):
    """Main curses UI loop."""
    logging.debug("main: pane=%s width=%d height=%d", pane_id, pane_width, pane_height)
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()
    # Color pair 1: bold yellow on black (labels)
    curses.init_pair(1, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    # Color pair 2: green on black (matched text)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    # Color pair 3: status line
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_WHITE)

    max_y, max_x = stdscr.getmaxyx()
    logging.debug("terminal size: max_y=%d max_x=%d", max_y, max_x)
    height = min(pane_height, max_y - 1)  # Reserve 1 row for status

    patterns = build_patterns()
    logging.debug("patterns: %s", patterns)
    if not patterns:
        logging.warning("no patterns configured, exiting")
        return

    raw = capture_pane(pane_id)
    all_lines = raw.split("\n")
    # Remove trailing empty line from capture
    if all_lines and all_lines[-1] == "":
        all_lines = all_lines[:-1]
    logging.debug("visible lines: %d (height=%d)", len(all_lines), height)

    matches, all_positions = find_matches(all_lines, patterns, height)
    logging.debug("found %d unique matches", len(matches))
    if not matches:
        # No matches — show briefly and exit
        stdscr.addstr(max_y - 1, 0, "  No matches found. Press any key.", curses.A_DIM)
        stdscr.refresh()
        stdscr.getkey()
        return

    labels = compute_labels(ALPHABET, len(matches))
    labeled = OrderedDict()
    for label, (row, col, text) in zip(labels, matches):
        labeled[label] = (row, col, text)

    # Get the visible lines to draw
    if len(all_lines) > height:
        visible_lines = all_lines[-height:]
    else:
        visible_lines = all_lines

    def draw(typed):
        stdscr.erase()

        # Draw pane content (dimmed)
        for row_idx, line in enumerate(visible_lines):
            if row_idx >= max_y - 1:
                break
            text = line[:max_x]
            try:
                stdscr.addstr(row_idx, 0, text, curses.A_DIM)
            except curses.error:
                pass

        # Highlight ALL occurrences of every matched value in green
        for label, (_, _, text) in labeled.items():
            for occ_row, occ_col in all_positions.get(text, []):
                if occ_row >= max_y - 1:
                    continue
                display_text = text[:max_x - occ_col]
                try:
                    stdscr.addstr(
                        occ_row, occ_col, display_text,
                        curses.color_pair(2) | curses.A_BOLD,
                    )
                except curses.error:
                    pass

        # Draw labels (only on the primary/labeled occurrence)
        for label, (row, col, text) in labeled.items():
            if row >= max_y - 1:
                continue
            label_attr = curses.color_pair(1) | curses.A_BOLD
            if typed and not label.startswith(typed):
                label_attr = curses.A_DIM
            try:
                stdscr.addstr(row, col, label, label_attr)
            except curses.error:
                pass

        # Status line
        status = "  tmux-quick-select  [type a label to copy]  ESC to cancel"
        if typed:
            status = f"  tmux-quick-select  [{typed}_]  ESC to cancel"
        try:
            stdscr.addstr(
                max_y - 1, 0,
                status.ljust(max_x),
                curses.color_pair(3),
            )
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

            # Exact match → copy and exit
            if typed in labeled:
                _, _, text = labeled[typed]
                copy_to_clipboard(text)
                break

            # Check if any label starts with typed prefix
            if not any(l.startswith(typed) for l in labeled):
                typed = ""

            draw(typed)
        else:
            # Ignore non-alphabet keys (except ESC/q handled above)
            pass

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
        print(f"Invalid arguments: {sys.argv[1:]}", file=sys.stderr)
        sys.exit(1)

    try:
        curses.wrapper(lambda stdscr: main(stdscr, pane_id, pane_width, pane_height))
    except Exception:
        logging.exception("fatal error")
        raise
