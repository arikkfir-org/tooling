#!/usr/bin/env python3
"""PreToolUse hook for Bash: denies a `git commit` in an arikkfir-org repository whose message breaks the commit rules
of the house (CONTRIBUTING.md in arikkfir-org/docs, "Commit messages"):

  * the subject is `<type>(<scope>)!: <summary>`, with a type from TYPES
  * the summary is at most 72 characters, has no trailing period and no issue key, and doesn't start with a
    capitalized verb ("Add", "Fix", …)
  * `!` comes with a `BREAKING CHANGE:` footer, and the footer with `!`
  * a blank line separates the subject from the body
  * no body or footer line is longer than 72 columns, except one holding a URL or no spaces at all: those can't be
    wrapped

Only messages given on the command line are checked: -m/--message, also as a heredoc, and -F/--file. A commit that
reuses a message (--amend without -m, -C, --fixup, …), git's own Merge/Revert/fixup! subjects, a message with shell
expansions, any repository outside arikkfir-org, and anything this hook cannot parse continue through the normal
permission flow.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from guard import GIT_OPTIONS_WITH_VALUE, SHELLS, simple_commands, strip_wrappers
except Exception:  # without guard.py's shell parsing there is nothing to check
    sys.exit(0)

TYPES = ("feat", "fix", "docs", "refactor", "perf", "test", "build", "ci", "chore", "revert")
LIMIT = 72
SUBJECT = re.compile(r"(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\s][^()]*)\))?(?P<bang>!)?: (?P<summary>\S.*)")
GIT_GENERATED = re.compile(r'(Merge |Revert "|Reapply "|fixup! |squash! |amend! )')
ISSUE_KEY = re.compile(r"\bENG-\d+\b")
BREAKING_FOOTER = re.compile(r"BREAKING[ -]CHANGE: ")
# Proper nouns may start a summary ("Tekton's default ServiceAccount …"); a capitalized verb may not.
CAPITALIZED_VERBS = {
    "Add", "Added", "Adds", "Allow", "Bump", "Change", "Changed", "Clean", "Create", "Delete", "Disable", "Document",
    "Drop", "Enable", "Ensure", "Fix", "Fixed", "Fixes", "Handle", "Implement", "Improve", "Introduce", "Make", "Move",
    "Refactor", "Remove", "Rename", "Replace", "Restore", "Run", "Set", "Simplify", "Support", "Switch", "Update",
    "Updated", "Updates", "Use",
}
ORGANIZATION_REMOTE = re.compile(r"(?:^|[/@])github\.com[/:]arikkfir-org/[^/\s]+?(?:\.git)?/?$")
# Options that make git take the message from somewhere else than the command line.
REUSED = ("-C", "-c", "--reuse-message", "--reedit-message", "--fixup", "--squash")
# Claude's usual form: -m "$(cat <<'EOF' … EOF )". A quoted delimiter means the body is not expanded.
SUBSTITUTED_HEREDOC = re.compile(
    r"\$\(\s*cat\s*<<-?[ \t]*(?P<quote>['\"]?)(?P<tag>\w+)(?P=quote)[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=tag)[ \t]*\n\s*\)",
    re.S,
)
STDIN_HEREDOC = re.compile(r"<<-?[ \t]*(?P<quote>['\"]?)(?P<tag>\w+)(?P=quote)")


def extract_heredocs(command):
    """Replaces every heredoc with a placeholder word. Returns the command and {placeholder: (body, expanded)}."""
    bodies = {}

    def substitute(match):
        placeholder = f"@@HEREDOC{len(bodies)}@@"
        bodies[placeholder] = (match["body"], not match["quote"])
        return placeholder

    command = SUBSTITUTED_HEREDOC.sub(substitute, command)
    position = 0
    while True:
        match = STDIN_HEREDOC.search(command, position)
        if not match:
            return command, bodies
        line_end = command.find("\n", match.end())
        if line_end < 0:
            return command, bodies
        terminator = re.compile(rf"^[ \t]*{re.escape(match['tag'])}[ \t]*$", re.M)
        end = terminator.search(command, line_end + 1)
        if not end:
            return command, bodies
        placeholder = f"@@STDIN{len(bodies)}@@"
        bodies[placeholder] = (command[line_end + 1:end.start()].rstrip("\n"), not match["quote"])
        command = command[:match.start()] + placeholder + command[match.end():line_end] + command[end.end():]
        position = match.start() + len(placeholder)


def resolve(text, bodies):
    """The value of a -m argument with its heredoc placeholders filled in, or None if the shell would expand it."""
    expanded = "$" in text or "`" in text
    for placeholder, (body, body_expanded) in bodies.items():
        if placeholder in text:
            text = text.replace(placeholder, body)
            expanded = expanded or (body_expanded and ("$" in body or "`" in body))
    return None if expanded else text


def commit_message(args, directory, bodies):
    """Returns the message `git commit <args>` would record, or None when it doesn't come from the command line."""
    messages, path = [], None
    i = 0
    while i < len(args):
        word, value_next = args[i], i + 1 < len(args)
        if word == "--":
            break
        if word.split("=", 1)[0] in REUSED:
            return None
        if word in ("-m", "--message", "-F", "--file"):
            if not value_next:
                return None
            if word in ("-m", "--message"):
                messages.append(args[i + 1])
            else:
                path = args[i + 1]
            i += 2
            continue
        if word.startswith("--message="):
            messages.append(word.split("=", 1)[1])
        elif word.startswith("--file="):
            path = word.split("=", 1)[1]
        elif re.fullmatch(r"-[A-Za-z].*", word):
            for k, letter in enumerate(word[1:]):
                attached = word[k + 2:]
                if letter in "cC":
                    return None
                if letter in "mFt":
                    value = attached or (args[i + 1] if value_next else None)
                    if value is None:
                        return None
                    if letter == "m":
                        messages.append(value)
                    elif letter == "F":
                        path = value
                    if not attached:
                        i += 1
                    break
                if letter in "Su":  # their value, if any, is attached
                    break
        i += 1

    if messages and path is not None:
        return None  # git refuses -m together with -F
    if path is not None:
        if path == "-":
            stdin = [body for key, body in bodies.items() if key.startswith("@@STDIN") and key in args]
            if len(stdin) != 1:
                return None
            body, expanded = stdin[0]
            return None if expanded and ("$" in body or "`" in body) else body
        try:
            with open(os.path.join(directory, path), encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None
    if not messages:
        return None
    resolved = [resolve(message, bodies) for message in messages]
    return None if None in resolved else "\n\n".join(resolved)


def cleanup(message):
    """git's "whitespace" cleanup: trailing spaces, leading and trailing blank lines, and runs of blank lines go."""
    lines, blank = [], False
    for line in (line.rstrip() for line in message.strip("\n").split("\n")):
        if line or not blank:
            lines.append(line)
        blank = not line
    return "\n".join(lines).strip("\n")


def problems(message):
    lines = cleanup(message).split("\n")
    subject = lines[0]
    if not subject or GIT_GENERATED.match(subject):
        return []
    found = []
    match = SUBJECT.fullmatch(subject)
    if not match:
        found.append(f"the subject isn't `<type>(<scope>): <summary>`: {subject}")
    else:
        if match["type"] not in TYPES:
            found.append(f"`{match['type']}` isn't a commit type; use one of: {', '.join(TYPES)}")
        summary = match["summary"]
        if len(summary) > LIMIT:
            found.append(f"the summary is {len(summary)} characters, over {LIMIT}")
        if summary.endswith("."):
            found.append("the summary ends with a period")
        if ISSUE_KEY.search(summary):
            found.append("the summary names an issue key; the pull request description carries it")
        first = summary.split()[0]
        if first in CAPITALIZED_VERBS:
            found.append(f"the summary starts with `{first}`; write it in lower case (`{first.lower()}`)")
        footer = any(BREAKING_FOOTER.match(line) for line in lines[1:])
        if match["bang"] and not footer:
            found.append("`!` marks a breaking change, but no `BREAKING CHANGE: …` footer explains the migration")
        if footer and not match["bang"]:
            found.append("a `BREAKING CHANGE:` footer needs `!` after the type or scope")
    if len(lines) > 1 and lines[1]:
        found.append("there is no blank line between the subject and the body")
    for number, line in enumerate(lines[1:], 2):
        if len(line) > LIMIT and "://" not in line and " " in line.strip():
            found.append(f"line {number} is {len(line)} columns, over {LIMIT}: {line[:40]}…")
    return found


def git_directory(args, directory):
    """Applies git's -C options. Returns the directory and the arguments after the global options."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            directory = os.path.join(directory, os.path.expanduser(args[i + 1]))
        if args[i] in GIT_OPTIONS_WITH_VALUE:
            i += 1
        i += 1
    return directory, args[i:]


def in_organization(directory):
    try:
        result = subprocess.run(
            ["git", "-C", directory, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and ORGANIZATION_REMOTE.search(result.stdout.strip()) is not None


def check(command, cwd):
    if "commit" not in command:
        return None
    command, bodies = extract_heredocs(command)
    directory = cwd
    for words in simple_commands(command):
        words = strip_wrappers(words)
        if not words:
            continue
        name = os.path.basename(words[0])
        if name == "cd":
            target = os.path.expanduser(words[1]) if len(words) > 1 else os.environ.get("HOME", "/")
            directory = os.path.join(directory, target)
        elif name in SHELLS and "-c" in words[1:]:
            index = words.index("-c")
            if index + 1 < len(words):
                reason = check(words[index + 1], directory)
                if reason:
                    return reason
        elif name == "git":
            repository, rest = git_directory(words[1:], directory)
            if not rest or rest[0] != "commit":
                continue
            message = commit_message(rest[1:], repository, bodies)
            if message is None or not in_organization(repository):
                continue
            found = problems(message)
            if found:
                return (
                    "This commit message breaks the commit rules (CONTRIBUTING.md in arikkfir-org/docs, "
                    "\"Commit messages\"):\n" + "\n".join(f"- {problem}" for problem in found)
                    + "\nRewrite the message and commit again."
                )
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return
        command = (payload.get("tool_input") or {}).get("command") or ""
        reason = check(command, payload.get("cwd") or os.getcwd())
    except Exception:  # never break the session because of this hook
        return
    if reason:
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, sys.stdout)


if __name__ == "__main__":
    main()
