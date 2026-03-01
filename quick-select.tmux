#!/usr/bin/env bash
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check for python3
if ! command -v python3 &>/dev/null; then
    tmux display-message "tmux-quick-select: python3 not found in PATH"
    exit 1
fi

# Check tmux version >= 3.2 (needed for display-popup)
tmux_version=$(tmux -V | sed 's/[^0-9.]//g')
required="3.2"
if printf '%s\n' "$required" "$tmux_version" | sort -V | head -n1 | grep -qv "$required"; then
    tmux display-message "tmux-quick-select: requires tmux >= 3.2 (found $tmux_version)"
    exit 1
fi

key=$(tmux show-option -gqv @quick-select-key)
[ -z "$key" ] && key="f"

tmux bind-key "$key" run-shell -b \
    "tmux display-popup -E -w 100% -h 100% 'python3 $CURRENT_DIR/scripts/quick_select.py #{pane_id} #{pane_width} #{pane_height}'"
