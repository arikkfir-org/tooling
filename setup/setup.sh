#!/usr/bin/env bash
# Installs the arikkfir Claude Code bundle (user-level CLAUDE.md, settings and hooks) into ~/.claude.
#
# Use it as the setup script of a Claude Code on the web environment:
#   curl -fsSL https://storage.googleapis.com/arikkfir-claude/setup.sh | bash
#
# Safe to run repeatedly. Failures only warn, so a missing bundle never blocks a session from starting; set
# ARIKKFIR_CLAUDE_STRICT=1 to fail instead. The build pins the bundle below to an immutable, content-addressed object.
set -euo pipefail

bundle_sha256="@BUNDLE_SHA256@"
base_url="${ARIKKFIR_CLAUDE_BASE_URL:-https://storage.googleapis.com/arikkfir-claude}"
config_dir="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

fail() {
  echo "arikkfir-claude: $*" >&2
  if [[ "${ARIKKFIR_CLAUDE_STRICT:-0}" == "1" ]]; then exit 1; fi
  echo "arikkfir-claude: continuing without the bundle" >&2
  exit 0
}

curl -fsSL --retry 3 --retry-connrefused -o "${work}/bundle.tar.gz" "${base_url}/bundles/${bundle_sha256}.tar.gz" \
  || fail "could not download bundle ${bundle_sha256}"
echo "${bundle_sha256}  ${work}/bundle.tar.gz" | sha256sum -c > /dev/null 2>&1 || fail "bundle checksum mismatch"
tar -xzf "${work}/bundle.tar.gz" -C "${work}" || fail "could not extract the bundle"
src="${work}/claude"

mkdir -p "${config_dir}/hooks"

# bundle.py runs this script in live sessions, so installs may overlap: they take turns, holding the lock until exit.
exec 9> "${config_dir}/hooks/.arikkfir.lock"
flock -w 60 9 || fail "could not lock ${config_dir}/hooks/.arikkfir.lock"

install -m 0644 "${src}/CLAUDE.md" "${config_dir}/CLAUDE.md"

# The bundle owns hooks/arikkfir entirely, so hooks removed from the bundle disappear here too. The new hooks are staged
# and swapped in, so a hook that fires meanwhile finds the old set or the new one.
hooks="${config_dir}/hooks/arikkfir"
rm -rf "${hooks}.new" "${hooks}.old"
mkdir -p "${hooks}.new"
install -m 0755 "${src}"/hooks/* "${hooks}.new/"
if [[ -e "${hooks}" ]]; then mv "${hooks}" "${hooks}.old"; fi
mv "${hooks}.new" "${hooks}"
rm -rf "${hooks}.old"

# Merge into existing settings (bundle values win), atomically.
python3 - "${config_dir}/settings.json" "${src}/settings.json" <<'PYTHON' || fail "could not merge settings.json"
import json, os, sys

target, bundle_path = sys.argv[1], sys.argv[2]

def merge(base, overlay):
    for key, value in overlay.items():
        base[key] = merge(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return base

current = {}
if os.path.exists(target):
    with open(target) as f:
        current = json.load(f)
with open(bundle_path) as f:
    merged = merge(current, json.load(f))
with open(target + ".tmp", "w") as f:
    json.dump(merged, f, indent=2)
    f.write("\n")
os.replace(target + ".tmp", target)
PYTHON

# MCP servers live in Claude Code's global config, beside the config directory unless CLAUDE_CONFIG_DIR moves it. Claude
# Code rewrites that file itself, so it is written only when a bundle server is missing or differs, keeping its mode.
global_config="${CLAUDE_CONFIG_DIR:-${HOME}}/.claude.json"
python3 - "${global_config}" "${src}/mcp.json" <<'PYTHON' || fail "could not merge MCP servers into ${global_config}"
import json, os, sys

target, bundle_path = sys.argv[1], sys.argv[2]

config = {}
if os.path.exists(target):
    with open(target) as f:
        config = json.load(f)
with open(bundle_path) as f:
    servers = json.load(f)["mcpServers"]
current = config.get("mcpServers") or {}
if all(current.get(name) == server for name, server in servers.items()):
    sys.exit(0)
config["mcpServers"] = {**current, **servers}
mode = os.stat(target).st_mode & 0o777 if os.path.exists(target) else 0o600
fd = os.open(target + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
with os.fdopen(fd, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
os.chmod(target + ".tmp", mode)
os.replace(target + ".tmp", target)
PYTHON

# Recorded last, so an install that failed partway is retried by bundle.py.
echo "${bundle_sha256}" > "${hooks}/.bundle"
echo "arikkfir-claude: installed bundle ${bundle_sha256:0:12} into ${config_dir}"
