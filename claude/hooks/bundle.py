#!/usr/bin/env python3
"""SessionStart hook (async): keeps the session's bundle current by installing the published one when it changed.

A cloud environment runs its setup script once and starts later sessions from a snapshot of the result for about seven
days, and an idle session resumes without running it again. So a session can start, or resume, with a bundle that has
since been replaced. This hook reads the bundle the published setup.sh is pinned to and, when it isn't the one setup.sh
last installed here (hooks/arikkfir/.bundle), runs that setup.sh. New hooks and settings apply at once; Claude Code
reads CLAUDE.md again at the next compaction or resume.

It runs in the background, so it never delays the session, and Claude doesn't see its output: it appends to LOG. Any
failure leaves the installed bundle as it is. Cloud sessions only (CLAUDE_CODE_REMOTE=true).
"""

import os
import re
import subprocess
import tempfile
import time
import urllib.request

BASE_URL = os.environ.get("ARIKKFIR_CLAUDE_BASE_URL") or "https://storage.googleapis.com/arikkfir-claude"
CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
LOG = os.path.join(tempfile.gettempdir(), "arikkfir-claude.log")
PINNED = re.compile(r'^bundle_sha256="([0-9a-f]{64})"$', re.MULTILINE)


def published():
    """Returns the published setup.sh and the bundle it is pinned to (None when it isn't pinned)."""
    with urllib.request.urlopen(f"{BASE_URL}/setup.sh", timeout=10) as response:
        script = response.read().decode()
    match = PINNED.search(script)
    return script, match.group(1) if match else None


def installed():
    try:
        with open(os.path.join(CONFIG_DIR, "hooks", "arikkfir", ".bundle")) as f:
            return f.read().strip()
    except OSError:
        return None


def log(message):
    try:
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
    except OSError:
        pass


def main():
    if os.environ.get("CLAUDE_CODE_REMOTE") != "true":
        return
    try:
        script, bundle = published()
        current = installed()
        if bundle is None or bundle == current:
            return
        log(f"Replacing bundle {(current or 'unknown')[:12]} with {bundle[:12]}")
        # The session's own environment: setup.sh finds the config directory and Claude Code's global config where
        # Claude Code does, both moved by CLAUDE_CONFIG_DIR only when the session sets it.
        with open(LOG, "a") as output:
            subprocess.run(
                ["bash", "-c", script], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "ARIKKFIR_CLAUDE_BASE_URL": BASE_URL}, timeout=60, check=False,
            )
    except Exception as error:  # never break the session because of this hook
        log(f"Could not refresh the bundle: {error!r}")


if __name__ == "__main__":
    main()
