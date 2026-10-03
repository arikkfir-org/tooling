# tooling

Org-wide developer tooling: the Claude Code web bundle (`claude/`) with its installer (`setup/setup.sh`), the pull
request reviewer (`reviewer/`), and the organization pipelines every repository runs (`organization.pipelines` in
`.octomaton.yaml`).

## Rules

- `claude/` is published to a public bucket. Never put secrets, tokens, internal hostnames or personal data in it.
  Allowed files: `claude/CLAUDE.md`, `claude/settings.json`, `claude/hooks/*.py`. Anything else needs a matching change
  to `scripts/verify.py`.
- Hooks must never break a session: catch everything, fail open, stay fast (< 1 s typical), stdlib-only Python 3.
- `claude/CLAUDE.md` is the user's personal response style. Change its substance only when asked.
- `setup/setup.sh` must stay idempotent and fail-soft (warn and exit 0 unless `ARIKKFIR_CLAUDE_STRICT=1`). Keep the
  `@BUNDLE_SHA256@` placeholder; `scripts/build.sh` pins it.
- Bundles are content-addressed and immutable. Upload bundles before `setup.sh`.
- CI is Octomaton + Tekton (`.octomaton.yaml`, `.tekton/ci.yaml`, `.tekton/publish.yaml`), not GitHub Actions. Only
  `publish` may name a ServiceAccount with Google Cloud roles (`ci-tooling-publish`, `main` only).
- This is the organization repository: `organization.pipelines` in `.octomaton.yaml` run in every repository, read from
  `main`. A change there applies org-wide; say so in the pull request.
- `reviewer/`: Octomaton runs `reviewer/pipelinerun.yaml` from the default branch for every repository. Its Python is
  stdlib-only; cover every rule and review action in `tests/test_reviewer.py`.
- `reviewer/report.py` holds `arikkfir-reviewer`'s token: it never runs or trusts anything from the shared volume, and
  reads only `findings.json` there, as data.
- The findings schema lives in `reviewer/prompt.md`, `reviewer/findings.py` and the design (`hub/designs/pr-reviewer.md`
  in `arikkfir-org/docs`). Change them together.
- `docs-site/`: the docs site's organization pipelines (`docs`, `docs-publish`). Their runs take `docs-site/` from the
  default branch and treat the repository under test as data. `compose.py` is stdlib-only and covered by
  `tests/test_docs_site.py`; `links.lua` by `tests/test_links.sh`.
- The reviewer's PipelineRun references only Secrets `reviewer-deepseek-api-key` and `reviewer-github-pat`, plus the
  token workspace Octomaton binds. Never add another. The token reads every repository: only `setup` and the `github`
  sidecar of `review` mount it (an isolated workspace), never a step that runs the model.
- Every reviewer step and the sidecar run in `me-west1-docker.pkg.dev/arikkfir/images/reviewer`, which
  `arikkfir-org/octomaton` builds (`images/reviewer`): one image for a node to pull. Pin it by digest, the same in every
  step; add tools there, not here.

## Before finishing a change

```bash
python3 -m unittest discover -s tests
sh scripts/build.sh && python3 scripts/verify.py && bash tests/test_setup.sh
shellcheck setup/setup.sh scripts/build.sh tests/test_setup.sh tests/test_links.sh
sh tests/test_links.sh   # needs pandoc
```

`scripts/build.sh` archives `HEAD`: commit before building, or the bundle won't contain your change.
Add a test to `tests/test_hooks.py` for every guard rule you add or change.
