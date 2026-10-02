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
FORMAT = os.path.join(ROOT, "claude", "hooks", "format.py")


def run_hook(script, payload, env=None):
    result = subprocess.run(
        [sys.executable, script], input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, **(env or {})}, check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def git(directory, *args):
    subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True)


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


if __name__ == "__main__":
    unittest.main()
