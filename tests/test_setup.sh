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
python3 - "$work/fresh/.claude.json" claude/mcp.json <<'PYTHON' || fail ".claude.json does not hold the bundle's MCP servers"
import json, sys
installed, bundled = (json.load(open(p)) for p in sys.argv[1:3])
assert installed == bundled
PYTHON
[[ "$(stat -c %a "$work/fresh/.claude.json")" == 600 ]] || fail ".claude.json is readable by others"
sha="$(sed -n 's/^bundle_sha256="\(.*\)"$/\1/p' dist/setup.sh)"
[[ "$(cat "$work/fresh/hooks/arikkfir/.bundle")" == "$sha" ]] || fail "installed bundle not recorded"
before="$(snapshot "$work/fresh")"
ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/fresh"
[[ "$before" == "$(snapshot "$work/fresh")" ]] || fail "second run changed the installation"

# Concurrent installs take turns: each leaves a complete hooks/arikkfir behind, and no staging directories.
for round in 1 2 3; do
  pids=()
  for _ in $(seq 8); do ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/concurrent$round" > /dev/null & pids+=($!); done
  for pid in "${pids[@]}"; do wait "$pid" || fail "a concurrent install failed"; done
  [[ "$(snapshot "$work/concurrent$round")" == "$before" ]] || fail "concurrent installs left a broken installation"
done

# bundle.py leaves the published bundle alone, and reinstalls it over a different one.
refresh() {
  CLAUDE_CODE_REMOTE=true CLAUDE_CONFIG_DIR="$1" ARIKKFIR_CLAUDE_BASE_URL="http://127.0.0.1:${port}" TMPDIR="$work" \
    python3 "$1/hooks/arikkfir/bundle.py" < /dev/null
}
refresh "$work/fresh"
[[ "$before" == "$(snapshot "$work/fresh")" ]] || fail "bundle.py changed the current bundle"
echo "0000000000000000000000000000000000000000000000000000000000000000" > "$work/fresh/hooks/arikkfir/.bundle"
rm "$work/fresh/CLAUDE.md"
refresh "$work/fresh"
[[ "$before" == "$(snapshot "$work/fresh")" ]] || fail "bundle.py did not reinstall the published bundle"

# Existing settings are kept and bundle values win; stale bundle hooks go, foreign hooks stay.
mkdir -p "$work/existing/hooks/arikkfir" "$work/existing/hooks/mine"
echo '{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}' \
  > "$work/existing/settings.json"
touch "$work/existing/hooks/arikkfir/stale.py" "$work/existing/hooks/mine/keep.sh"
echo '{"numStartups": 3, "mcpServers": {"mine": {"type": "http", "url": "https://example.com/mcp"},
  "gke": {"type": "http", "url": "https://example.com/old"}}}' > "$work/existing/.claude.json"
chmod 0640 "$work/existing/.claude.json"
ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/existing"
python3 - "$work/existing/settings.json" <<'PYTHON' || fail "existing settings were not merged"
import json, sys
settings = json.load(open(sys.argv[1]))
assert settings["model"] == "opus"
assert set(settings["hooks"]) == {"Stop", "SessionStart", "PreToolUse", "PostToolUse"}
PYTHON
python3 - "$work/existing/.claude.json" claude/mcp.json <<'PYTHON' || fail "MCP servers were not merged into .claude.json"
import json, sys
config, bundled = (json.load(open(p)) for p in sys.argv[1:3])
assert config["numStartups"] == 3
assert config["mcpServers"] == {"mine": {"type": "http", "url": "https://example.com/mcp"}, **bundled["mcpServers"]}
PYTHON
[[ "$(stat -c %a "$work/existing/.claude.json")" == 640 ]] || fail ".claude.json lost its mode"
[[ ! -e "$work/existing/hooks/arikkfir/stale.py" ]] || fail "stale bundle hook kept"
[[ -e "$work/existing/hooks/mine/keep.sh" ]] || fail "foreign hook removed"

# A tampered bundle is rejected: strict mode fails, default mode warns, exits 0 and installs nothing.
cp -r dist "$work/tampered"
for bundle in "$work"/tampered/bundles/*.tar.gz; do printf 'x' >> "$bundle"; done
serve "$work/tampered"
if ARIKKFIR_CLAUDE_STRICT=1 install_into "$work/strict" 2> /dev/null; then fail "strict mode accepted a tampered bundle"; fi
ARIKKFIR_CLAUDE_STRICT=0 install_into "$work/lenient" 2> /dev/null || fail "default mode failed instead of warning"
[[ ! -e "$work/lenient/CLAUDE.md" && ! -e "$work/lenient/.claude.json" ]] || fail "tampered bundle was installed"

echo "setup.sh tests passed"
