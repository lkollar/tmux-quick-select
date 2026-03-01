# tmux-quick-select

WezTerm-style Quick Select mode for tmux. Overlays short keyboard labels on
URLs, file paths, git SHAs, and other patterns in your pane. Type a label to
copy the match to your clipboard.

## Requirements

- tmux >= 3.2 (for `display-popup`)
- Python 3.6+

## Installation

### With [TPM](https://github.com/tmux-plugins/tpm)

Add to `~/.tmux.conf`:

```tmux
set -g @plugin 'lkollar/tmux-quick-select'
```

Then press `prefix + I` to install.

### Manual

```bash
git clone https://github.com/lkollar/tmux-quick-select ~/.tmux/plugins/tmux-quick-select
```

Add to `~/.tmux.conf`:

```tmux
run-shell ~/.tmux/plugins/tmux-quick-select/quick-select.tmux
```

## Usage

Press `prefix + f` to activate Quick Select. Labels appear on each match.
Type the label character(s) to copy that match to your clipboard (and tmux
buffer). Press `ESC` or `q` to cancel.

If there are more matches than letters in the alphabet, two-character labels
are used automatically.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `@quick-select-key` | `f` | Keybinding (after prefix) |
| `@quick-select-patterns` | _(none)_ | Space-separated extra regexes |
| `@quick-select-no-url` | `0` | Set to `1` to disable URL matching |
| `@quick-select-no-path` | `0` | Disable absolute path matching |
| `@quick-select-no-relative-path` | `0` | Disable `./` relative paths |
| `@quick-select-no-git-sha` | `0` | Disable git SHA matching |
| `@quick-select-no-ipv4` | `0` | Disable IPv4 address matching |

Example — add a custom pattern and disable git SHAs:

```tmux
set -g @quick-select-patterns 'TODO:[^\s]+'
set -g @quick-select-no-git-sha 1
```

## How it works

1. Captures the current pane (with `-J` to join wrapped lines)
2. Runs regex patterns against visible content
3. Assigns labels bottom-to-top so nearest matches get shortest labels
4. Renders a curses overlay with dimmed pane text and highlighted labels
5. On label match, copies to tmux buffer + system clipboard

## Comparison with tmux-fzf-url

| Feature | tmux-quick-select | tmux-fzf-url |
|---------|-------------------|--------------|
| Selection UI | Inline labels on pane content | fzf picker in popup |
| Visual context | See matches in place | Matches listed out of context |
| Speed | Single/double keypress | Navigate fzf list |
| Pattern types | URLs, paths, SHAs, IPs, custom | URLs only (by default) |
| Dependencies | Python 3 (stdlib only) | fzf, bash |
| Pane colours | Not reproduced (by design) | N/A |

## Known limitations

- Pane ANSI colours are not reproduced in the overlay — text is shown dimmed
  with coloured labels. This is intentional for readability.
- No mouse support (planned for v2).
- Copy-only action; open-in-browser planned for v2 via `@quick-select-action`.

## License

MIT
