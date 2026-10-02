import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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
            ("git push --prune origin main", "on-feature"),
            ("git push --force-w origin main", "on-feature"),
            ("git push --mirr origin", "on-feature"),
            ("timeout 30 git push -f origin main", "on-feature"),
            ("nice -n 10 git push -f origin main", "on-feature"),
            ("sudo -u root git push -f origin main", "on-feature"),
            ("env -u HOME git push -f origin main", "on-feature"),
            ("time -o /dev/null git push -f origin main", "on-feature"),
            ("git push --prune origin 'refs/heads/*:refs/heads/*' master", "on-feature"),
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
            ("git switch -qf main && git push -f origin main", "on-feature"),
        ]:
            with self.subTest(command=command, cwd=cwd):
                self.assertEqual(self.decide(command, cwd), "deny")

    def test_asked(self):
        for command, cwd in [
            ("git push origin --delete feature", "on-feature"),
            ("git push -d origin feature", "on-feature"),
            ("git push origin :feature", "on-feature"),
            ("git push origin :refs/heads/feature", "on-feature"),
            ("git push --prune origin 'refs/heads/*:refs/heads/*'", "on-feature"),
            ("git switch -f main", "on-feature"),
            ("git switch -qf main", "on-feature"),
            ("git switch main --discard-changes", "on-feature"),
            ("git switch --disc main", "on-feature"),
            ("git switch -C feature", "on-feature"),
            ("git switch -qC feature", "on-feature"),
            ("git switch --force-create=feature", "on-feature"),
            ("git switch --force-c feature", "on-feature"),
            ("git -C ../on-main switch --force main", "on-feature"),
            ("git add -A && git switch -qf main", "on-feature"),
            ("bash -c 'git switch -qf main'", "on-feature"),
            ("git push origin --del feature", "on-feature"),
            ("git push --pru origin", "on-feature"),
            ("timeout 30 git switch -qf main", "on-feature"),
            ("timeout -s KILL 30s git push origin :feature", "on-feature"),
            ("stdbuf -o L git switch -qf main", "on-feature"),
            ("xargs git switch -qf", "on-feature"),
            ("builtin git switch -qf main", "on-feature"),
            ("nice -n 10 git switch -qf main", "on-feature"),
            ("nice --adjustment 10 git push origin :feature", "on-feature"),
            ("exec -a name git switch -qf main", "on-feature"),
        ]:
            with self.subTest(command=command, cwd=cwd):
                self.assertEqual(self.decide(command, cwd), "ask")

    def test_ask_names_only_the_deleted_branches(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git push origin feature :old"}, "cwd": self.tmp}
        output = run_hook(GUARD, payload, env={"HOME": self.home})
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(reason, "This push deletes old on the remote.")

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
            ("git push --dry-run origin :feature", "on-feature"),
            ("git switch main", "on-feature"),
            ("git switch -c feature", "on-feature"),
            ("git switch -cfix", "on-feature"),
            ("git switch --create fix-ffoo", "on-feature"),
            ("git switch --detach main", "on-feature"),
            ("git switch -m main", "on-feature"),
            ("git switch --no-guess main", "on-feature"),
            ("git push --dry origin :feature", "on-feature"),
            ("git push --no-force-with-lease origin main", "on-feature"),
            ("git push --repo origin feature", "on-feature"),
            ("timeout 30 git status", "on-feature"),
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
            ('timeout 30 git commit -m "add a thing"', "org"),
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


if __name__ == "__main__":
    unittest.main()
