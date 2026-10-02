#!/usr/bin/env python3
"""PreToolUse hook for Bash: denies commands that destroy work in ways that are hard to undo, and asks before others
that lose work.

Denied:
  * force pushes (--force, -f, --force-with-lease, --mirror, "+" refspecs) and remote branch deletions that target
    main or master, named explicitly or implied by pushing a checked-out main/master branch
  * recursive deletion of the filesystem root or the home directory

Asked, even when a permission rule allows the command:
  * a push that deletes any other remote branch (--delete, -d, a ":branch" refspec) or prunes them (--prune)
  * a git switch that discards uncommitted changes or resets a branch (-f, --force, --discard-changes, -C,
    --force-create), however its options are clustered, attached or abbreviated: settings.json's ask rules are globs,
    which can't match `-qf` without matching `git switch -c` too

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
# Wrappers, each with its options that take the next word as their value. Claude Code strips these (and bare xargs)
# before matching permission rules, so the hook must see through them, values included.
WRAPPERS = {
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-D", "--chdir", "-h", "--host", "-p", "--prompt",
             "-r", "--role", "-t", "--type", "-T", "--command-timeout", "-U", "--other-user"},
    "env": {"-u", "--unset", "-C", "--chdir"},
    "nohup": set(),
    "time": {"-o", "--output", "-f", "--format"},
    "command": set(),
    "exec": {"-a"},
    "nice": {"-n", "--adjustment"},
    "builtin": set(),
    "noglob": set(),
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
}
# git push's long options: git accepts any unambiguous prefix of one.
PUSH_LONG_OPTIONS = (
    "all", "atomic", "branches", "delete", "dry-run", "exec", "follow-tags", "force", "force-if-includes",
    "force-with-lease", "ipv4", "ipv6", "mirror", "no-atomic", "no-force-if-includes", "no-force-with-lease",
    "no-progress", "no-recurse-submodules", "no-signed", "no-thin", "no-verify", "porcelain", "progress", "prune",
    "push-option", "quiet", "receive-pack", "recurse-submodules", "repo", "set-upstream", "signed", "tags", "thin",
    "verbose", "verify",
)
SHELLS = {"sh", "bash", "zsh", "dash", "ash"}
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
PUSH_OPTIONS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
# git switch's long options that lose local work; git accepts any unambiguous prefix, and --force is one of the first.
SWITCH_LOSING_WORK = ("force-create", "discard-changes")


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
            takes_value = WRAPPERS[head]
            words = words[1:]
            while words and words[0].startswith("-"):
                if words[0] == "--":
                    words = words[1:]
                    break
                words = words[2:] if wrapper_option_takes_next(words[0], takes_value) else words[1:]
            if head == "timeout":
                words = words[1:]  # the duration
        elif head == "xargs" and len(words) > 1 and not words[1].startswith("-"):
            words = words[1:]  # only bare xargs, as Claude Code strips it
        else:
            break
    return words


def wrapper_option_takes_next(word, takes_value):
    """Tells whether a wrapper's option takes the next word as its value, as getopt_long reads it: a long option by
    any prefix, a short one at the end of a cluster (earlier in one, the rest of the cluster is its value)."""
    if word.startswith("--"):
        return "=" not in word and any(option.startswith(word) for option in takes_value if option.startswith("--"))
    for index, letter in enumerate(word[1:], start=1):
        if "-" + letter in takes_value:
            return index == len(word) - 1
    return False


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


def git_subcommand(args, cwd):
    """Skips git's global options. Returns the directory -C points at, the subcommand and its arguments."""
    directory = cwd
    i = 0
    while i < len(args) and args[i].startswith("-"):
        option = args[i].split("=", 1)[0]
        if option == "-C" and i + 1 < len(args):
            directory = os.path.join(directory, args[i + 1])
        if args[i] in GIT_OPTIONS_WITH_VALUE:
            i += 1
        i += 1
    if i >= len(args):
        return directory, None, []
    return directory, args[i], args[i + 1:]


def check_git(args, cwd):
    directory, subcommand, rest = git_subcommand(args, cwd)
    if subcommand == "push":
        return check_git_push(rest, directory)
    if subcommand == "switch" and switch_loses_work(rest):
        return "ask", "This git switch discards uncommitted changes or resets a branch."
    return None


def switch_loses_work(args):
    for word in args:
        if word == "--":
            break
        if word.startswith("--"):
            name = word[2:].split("=", 1)[0]
            if len(name) >= 2 and any(option.startswith(name) for option in SWITCH_LOSING_WORK):
                return True
        elif word.startswith("-"):
            for letter in word[1:]:
                if letter in "fC":
                    return True
                if letter == "c":  # the rest of the cluster, or the next word, names the branch it creates
                    break
    return False


def push_long_options(word):
    """The git push long options a word can stand for: its exact name, or every option it abbreviates."""
    if not word.startswith("--") or word == "--":
        return set()
    name = word[2:].split("=", 1)[0]
    if name in PUSH_LONG_OPTIONS:
        return {name}
    return {option for option in PUSH_LONG_OPTIONS if name and option.startswith(name)}


def check_git_push(rest, directory):
    force = delete = dry_run = every_branch = prune = False
    positional = []
    j = 0
    while j < len(rest):
        word = rest[j]
        flags = short_flags(word)
        if word == "--":
            positional.extend(rest[j + 1:])
            break
        # An ambiguous abbreviation counts as every option it could be when that is the riskier reading; git
        # refuses it anyway.
        options = push_long_options(word)
        if options & {"force", "force-with-lease", "mirror"} or "f" in flags:
            force = True
        if "delete" in options or "d" in flags:
            delete = True
        if options == {"dry-run"} or "n" in flags:
            dry_run = True
        if options & {"mirror", "all", "branches"}:
            every_branch = True
        if "prune" in options:
            prune = True
        takes_value = len(options) == 1 and options <= {"repo", "receive-pack", "exec", "push-option"}
        if (takes_value and "=" not in word) or word in PUSH_OPTIONS_WITH_VALUE or "o" in flags:
            j += 1
        elif not word.startswith("-"):
            positional.append(word)
        j += 1

    if dry_run:
        return None
    refspecs = positional[1:]
    every_target_deleted = delete
    targets, deleted = set(), set()
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
        target = re.sub(r"^refs/heads/", "", target)
        targets.add(target)
        if refspec.startswith(":"):
            deleted.add(target)
    if every_branch:
        targets |= PROTECTED_BRANCHES
    if not refspecs and not every_branch:
        branch = current_branch(directory)
        if branch:
            targets.add(branch)

    hit = sorted(targets & PROTECTED_BRANCHES)
    if hit and (force or delete or prune):
        action = "Deleting" if (delete or prune) and not force else "Force-pushing"
        return "deny", (
            f"{action} {', '.join(hit)} rewrites shared history. Push a branch and open a pull request instead."
        )
    if delete:
        names = sorted(targets if every_target_deleted else deleted)
        return "ask", f"This push deletes {', '.join(names) or 'a branch'} on the remote."
    if prune:
        return "ask", "This push deletes every remote branch its refspecs match that has no local counterpart."
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
    """Returns ("deny", reason) for the first command to deny, else ("ask", reason) for the first to ask about."""
    asked = None
    for words in simple_commands(command):
        words = strip_wrappers(words)
        if not words:
            continue
        name = os.path.basename(words[0])
        result = None
        if name in SHELLS and "-c" in words[1:]:
            index = words.index("-c")
            if index + 1 < len(words):
                result = check(words[index + 1], cwd)
        elif name == "git":
            result = check_git(words[1:], cwd)
        elif name == "rm":
            reason = check_rm(words[1:])
            result = ("deny", reason) if reason else None
        if result and result[0] == "deny":
            return result
        asked = asked or result
    return asked


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return
        command = (payload.get("tool_input") or {}).get("command") or ""
        result = check(command, payload.get("cwd") or os.getcwd())
    except Exception:  # never break the session because of this hook
        return
    if result:
        decision, reason = result
        json.dump({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }, sys.stdout)


if __name__ == "__main__":
    main()
