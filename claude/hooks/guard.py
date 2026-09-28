#!/usr/bin/env python3
"""PreToolUse hook for Bash: denies commands that destroy work in ways that are hard to undo.

Denied:
  * force pushes (--force, -f, --force-with-lease, --mirror, "+" refspecs) and remote branch deletions that target
    main or master, named explicitly or implied by pushing a checked-out main/master branch
  * recursive deletion of the filesystem root or the home directory

Everything else, including anything this hook cannot parse, continues through the normal permission flow.
"""

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED_BRANCHES = {"main", "master"}
SEPARATOR_CHARS = set(";&|()\n")
WRAPPERS = {"sudo", "env", "nohup", "time", "command", "exec", "nice"}
SHELLS = {"sh", "bash", "zsh", "dash", "ash"}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
PUSH_OPTIONS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def simple_commands(command):
    """Splits a shell command line into simple commands, each a list of words."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    current = []
    for token in lexer:
        if token and set(token) <= SEPARATOR_CHARS:
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def strip_wrappers(words):
    """Removes leading variable assignments and wrappers such as sudo or env."""
    while words:
        head = words[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", head):
            words = words[1:]
        elif head in WRAPPERS:
            words = words[1:]
            while words and words[0].startswith("-"):
                words = words[1:]
        else:
            break
    return words


def short_flags(word):
    """Returns the letters of a short-option cluster such as -fu, or an empty set."""
    if re.fullmatch(r"-[A-Za-z]+", word):
        return set(word[1:])
    return set()


def current_branch(directory):
    try:
        result = subprocess.run(
            ["git", "-C", directory, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def check_git_push(args, cwd):
    directory = cwd
    i = 0
    while i < len(args) and args[i].startswith("-"):
        option = args[i].split("=", 1)[0]
        if option == "-C" and i + 1 < len(args):
            directory = os.path.join(directory, args[i + 1])
        if args[i] in GIT_OPTIONS_WITH_VALUE:
            i += 1
        i += 1
    if i >= len(args) or args[i] != "push":
        return None

    force = delete = dry_run = every_branch = False
    positional = []
    rest = args[i + 1:]
    j = 0
    while j < len(rest):
        word = rest[j]
        flags = short_flags(word)
        if word == "--":
            positional.extend(rest[j + 1:])
            break
        if word in ("--force", "--mirror") or word.startswith("--force-with-lease") or "f" in flags:
            force = True
        if word == "--delete" or "d" in flags:
            delete = True
        if word == "--dry-run" or "n" in flags:
            dry_run = True
        if word in ("--mirror", "--all", "--branches"):
            every_branch = True
        if word in PUSH_OPTIONS_WITH_VALUE or "o" in flags:
            j += 1
        elif not word.startswith("-"):
            positional.append(word)
        j += 1

    if dry_run:
        return None
    refspecs = positional[1:]
    targets = set()
    for refspec in refspecs:
        if refspec.startswith("+"):
            force = True
            refspec = refspec[1:]
        source, _, destination = refspec.partition(":")
        if refspec.startswith(":"):
            delete = True
        target = destination or source
        if target == "HEAD":
            target = current_branch(directory) or ""
        targets.add(re.sub(r"^refs/heads/", "", target))
    if every_branch:
        targets |= PROTECTED_BRANCHES
    if not refspecs and not every_branch:
        branch = current_branch(directory)
        if branch:
            targets.add(branch)

    hit = sorted(targets & PROTECTED_BRANCHES)
    if hit and (force or delete):
        action = "Deleting" if delete and not force else "Force-pushing"
        return f"{action} {', '.join(hit)} rewrites shared history. Push a branch and open a pull request instead."
    return None


def dangerous_path(path, home):
    path = re.sub(r"^(\$HOME|\$\{HOME\})(?=/|$)", home, path)
    if path == "~" or path.startswith("~/"):
        path = home + path[1:]
    path = re.sub(r"/+", "/", path)
    if path.endswith("/*"):
        path = path[:-2] or "/"
    normalized = os.path.normpath(path) if path else path
    return normalized in {"/", os.path.normpath(home)}


def check_rm(args):
    recursive = no_preserve_root = False
    targets = []
    options_done = False
    for word in args:
        if not options_done and word == "--":
            options_done = True
        elif not options_done and word.startswith("-"):
            if word == "--recursive" or short_flags(word) & {"r", "R"}:
                recursive = True
            if word == "--no-preserve-root":
                no_preserve_root = True
        else:
            targets.append(word)
    if not recursive:
        return None
    home = os.environ.get("HOME", "/root")
    if no_preserve_root or any(dangerous_path(target, home) for target in targets):
        return "Recursively deleting the filesystem root or the home directory is not allowed."
    return None


def check(command, cwd):
    for words in simple_commands(command):
        words = strip_wrappers(words)
        if not words:
            continue
        name = os.path.basename(words[0])
        if name in SHELLS and "-c" in words[1:]:
            index = words.index("-c")
            if index + 1 < len(words):
                reason = check(words[index + 1], cwd)
                if reason:
                    return reason
        elif name == "git":
            reason = check_git_push(words[1:], cwd)
            if reason:
                return reason
        elif name == "rm":
            reason = check_rm(words[1:])
            if reason:
                return reason
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
