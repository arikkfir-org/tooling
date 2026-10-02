#!/usr/bin/env python3
"""SessionStart hook: starts the Docker daemon in cloud sessions, pulling from Docker Hub through mirror.gcr.io.

Cloud sessions ship docker and dockerd but don't run the daemon, so anything that needs it (a testcontainers test, a
docker build) fails until someone starts it. Their egress IP is shared, so anonymous Docker Hub pulls hit its rate
limit; Google's mirror of Docker Hub has no such limit, and Docker falls back to Docker Hub for what the mirror lacks.

Before starting the daemon it adds the mirror to /etc/docker/daemon.json, keeping whatever else is there, and leaves a
file it can't parse alone. It doesn't wait for the daemon to come up, so the session starts as fast as before. A
running daemon is left as it is. Cloud sessions only (CLAUDE_CODE_REMOTE=true).
"""

import json
import os
import shutil
import socket
import subprocess
import sys

DOCKERD = "dockerd"
DAEMON_JSON = "/etc/docker/daemon.json"
SOCKET = "/var/run/docker.sock"
LOG = "/var/log/dockerd.log"
MIRROR = "https://mirror.gcr.io"


def running(path):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(path)
        return True
    except OSError:
        return False


def add_mirror(path):
    """Adds MIRROR to the daemon's registry mirrors. Returns False when the file can't be read as an object."""
    settings = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                settings = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(settings, dict):
            return False
    mirrors = settings.setdefault("registry-mirrors", [])
    if not isinstance(mirrors, list):
        return False
    if MIRROR in (str(mirror).rstrip("/") for mirror in mirrors):
        return True
    mirrors.append(MIRROR)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)
    return True


def start(log_path):
    try:
        log = open(log_path, "ab")
    except OSError:
        log = subprocess.DEVNULL
    subprocess.Popen(
        [DOCKERD], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def main():
    try:
        if os.environ.get("CLAUDE_CODE_REMOTE") != "true" or shutil.which(DOCKERD) is None or running(SOCKET):
            return
        mirrored = add_mirror(DAEMON_JSON)
        start(LOG)
    except Exception:  # never break the session because of this hook
        return
    through = f", pulling from Docker Hub through {MIRROR}" if mirrored else ""
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                f"Started the Docker daemon in the background{through} (log: {LOG}). It takes a few seconds before "
                "docker commands work."
            ),
        }
    }, sys.stdout)


if __name__ == "__main__":
    main()
