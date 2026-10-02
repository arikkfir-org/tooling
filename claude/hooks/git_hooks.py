#!/usr/bin/env python3
"""SessionStart hook, and PostToolUse hook for register_repo_root: points every arikkfir-org repository that commits
its git hooks in .githooks/ at them (core.hooksPath), so they run on the commits and pushes of the session.

Git doesn't clone hooks: a fresh clone has none until core.hooksPath is set, and every cloud session works on fresh
clones. It runs at session start for the repositories the session begins with, and after register_repo_root, the last
step of attaching one mid-session. Either way it looks at the working directory and its subdirectories, plus the
directory register_repo_root names, and leaves a repository alone when core.hooksPath is already set.

Only a repository whose origin is github.com/arikkfir-org/… is armed. Any other keeps git's default, in which its
committed hooks never run: they'd be someone else's code running on the session's next commit.

Cloud sessions only (CLAUDE_CODE_REMOTE=true): on a workstation, the repositories' configuration is the developer's.
"""

import json
import os
import re
import subprocess
import sys

ORGANIZATION_REMOTE = re.compile(r"(?:^|[/@])github\.com[/:]arikkfir-org/[^/\s]+?(?:\.git)?/?$")


def git(directory, *args):
    return subprocess.run(["git", "-C", directory, *args], capture_output=True, text=True, timeout=5, check=False)


def candidates(payload):
    cwd = payload.get("cwd") or os.getcwd()
    found = [cwd]
    try:
        found += sorted(entry.path for entry in os.scandir(cwd) if entry.is_dir() and not entry.name.startswith("."))
    except OSError:
        pass
    attached = (payload.get("tool_input") or {}).get("directory")
    if attached:
        found.append(attached)
    return list(dict.fromkeys(os.path.normpath(path) for path in found))


def arm(directory):
    """Sets core.hooksPath to .githooks in an arikkfir-org repository that has that directory and no hooks path yet."""
    if not os.path.exists(os.path.join(directory, ".git")) or not os.path.isdir(os.path.join(directory, ".githooks")):
        return False
    origin = git(directory, "remote", "get-url", "origin")
    if origin.returncode != 0 or not ORGANIZATION_REMOTE.search(origin.stdout.strip()):
        return False
    if git(directory, "config", "--local", "--get", "core.hooksPath").stdout.strip():
        return False
    return git(directory, "config", "--local", "core.hooksPath", ".githooks").returncode == 0


def main():
    try:
        if os.environ.get("CLAUDE_CODE_REMOTE") != "true":
            return
        payload = json.load(sys.stdin)
        armed = [os.path.basename(directory) for directory in candidates(payload) if arm(directory)]
    except Exception:  # never break the session because of this hook
        return
    if armed:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": payload.get("hook_event_name") or "SessionStart",
                "additionalContext": (
                    f"Pointed {', '.join(armed)} at the git hooks committed in .githooks/ (core.hooksPath), so "
                    "they run on this session's commits and pushes. Don't bypass them with --no-verify; fix what "
                    "they report."
                ),
            }
        }, sys.stdout)


if __name__ == "__main__":
    main()
