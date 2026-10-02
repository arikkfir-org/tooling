#!/usr/bin/env bash
# End-to-end test of dist/setup.sh: serves dist/ over HTTP and installs into temporary config directories.
# Run scripts/build.sh first.
set -euo pipefail

cd "$(dirname "$0")/.."
work="$(mktemp -d)"
port=18765
server=""
trap '[[ -n "$server" ]] && kill "$server"; rm -rf "$work"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

serve() {
  [[ -n "$server" ]] && kill "$server" && wait "$server" 2> /dev/null || true
  python3 -m http.server --bind 127.0.0.1 --directory "$1" "$port" > /dev/null 2>&1 &
  server=$!
  for _ in $(seq 50); do curl -fs "http://127.0.0.1:${port}/setup.sh" > /dev/null && return; sleep 0.1; done
  fail "HTTP server did not start"
}

install_into() {
  CLAUDE_CONFIG_DIR="$1" ARIKKFIR_CLAUDE_BASE_URL="http://127.0.0.1:${port}" bash dist/setup.sh
}

snapshot() { (cd "$1" && find . -type f -exec sha256sum {} + | sort); }

serve dist

# Fresh install, then a second run changes nothing.
ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/fresh"
[[ -f "$work/fresh/CLAUDE.md" ]] || fail "CLAUDE.md not installed"
[[ -x "$work/fresh/hooks/arikkfir/guard.py" && -x "$work/fresh/hooks/arikkfir/format.py" ]] || fail "hooks not installed"
python3 - "$work/fresh/settings.json" claude/settings.json <<'PYTHON' || fail "settings.json differs from the bundle"
import json, sys
installed, bundled = (json.load(open(p)) for p in sys.argv[1:3])
assert installed == bundled
PYTHON
before="$(snapshot "$work/fresh")"
ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/fresh"
[[ "$before" == "$(snapshot "$work/fresh")" ]] || fail "second run changed the installation"

# Existing settings are kept and bundle values win; stale bundle hooks go, foreign hooks stay.
mkdir -p "$work/existing/hooks/arikkfir" "$work/existing/hooks/mine"
echo '{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}' \
  > "$work/existing/settings.json"
touch "$work/existing/hooks/arikkfir/stale.py" "$work/existing/hooks/mine/keep.sh"
ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/existing"
python3 - "$work/existing/settings.json" <<'PYTHON' || fail "existing settings were not merged"
import json, sys
settings = json.load(open(sys.argv[1]))
assert settings["model"] == "opus"
assert set(settings["hooks"]) == {"Stop", "SessionStart", "PreToolUse", "PostToolUse"}
PYTHON
[[ ! -e "$work/existing/hooks/arikkfir/stale.py" ]] || fail "stale bundle hook kept"
[[ -e "$work/existing/hooks/mine/keep.sh" ]] || fail "foreign hook removed"

# A tampered bundle is rejected: strict mode fails, default mode warns, exits 0 and installs nothing.
cp -r dist "$work/tampered"
for bundle in "$work"/tampered/bundles/*.tar.gz; do printf 'x' >> "$bundle"; done
serve "$work/tampered"
if ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/strict" 2> /dev/null; then fail "strict mode accepted a tampered bundle"; fi
install_into "$work/lenient" 2> /dev/null || fail "default mode failed instead of warning"
[[ ! -e "$work/lenient/CLAUDE.md" ]] || fail "tampered bundle was installed"

echo "setup.sh tests passed"
