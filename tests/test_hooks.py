import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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


class PullRequestTest(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "claude", "hooks", "pull_request.py")
    RESULT = '{"id":"4711397174","url":"https://github.com/arikkfir-org/tooling/pull/14"}'

    def remind(self, owner="arikkfir-org", reviewers=None, result=RESULT, error=False):
        arguments = {"owner": owner, "repo": "tooling", "title": "t", "head": "b", "base": "main"}
        if reviewers is not None:
            arguments["reviewers"] = reviewers
        payload = {
            "tool_name": "mcp__github__create_pull_request", "tool_input": arguments,
            "tool_result": result, "tool_result_is_error": error,
        }
        output = run_hook(self.SCRIPT, payload)
        return output["hookSpecificOutput"]["additionalContext"] if output else None

    def test_reminds_to_request_the_review_and_check_the_description(self):
        message = self.remind()
        self.assertIn("You opened #14 in arikkfir-org/tooling", message)
        self.assertIn('reviewers: ["arikkfir-reviewer"]', message)
        self.assertIn("ENG-", message)

    def test_skips_the_review_step_when_already_requested(self):
        message = self.remind(reviewers=["Arikkfir-Reviewer"])
        self.assertNotIn("update_pull_request", message)
        self.assertIn("ENG-", message)

    def test_names_no_number_it_cannot_find(self):
        self.assertIn("You opened a pull request in arikkfir-org/tooling", self.remind(result=None))

    def test_silent_elsewhere(self):
        self.assertIsNone(self.remind(owner="weesp-ai"))
        self.assertIsNone(self.remind(error=True))
        payload = {"tool_name": "mcp__github__update_pull_request", "tool_input": {"owner": "arikkfir-org"}}
        self.assertIsNone(run_hook(self.SCRIPT, payload))

    def test_garbage_input_is_ignored(self):
        result = subprocess.run([sys.executable, self.SCRIPT], input="{", capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
