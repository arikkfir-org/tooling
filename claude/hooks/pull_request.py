#!/usr/bin/env python3
"""PostToolUse hook for create_pull_request: reminds the session, right after it opens a pull request in arikkfir-org,
of what the pull request still needs: the AI review, and a description that links its Linear issue and design.

Opening is not finishing: the author can't approve their own pull request, so the review that lets it merge is
arikkfir-reviewer's, and nobody requests it unless the session does. A rule in prose needs the session to remember it
at this moment; this hook is there at the moment. It reminds and blocks nothing.
"""

import json
import re
import sys

ORGANIZATION = "arikkfir-org"
REVIEWER = "arikkfir-reviewer"
NAME = re.compile(r"[A-Za-z0-9._-]+")


def main():
    try:
        payload = json.load(sys.stdin)
        if not str(payload.get("tool_name") or "").endswith("__create_pull_request"):
            return
        if payload.get("tool_result_is_error"):
            return
        arguments = payload.get("tool_input") or {}
        owner, repo = str(arguments.get("owner") or ""), str(arguments.get("repo") or "")
        if owner.lower() != ORGANIZATION or not NAME.fullmatch(repo):
            return
        result = json.dumps(payload.get("tool_result", payload.get("tool_response")))
        number = re.search(rf"github\.com/{re.escape(owner)}/{re.escape(repo)}/pull/(\d+)", result, re.I)
        requested = any(str(login).lower() == REVIEWER for login in arguments.get("reviewers") or [])
    except Exception:  # never break the session because of this hook
        return

    steps = []
    if not requested:
        steps.append(
            f'Request the AI review: mcp__github__update_pull_request with reviewers: ["{REVIEWER}"]. Its approval '
            "is the one the pull request can merge with, since the author can't approve their own. After you push "
            "fixes for its findings and reply on its threads, request it again."
        )
    steps.append(
        "Check the description against CONTRIBUTING.md in arikkfir-org/docs (\"Pull requests\"): its Linear issue "
        "as `Closes ENG-<n>` or `Part of ENG-<n>`, and the link to the design doc for a meaningful change."
    )
    subject = f"#{number.group(1)} in {owner}/{repo}" if number else f"a pull request in {owner}/{repo}"
    message = f"You opened {subject}. It isn't finished until it is with a reviewer:\n" + "\n".join(
        f"{index}. {step}" for index, step in enumerate(steps, 1)
    )
    json.dump({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}}, sys.stdout)


if __name__ == "__main__":
    main()
