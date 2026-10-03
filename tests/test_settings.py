import fnmatch
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(ROOT, "claude", "settings.json")

# Commands the repositories' CLAUDE.md files forbid, or that change something outside the checkout: no allow rule may
# cover them.
FORBIDDEN = [
    "terraform apply",
    "terraform apply -auto-approve",
    "terraform destroy",
    "terraform import google_storage_bucket.x x",
    "terraform state rm google_storage_bucket.x",
    "terraform taint google_storage_bucket.x",
    "terraform force-unlock 1234",
    "terraform plan",
    "terraform init",
    "make terraform gcp",
    "kubectl apply -f x.yaml",
    "kubectl edit deployment x",
    "kubectl patch deployment x -p {}",
    "kubectl delete pod x",
    "helm install x y",
    "helm upgrade x y",
    "gcloud storage ls",
    "git reset --hard",
    "git clean -fdx",
    "git rebase main",
    "rm -rf build",
    # Local work that can't be recovered, and branches deleted on the remote.
    "git checkout -- .",
    "git checkout README.md",
    "git branch -D feature",
    "git stash drop",
    "git stash clear",
    # git switch: discarding changes or resetting a branch, in any position, with attached values, and in the
    # abbreviations git accepts for long options.
    "git switch -f main",
    "git switch main -f",
    "git switch --force main",
    "git switch --discard-changes main",
    "git switch main --discard-changes",
    "git switch --disc main",
    "git switch -C feature",
    "git switch -Cfeature",
    "git switch -C feature origin/main",
    "git switch --force-create feature",
    "git switch --force-create=feature",
    "git switch --force-c feature",
    "git switch -c feature --force-create other",
    "git push --delete origin feature",
    "git push -d origin feature",
    "git push origin --delete feature",
]
# guard.py asks about what a glob can't express: clustered short options (`git switch -qf`), which a glob can't tell
# from `git switch -c feature`, and `:branch` refspecs, since a rule ending in `:*` is a trailing wildcard (GuardTest).

# Routine commands the bundle exists to run without a prompt.
ROUTINE = [
    "git add -A",
    "git commit -m 'fix: x'",
    "git switch -c feature",
    "git switch main",
    "git fetch origin main",
    "git push -u origin HEAD",
    "go test ./...",
    "terraform validate",
    "terraform init -backend=false -input=false",
    "kubectl kustomize deploy",
    "python3 -m unittest discover -s tests",
]

# The tools of GKE's remote MCP server (container.googleapis.com/mcp, MCP server gke in mcp.json): sessions read the hub
# cluster without a prompt and never change it.
GKE_READ_TOOLS = [
    "list_k8s_api_resources",
    "check_k8s_auth",
    "describe_k8s_resource",
    "list_k8s_events",
    "get_k8s_resource",
    "get_k8s_cluster_info",
    "get_k8s_version",
    "get_k8s_rollout_status",
    "get_k8s_logs",
    "list_clusters",
    "get_cluster",
    "list_operations",
    "get_operation",
    "list_node_pools",
    "get_node_pool",
]
GKE_WRITE_TOOLS = [
    "apply_k8s_manifest",
    "patch_k8s_resource",
    "delete_k8s_resource",
    "create_cluster",
    "update_cluster",
    "delete_cluster",
    "create_node_pool",
    "update_node_pool",
    "delete_node_pool",
    "cancel_operation",
]


def bash_rule(rule):
    """The regex of a Bash(…) rule: `*` is any text, a trailing ` *` also matches the bare command, and a trailing
    `:*` is the same as ` *`."""
    pattern = rule[len("Bash("):-1]
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"
    if pattern.endswith(" *") and pattern.count("*") == 1:
        return re.compile(re.escape(pattern[:-2]) + r"(?: .*)?", re.S)
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")), re.S)


class PermissionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETTINGS) as f:
            cls.permissions = json.load(f)["permissions"]

    def bash_rules(self, kind):
        return [bash_rule(rule) for rule in self.permissions[kind] if rule.startswith("Bash(")]

    def runs_without_a_prompt(self, command):
        """Ask rules win over allow rules: a command runs unasked when it matches an allow rule and no ask rule."""
        return any(rule.fullmatch(command) for rule in self.bash_rules("allow")) and not any(
            rule.fullmatch(command) for rule in self.bash_rules("ask")
        )

    def test_rules_are_scoped(self):
        for kind in ("allow", "ask", "deny"):
            for rule in self.permissions[kind]:
                with self.subTest(rule=rule):
                    self.assertRegex(rule, r"^(Bash\([^*()][^()]*\)|mcp__(github|gke)__[a-z_]+\*?)$")

    def test_forbidden_commands_never_run_unasked(self):
        for command in FORBIDDEN:
            with self.subTest(command=command):
                self.assertFalse(self.runs_without_a_prompt(command))

    def test_routine_commands_run_unasked(self):
        for command in ROUTINE:
            with self.subTest(command=command):
                self.assertTrue(self.runs_without_a_prompt(command))

    def test_recursive_deletion_asks(self):
        ask = self.bash_rules("ask")
        for command in ["rm -rf build", "rm -fr build", "rm -Rf build", "rm -r build", "rm -R build"]:
            with self.subTest(command=command):
                self.assertTrue(any(rule.fullmatch(command) for rule in ask))

    def test_github_rules_only_read(self):
        for rule in self.permissions["allow"]:
            if rule.startswith("mcp__github__"):
                with self.subTest(rule=rule):
                    self.assertRegex(rule, r"^mcp__github__(get_|list_|search_|pull_request_read$|issue_read$)")

    def test_gke_reads_run_unasked_and_writes_are_denied(self):
        def matches(kind, tool):
            return any(fnmatch.fnmatchcase(f"mcp__gke__{tool}", rule) for rule in self.permissions[kind])

        for tool in GKE_READ_TOOLS:
            with self.subTest(tool=tool):
                self.assertTrue(matches("allow", tool))
                self.assertFalse(matches("deny", tool))
        for tool in GKE_WRITE_TOOLS:
            with self.subTest(tool=tool):
                self.assertTrue(matches("deny", tool))
                self.assertFalse(matches("allow", tool))


if __name__ == "__main__":
    unittest.main()
