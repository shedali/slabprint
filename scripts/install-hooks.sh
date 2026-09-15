#!/usr/bin/env bash
# Install the repository's git hooks.
#
# Copies into .git/hooks rather than setting core.hooksPath, so that a global
# hooks path (a shared secret scanner, say) still runs and chains here.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"

# NOT `git rev-parse --git-path hooks`: that honours core.hooksPath, so on a
# machine with a global hooks directory it resolves outside this repo — and if
# that directory is read-only the install fails while the commit gate silently
# does nothing. Always target this repository's own hooks directory.
hooks_dir="$(git rev-parse --git-dir)/hooks"
mkdir -p "$hooks_dir"

for hook in "$root"/.githooks/*; do
  name="$(basename "$hook")"
  install -m 755 "$hook" "$hooks_dir/$name"
  echo "installed $name"
done
