#!/usr/bin/env python3
"""PreToolUse hook for the remote session's add_repo and register_repo_root: allows them without a prompt when the
repository belongs to arikkfir-org. Any other owner goes through the normal permission flow.

It only spares the prompt: the backend still decides whether the session may reach the repository.
"""

import json
import sys

ORGANIZATION = "arikkfir-org"
TOOLS = ("__add_repo", "__register_repo_root")


def main():
    try:
        payload = json.load(sys.stdin)
        if not str(payload.get("tool_name") or "").endswith(TOOLS):
            return
        owner = str((payload.get("tool_input") or {}).get("owner") or "")
    except Exception:  # never break the session because of this hook
        return
    if owner.lower() == ORGANIZATION:  # GitHub owner names are case-insensitive
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": f"The repository belongs to {ORGANIZATION}.",
            }
        }, sys.stdout)


if __name__ == "__main__":
    main()
