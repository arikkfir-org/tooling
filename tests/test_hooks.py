import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "claude", "hooks", "guard.py")
COMMIT_MESSAGE = os.path.join(ROOT, "claude", "hooks", "commit_message.py")
FORMAT = os.path.join(ROOT, "claude", "hooks", "format.py")


def run_hook(script, payload, env=None):
    result = subprocess.run(
        [sys.executable, script], input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, **(env or {})}, check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def git(directory, *args):
    subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True)


class AddRepoTest(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "claude", "hooks", "add_repo.py")

    def decide(self, tool, owner, repo="docs"):
        payload = {"tool_name": tool, "tool_input": {"owner": owner, "repo": repo}, "cwd": ROOT}
        output = run_hook(self.SCRIPT, payload)
        return output["hookSpecificOutput"]["permissionDecision"] if output else "pass"

    def test_public_repositories_are_allowed(self):
        for tool in ("mcp__claude-code-remote__add_repo", "mcp__Claude_Code_Remote__register_repo_root"):
            for owner, repo in [
                ("arikkfir-org", "docs"),
                ("Arikkfir-Org", "Docs"),
                ("arikkfir-org", ".github"),
                ("arikkfir-org", "delivery"),
                ("arikkfir-org", "infra"),
                ("arikkfir-org", "octomaton"),
                ("arikkfir-org", "tooling"),
            ]:
                with self.subTest(tool=tool, owner=owner, repo=repo):
                    self.assertEqual(self.decide(tool, owner, repo), "allow")

    def test_others_pass_through(self):
        for tool, owner, repo in [
            ("mcp__claude-code-remote__add_repo", "arikkfir-org", "fin"),
            ("mcp__Claude_Code_Remote__register_repo_root", "arikkfir-org", "FIN"),
            ("mcp__claude-code-remote__add_repo", "arikkfir-org", "a-new-repository"),
            ("mcp__claude-code-remote__add_repo", "arikkfir-org", None),
            ("mcp__claude-code-remote__add_repo", "weesp-ai", "docs"),
            ("mcp__claude-code-remote__add_repo", "arikkfir", "docs"),
            ("mcp__claude-code-remote__add_repo", "arikkfir-org-fork", "docs"),
            ("mcp__claude-code-remote__add_repo", None, "docs"),
            ("mcp__github__create_repository", "arikkfir-org", "docs"),
            ("mcp__someone-else__add_repo", "arikkfir-org", "docs"),
        ]:
            with self.subTest(tool=tool, owner=owner, repo=repo):
                self.assertEqual(self.decide(tool, owner, repo), "pass")

    def test_garbage_input_is_ignored(self):
        result = subprocess.run([sys.executable, self.SCRIPT], input="[]", capture_output=True, text=True, check=True)


class DockerdTest(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "claude", "hooks", "dockerd.py")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        spec = importlib.util.spec_from_file_location("dockerd_hook", self.SCRIPT)
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)
        # A dockerd that only records that it was started.
        self.started = os.path.join(self.tmp, "started")
        self.hook.DOCKERD = os.path.join(self.tmp, "dockerd")
        with open(self.hook.DOCKERD, "w") as f:
            f.write(f"#!/bin/sh\ntouch '{self.started}'\n")
        os.chmod(self.hook.DOCKERD, 0o755)
        self.hook.DAEMON_JSON = os.path.join(self.tmp, "etc", "docker", "daemon.json")
        self.hook.SOCKET = os.path.join(self.tmp, "docker.sock")
        self.hook.LOG = os.path.join(self.tmp, "dockerd.log")

    def run_main(self, remote="true"):
        output = io.StringIO()
        popen = subprocess.Popen

        def started_and_awaited(*args, **kwargs):
            # The daemon outlives the hook: wait for the fake one before cleanup deletes the directory it writes to.
            process = popen(*args, **kwargs)
            self.addCleanup(process.wait, 5)
            return process

        with mock.patch.dict(os.environ, {"CLAUDE_CODE_REMOTE": remote}), mock.patch("sys.stdout", output), \
                mock.patch.object(self.hook.subprocess, "Popen", started_and_awaited):
            self.hook.main()
        return output.getvalue()

    def was_started(self):
        for _ in range(50):
            if os.path.exists(self.started):
                return True
            time.sleep(0.05)
        return False

    def daemon_json(self):
        with open(self.hook.DAEMON_JSON) as f:
            return json.load(f)

    def test_starts_the_daemon_with_the_mirror(self):
        output = json.loads(self.run_main())
        self.assertIn("mirror.gcr.io", output["hookSpecificOutput"]["additionalContext"])
        self.assertTrue(self.was_started())
        self.assertEqual(self.daemon_json(), {"registry-mirrors": ["https://mirror.gcr.io"]})

    def test_keeps_the_existing_daemon_settings(self):
        os.makedirs(os.path.dirname(self.hook.DAEMON_JSON))
        with open(self.hook.DAEMON_JSON, "w") as f:
            json.dump({"debug": True, "registry-mirrors": ["https://mirror.example"]}, f)
        self.run_main()
        mirrors = ["https://mirror.example", "https://mirror.gcr.io"]
        self.assertEqual(self.daemon_json(), {"debug": True, "registry-mirrors": mirrors})
        self.run_main()
        self.assertEqual(self.daemon_json()["registry-mirrors"].count("https://mirror.gcr.io"), 1)

    def test_leaves_an_unreadable_daemon_json_alone(self):
        os.makedirs(os.path.dirname(self.hook.DAEMON_JSON))
        with open(self.hook.DAEMON_JSON, "w") as f:
            f.write("{not json")
        output = json.loads(self.run_main())
        self.assertNotIn("mirror.gcr.io", output["hookSpecificOutput"]["additionalContext"])
        with open(self.hook.DAEMON_JSON) as f:
            self.assertEqual(f.read(), "{not json")

    def test_starts_the_daemon_when_daemon_json_cannot_be_written(self):
        with open(os.path.join(self.tmp, "etc"), "w"):
            pass  # a file where /etc/docker should be: the write fails, even for root
        output = json.loads(self.run_main())
        self.assertTrue(self.was_started())
        self.assertNotIn("mirror.gcr.io", output["hookSpecificOutput"]["additionalContext"])

    def test_leaves_a_running_daemon_alone(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(self.hook.SOCKET)
            server.listen()
            self.assertEqual(self.run_main(), "")
        self.assertFalse(os.path.exists(self.started))
        self.assertFalse(os.path.exists(self.hook.DAEMON_JSON))

    def test_does_nothing_outside_cloud_sessions(self):
        self.assertEqual(self.run_main(remote=""), "")
        self.assertFalse(os.path.exists(self.hook.DAEMON_JSON))
        result = subprocess.run(
            [sys.executable, self.SCRIPT], input="{}", capture_output=True, text=True, check=True,
            env={**os.environ, "CLAUDE_CODE_REMOTE": ""},
        )


        self.assertEqual(result.stdout, "")


class GuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home)
        for name, branch in (("on-main", "main"), ("on-feature", "feature")):
            path = os.path.join(cls.tmp, name)
            os.makedirs(path)
            git(path, "init", "--quiet", f"--initial-branch={branch}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def decide(self, command, cwd="on-feature"):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.path.join(self.tmp, cwd)}
        output = run_hook(GUARD, payload, env={"HOME": self.home})
        return output["hookSpecificOutput"]["permissionDecision"] if output else "pass"

    def test_denied(self):
        for command, cwd in [
            ("git push --force origin main", "on-feature"),
            ("git push -f origin master", "on-feature"),
            ("git push -uf origin main", "on-feature"),
            ("git push --force-with-lease=main origin main", "on-feature"),
            ("git push origin +main", "on-feature"),
            ("git push origin +HEAD:main", "on-feature"),
            ("git push origin +feature:refs/heads/main", "on-feature"),
            ("git push origin --delete main", "on-feature"),
            ("git push origin :main", "on-feature"),
            ("git push --mirror origin", "on-feature"),
            ("git push --force", "on-main"),
            ("git push -f origin HEAD", "on-main"),
            ("git -C ../on-main push --force", "on-feature"),
            ("cd /tmp && FOO=1 git push -f origin main", "on-feature"),
            ("bash -c 'git push --force origin main'", "on-feature"),
            ("rm -rf /", "on-feature"),
            ("sudo rm -rf /*", "on-feature"),
            ("rm -fr ~", "on-feature"),
            ("rm -rf ~/", "on-feature"),
            ("rm -r $HOME", "on-feature"),
            ('rm -rf "${HOME}"/*', "on-feature"),
            ("rm -r --no-preserve-root /tmp/x", "on-feature"),
            ("true; rm -Rf //", "on-feature"),
        ]:
            with self.subTest(command=command, cwd=cwd):
                self.assertEqual(self.decide(command, cwd), "deny")

    def test_passed(self):
        for command, cwd in [
            ("git push", "on-feature"),
            ("git push -u origin HEAD", "on-feature"),
            ("git push --force", "on-feature"),
            ("git push --force origin feature", "on-feature"),
            ("git push origin main", "on-feature"),
            ("git push origin main", "on-main"),
            ("git push --dry-run --force origin main", "on-feature"),
            ("git push -o ci.skip origin feature", "on-feature"),
            ("git status && git log --oneline -3", "on-main"),
            ("echo 'git push --force origin main'", "on-feature"),
            ("rm -rf build dist", "on-feature"),
            ("rm -rf ./node_modules ~/.cache/go-build", "on-feature"),
            ("rm -f /", "on-feature"),
            ("rm -rf 'unterminated", "on-feature"),
        ]:
            with self.subTest(command=command, cwd=cwd):
                self.assertEqual(self.decide(command, cwd), "pass")

    def test_other_tools_ignored(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "/x", "content": "rm -rf /"}, "cwd": self.tmp}
        self.assertIsNone(run_hook(GUARD, payload))

    def test_garbage_input_is_ignored(self):
        result = subprocess.run([sys.executable, GUARD], input="not json", capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "")


class CommitMessageTest(unittest.TestCase):
    LONG_LINE = "this body line goes on and on and on, well past the seventy-two column limit"
    LONG_SUMMARY = "add a summary that keeps on going and going, well past the seventy-two characters"
    URL = "https://claude.ai/code/session_0123456789abcdefghijklmnopqrstuvwxyz0123456789"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for name, remote in (
            ("org", "https://github.com/arikkfir-org/tooling"),
            ("org-ssh", "git@github.com:arikkfir-org/tooling.git"),
            ("other", "https://github.com/someone-else/tooling"),
            ("other-host", "https://gitlab.com/arikkfir-org/tooling"),
            ("look-alike-host", "https://notgithub.com/arikkfir-org/tooling"),
            ("local", None),
        ):
            path = os.path.join(cls.tmp, name)
            os.makedirs(path)
            git(path, "init", "--quiet")
            if remote:
                git(path, "remote", "add", "origin", remote)
        with open(os.path.join(cls.tmp, "org", "bad.txt"), "w") as f:
            f.write("feat: add a thing\nthe body starts right under the subject\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def run_commit_hook(self, command, cwd="org"):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.path.join(self.tmp, cwd)}
        return run_hook(COMMIT_MESSAGE, payload)

    def decide(self, command, cwd="org"):
        output = self.run_commit_hook(command, cwd)
        return output["hookSpecificOutput"]["permissionDecision"] if output else "pass"

    @staticmethod
    def heredoc(message):
        """The command Claude usually writes: the message as a quoted heredoc inside a command substitution."""
        return f"git commit -m \"$(cat <<'EOF'\n{message}\nEOF\n)\""

    def test_denied(self):
        for command, cwd in [
            ('git commit -m "add a thing"', "org"),
            ('git commit -m "style: tidy up"', "org"),
            ('git commit -m "Feat: add a thing"', "org"),
            ('git commit -m "feat: add a thing."', "org"),
            ('git commit -m "feat: Add a thing"', "org"),
            ('git commit -m "fix(gke): close ENG-12"', "org"),
            ('git commit -m "feat!: drop the v1 API"', "org"),
            ('git commit -m "feat: drop the v1 API" -m "BREAKING CHANGE: callers move to v2"', "org"),
            (f'git commit -m "feat: {self.LONG_SUMMARY}"', "org"),
            (f'git commit -m "feat: add a thing" -m "{self.LONG_LINE}"', "org"),
            ("git commit -am 'Fix the thing'", "org"),
            ('git commit --message="Update the docs"', "org"),
            ("git commit -F bad.txt", "org"),
            ("git commit -F - <<'EOF'\nUpdate things\nEOF", "org"),
            (self.heredoc(f"feat: add a thing\n\n{self.LONG_LINE}"), "org"),
            ("git add -A && " + self.heredoc("Add a thing") + " && git push", "org"),
            ('git -C ../org commit -m "add a thing"', "other"),
            ('cd ../org && git commit -m "add a thing"', "other"),
            ("bash -c 'git commit -m \"add a thing\"'", "org"),
            ('git commit -m "add a thing"', "org-ssh"),
        ]:
            with self.subTest(command=command, cwd=cwd):
                self.assertEqual(self.decide(command, cwd), "deny")

    def test_passed(self):
        for command, cwd in [
            ('git commit -m "feat: add a thing"', "org"),
            ('git commit -m "fix(gke): pin the node pool version" -m "The upgrade broke it."', "org"),
            ("git commit -m \"docs(hub): Tekton's default ServiceAccount has no Google Cloud role\"", "org"),
            # A long type and scope may take the subject past 72 columns; only the summary is limited.
            ('git commit -m "feat(kubernetes-platform): add resource requests and memory limits to every task"', "org"),
            ('git commit -m "feat!: drop the v1 API" -m "BREAKING CHANGE: callers move to v2"', "org"),
            (self.heredoc(f"feat: add a thing\n\nWhy it matters.\n\nSession: {self.URL}"), "org"),
            ("git commit -F - <<'EOF'\nci: run the tests\nEOF", "org"),
            ("git commit --amend --no-edit", "org"),
            ("git commit", "org"),
            ("git commit -C HEAD", "org"),
            ("git commit --fixup=HEAD", "org"),
            ("git commit -m 'Revert \"feat: add a thing\"'", "org"),
            ('git commit -m "Merge branch main"', "org"),
            ('git commit -m "fix: bump to $VERSION"', "org"),
            ("git commit -F missing.txt", "org"),
            ('git commit -m "add a thing"', "other"),
            ('git commit -m "add a thing"', "other-host"),
            ('git commit -m "add a thing"', "look-alike-host"),
            ('git commit -m "add a thing"', "local"),
            ("echo 'git commit -m \"add a thing\"'", "org"),
            ('git log --grep "commit"', "org"),
            ("git commit -m 'unterminated", "org"),
        ]:
            with self.subTest(command=command, cwd=cwd):
                self.assertEqual(self.decide(command, cwd), "pass")

    def test_reason_lists_every_problem(self):
        output = self.run_commit_hook('git commit -m "feat: Add a thing."')
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("ends with a period", reason)
        self.assertIn("lower case", reason)

    def test_other_tools_ignored(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "/x", "content": "git commit -m x"}}
        self.assertIsNone(run_hook(COMMIT_MESSAGE, payload))

    def test_garbage_input_is_ignored(self):
        result = subprocess.run(
            [sys.executable, COMMIT_MESSAGE], input="not json", capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout, "")


@unittest.skipIf(shutil.which("gofmt") is None, "gofmt not installed")
class FormatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def run_format(self, path):
        return run_hook(FORMAT, {"tool_name": "Write", "tool_input": {"file_path": path}, "cwd": self.tmp})

    def test_reformats_and_reports(self):
        path = self.write("main.go", "package main\nfunc main( ) {\n}\n")
        output = self.run_format(path)
        self.assertIn("reformatted", output["hookSpecificOutput"]["additionalContext"])
        with open(path) as f:
            self.assertEqual(f.read(), "package main\n\nfunc main() {\n}\n")

    def test_silent_when_already_formatted(self):
        self.assertIsNone(self.run_format(self.write("ok.go", "package main\n\nfunc main() {\n}\n")))

    def test_reports_formatter_errors(self):
        output = self.run_format(self.write("bad.go", "package main\nfunc {\n"))
        self.assertIn("could not format", output["hookSpecificOutput"]["additionalContext"])

    def test_ignores_other_files(self):
        self.assertIsNone(self.run_format(self.write("notes.md", "#  x\n")))

class GitHooksTest(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "claude", "hooks", "git_hooks.py")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.workspace = os.path.join(self.tmp, "workspace")
        for name, hooks, hooks_path, origin in (
            ("with-hooks", True, None, "https://github.com/arikkfir-org/with-hooks"),
            ("without-hooks", False, None, "https://github.com/arikkfir-org/without-hooks"),
            ("own-hooks-path", True, "custom", "https://github.com/arikkfir-org/own-hooks-path"),
            ("third-party", True, None, "https://github.com/someone-else/tool"),
            ("other-host", True, None, "https://gitlab.com/arikkfir-org/tool"),
            ("no-origin", True, None, None),
        ):
            path = os.path.join(self.workspace, name)
            os.makedirs(path)
            git(path, "init", "--quiet")
            if hooks:
                os.makedirs(os.path.join(path, ".githooks"))
            if hooks_path:
                git(path, "config", "core.hooksPath", hooks_path)
            if origin:
                git(path, "remote", "add", "origin", origin)
        os.makedirs(os.path.join(self.workspace, "not-a-repository", ".githooks"))
        self.attached = os.path.join(self.tmp, "attached")
        os.makedirs(os.path.join(self.attached, ".githooks"))
        git(self.attached, "init", "--quiet")
        git(self.attached, "remote", "add", "origin", "git@github.com:arikkfir-org/attached.git")

    def hooks_path(self, directory):
        result = subprocess.run(
            ["git", "-C", directory, "config", "--local", "--get", "core.hooksPath"], capture_output=True, text=True,
        )
        return result.stdout.strip() or None

    def run_git_hooks(self, payload, remote="true"):
        return run_hook(self.SCRIPT, payload, env={"CLAUDE_CODE_REMOTE": remote})

    def test_session_start_arms_repositories_that_ship_hooks(self):
        output = self.run_git_hooks({"hook_event_name": "SessionStart", "cwd": self.workspace})
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("with-hooks", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.hooks_path(os.path.join(self.workspace, "with-hooks")), ".githooks")
        self.assertIsNone(self.hooks_path(os.path.join(self.workspace, "without-hooks")))
        self.assertEqual(self.hooks_path(os.path.join(self.workspace, "own-hooks-path")), "custom")
        for name in ("third-party", "other-host", "no-origin"):
            with self.subTest(repository=name):
                self.assertNotIn(name, output["hookSpecificOutput"]["additionalContext"])
                self.assertIsNone(self.hooks_path(os.path.join(self.workspace, name)))
        self.assertIsNone(self.run_git_hooks({"hook_event_name": "SessionStart", "cwd": self.workspace}))

    def test_session_start_in_a_repository(self):
        repository = os.path.join(self.workspace, "with-hooks")
        output = self.run_git_hooks({"hook_event_name": "SessionStart", "cwd": repository})
        self.assertIn("with-hooks", output["hookSpecificOutput"]["additionalContext"])

    def test_register_repo_root_arms_the_attached_repository(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__claude-code-remote__register_repo_root",
            "tool_input": {"owner": "arikkfir-org", "repo": "attached", "directory": self.attached},
            "cwd": os.path.join(self.workspace, "without-hooks"),
        }
        output = self.run_git_hooks(payload)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertEqual(self.hooks_path(self.attached), ".githooks")

    def test_does_nothing_outside_cloud_sessions(self):
        self.assertIsNone(self.run_git_hooks({"hook_event_name": "SessionStart", "cwd": self.workspace}, remote=""))
        self.assertIsNone(self.hooks_path(os.path.join(self.workspace, "with-hooks")))

    def test_garbage_input_is_ignored(self):
        result = subprocess.run(
            [sys.executable, self.SCRIPT], input="not json", capture_output=True, text=True, check=True,
            env={**os.environ, "CLAUDE_CODE_REMOTE": "true"},
        )
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
