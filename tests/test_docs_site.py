"""Tests of docs-site/compose.py. Run: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "docs-site"))

import compose  # noqa: E402  (imported from docs-site/, as the pipelines run it)

LAYERS = "gs://arikkfir-docs/.layers"


class SourcesTest(unittest.TestCase):
    FILES = ["README.md", "docs/guide.md", "docs/dir1/x.md", "docs/.hidden.md", "docs/dir1/.draft/y.md",
             ".tekton/ci.yaml", "hub/reference.md", "hub/overview.html", ".github/z.md", "docsx/a.md"]

    def test_docs_repository_publishes_its_whole_tree(self):
        self.assertEqual(compose.sources("docs", self.FILES), {
            "README.md": "README.md", "docs/guide.md": "docs/guide.md", "docs/dir1/x.md": "docs/dir1/x.md",
            "hub/reference.md": "hub/reference.md", "hub/overview.html": "hub/overview.html",
            "docsx/a.md": "docsx/a.md"})

    def test_other_repositories_publish_their_docs_directory_at_the_root(self):
        self.assertEqual(compose.sources("infra", self.FILES),
                         {"guide.md": "docs/guide.md", "dir1/x.md": "docs/dir1/x.md"})

    def test_no_docs_directory(self):
        self.assertEqual(compose.sources("infra", ["README.md", "main.tf"]), {})


class LayersTest(unittest.TestCase):
    def test_parses_other_layers(self):
        urls = [f"{LAYERS}/docs/hub/reference.md\n", f"{LAYERS}/infra/guide.md", f"{LAYERS}/infra/dir1/x.md",
                f"{LAYERS}/octomaton/a b.md", f"{LAYERS}/infra/dir1/", "gs://arikkfir-docs/README.md", "", "junk"]
        self.assertEqual(compose.layers(urls, "tooling"), {
            "docs": {"hub/reference.md"}, "infra": {"guide.md", "dir1/x.md"}, "octomaton": {"a b.md"}})

    def test_leaves_out_own_layer(self):
        urls = [f"{LAYERS}/infra/guide.md", f"{LAYERS}/docs/README.md"]
        self.assertEqual(compose.layers(urls, "infra"), {"docs": {"README.md"}})


class ProblemsTest(unittest.TestCase):
    def test_no_problems_when_only_directories_are_shared(self):
        mine = {"dir1/doc1.md": "", "doc.md": ""}
        others = {"infra": {"dir1/infra-doc1.md", "infra-doc.md"}}
        self.assertEqual(compose.problems(mine, others), [])

    def test_same_path_collides(self):
        problems = compose.problems({"dir1/x.md": ""}, {"infra": {"dir1/x.md"}, "tooling": {"dir1/x.md"}})
        self.assertEqual(problems, [
            "dir1/x.md: infra already publishes this path; rename or move one of them",
            "dir1/x.md: tooling already publishes this path; rename or move one of them"])

    def test_file_where_another_layer_has_a_directory(self):
        problems = compose.problems({"guide": ""}, {"infra": {"guide/x.md"}})
        self.assertEqual(problems, ["guide: infra publishes a directory at this path"])

    def test_directory_where_another_layer_has_a_file(self):
        problems = compose.problems({"guide/x.md": ""}, {"infra": {"guide"}})
        self.assertEqual(problems, ["guide/x.md: infra publishes a file at guide, which this path needs as a directory"])

    def test_rendered_page_names_are_reserved(self):
        problems = compose.problems({"x.md.html": "", "y.html": "", "z.md": ""}, {})
        self.assertEqual(problems, ["x.md.html: the name is reserved for the page the site renders from x.md"])


class ComposeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.checkout = os.path.join(self.tmp, "repo")
        for path, text in {"docs/guide.md": "# Guide\n", "docs/dir1/x.md": "# X\n", "docs/img.png": "png",
                           "docs/clash": "mine"}.items():
            os.makedirs(os.path.dirname(os.path.join(self.checkout, path)), exist_ok=True)
            with open(os.path.join(self.checkout, path), "w") as f:
                f.write(text)
        self.site = os.path.join(self.tmp, "site")

    def read(self, path):
        with open(os.path.join(self.site, path)) as f:
            return f.read()

    def test_writes_sources_and_placeholders(self):
        mine = compose.sources("infra", ["docs/guide.md", "docs/dir1/x.md", "docs/img.png"])
        links = compose.compose(self.checkout, mine, {"docs": {"hub/reference.md", "dir1/y.md"}}, self.site)
        self.assertEqual(links, ["dir1/x.md", "guide.md"])
        self.assertEqual(self.read("guide.md"), "# Guide\n")
        self.assertEqual(self.read("img.png"), "png")
        self.assertEqual(self.read("hub/reference.md"), "")
        self.assertEqual(self.read("dir1/y.md"), "")

    def test_skips_what_collides_with_another_layer_as_a_directory(self):
        mine = compose.sources("infra", ["docs/clash"])
        links = compose.compose(self.checkout, mine, {"docs": {"clash/x.md"}}, self.site)
        self.assertEqual(links, [])
        self.assertTrue(os.path.isdir(os.path.join(self.site, "clash")))

    def test_empty(self):
        self.assertEqual(compose.compose(self.checkout, {}, {}, self.site), [])
        self.assertTrue(os.path.isdir(self.site))


class MainTest(unittest.TestCase):
    def test_reports_problems_to_the_file_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "repo", "docs"))
            with open(os.path.join(tmp, "repo", "docs", "README.md"), "w") as f:
                f.write("# Infra\n")
            with open(os.path.join(tmp, "files"), "wb") as f:
                f.write(b"main.tf\0docs/README.md\0")
            with open(os.path.join(tmp, "layers"), "w") as f:
                f.write(f"{LAYERS}/docs/README.md\n{LAYERS}/infra/old.md\n")
            problems = os.path.join(tmp, "problems")
            with open(problems, "w") as f:
                f.write("error: earlier\n")
            argv = ["compose.py", "--repository", "infra", "--checkout", os.path.join(tmp, "repo"),
                    "--files", os.path.join(tmp, "files"), "--layers", os.path.join(tmp, "layers"),
                    "--site", os.path.join(tmp, "site"), "--links", os.path.join(tmp, "links"),
                    "--problems", problems]
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(compose.main(), 0)
            with open(problems) as f:
                self.assertEqual(f.read(), "error: earlier\n"
                                 "error: README.md: docs already publishes this path; rename or move one of them\n")
            with open(os.path.join(tmp, "links")) as f:
                self.assertEqual(f.read(), "README.md\n")
            self.assertIn("infra: 1 source(s); other layers: docs (1)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
