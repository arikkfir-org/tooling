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
install -m 0644 "${src}/CLAUDE.md" "${config_dir}/CLAUDE.md"

# The bundle owns hooks/arikkfir entirely, so hooks removed from the bundle disappear here too.
rm -rf "${config_dir}/hooks/arikkfir"
mkdir -p "${config_dir}/hooks/arikkfir"
install -m 0755 "${src}"/hooks/* "${config_dir}/hooks/arikkfir/"

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

echo "arikkfir-claude: installed bundle ${bundle_sha256:0:12} into ${config_dir}"
