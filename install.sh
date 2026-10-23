#!/usr/bin/env bash
# Links the gifetch command into ~/.local/bin. `./install.sh --uninstall` removes the link.
# Saved presets in ~/.config/gifetch are left alone either way.
set -euo pipefail
target="$HOME/.local/bin/gifetch"
source="$(cd "$(dirname "$0")" && pwd)/gifetch.py"

case "${1:-}" in
  "") ;;
  --uninstall)
    # Only remove our own link, never a file or another program's link of the same name.
    if [[ -L $target && $(readlink -f "$target") == "$(readlink -f "$source")" ]]; then
      rm "$target"
      echo "Removed $target. Presets stay in ${XDG_CONFIG_HOME:-$HOME/.config}/gifetch."
    elif [[ -e $target || -L $target ]]; then
      echo "error: $target does not point to $source, leaving it alone" >&2
      exit 1
    else
      echo "Nothing to remove: $target does not exist."
    fi
    exit 0
    ;;
  *)
    echo "usage: $0 [--uninstall]" >&2
    exit 2
    ;;
esac

if [[ -e $target || -L $target ]] && [[ $(readlink -f "$target") != "$(readlink -f "$source")" ]]; then
  echo "error: $target already exists and is not ours, not overwriting it" >&2
  exit 1
fi
mkdir -p "$HOME/.local/bin"
ln -sfn "$source" "$target"
echo "Linked $target -> $source"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "note: ~/.local/bin is not on PATH, add it to call gifetch by name" ;;
esac
