# tooling

Org-wide developer tooling: the Claude Code web bundle (`claude/`) with its installer (`setup/setup.sh`), the pull
request reviewer (`reviewer/`), and the organization pipelines every repository runs (`organization.pipelines` in
`.octomaton.yaml`).

## Rules

- `claude/` is published to a public bucket. Never put secrets, tokens, internal hostnames or personal data in it.
  Allowed files: `claude/CLAUDE.md`, `claude/settings.json`, `claude/mcp.json`, `claude/hooks/*.py` and, per skill,
  `claude/skills/<name>/SKILL.md` with `.md` and `.py` files beside it. Anything else needs a matching change to
  `scripts/verify.py`. Skills are stdlib-only Python too, and link only public pages (GitHub, never the docs site).
- Hooks must never break a session: catch everything, fail open, stay fast (< 1 s typical), stdlib-only Python 3.
- `claude/CLAUDE.md` is the user's personal response style. Change its substance only when asked.
- `setup/setup.sh` must stay idempotent and fail-soft (warn and exit 0 unless `ARIKKFIR_CLAUDE_STRICT=1`). Keep the
  `@BUNDLE_SHA256@` placeholder; `scripts/build.sh` pins it.
- Bundles are content-addressed and immutable. Upload bundles before `setup.sh`.
- CI is Octomaton + Tekton (`.octomaton.yaml`, `.tekton/ci.yaml`, `.tekton/publish.yaml`,
  `.tekton/reviewer-image.yaml`, `.tekton/reviewer-image-check.yaml`), not GitHub Actions. Only `publish` and
  `reviewer-image`, which run on pushes to `main`, may name the ServiceAccount with Google Cloud roles
  (`ci-tooling-publish`, `main` only).
- This is the organization repository: `organization.pipelines` in `.octomaton.yaml` run in every repository, read from
  `main`. A change there applies org-wide; say so in the pull request.
- `reviewer/`: Octomaton runs `reviewer/pipelinerun.yaml` from the default branch for every repository. Its Python is
  stdlib-only; cover every rule and review action in `tests/test_reviewer.py`.
- `reviewer/report.py` holds `arikkfir-reviewer`'s token: it never runs or trusts anything from the shared volume, and
  reads only `findings.json` there, as data. Its step, the `review` task's last, is the only container with the token,
  runs its own clone of this repository, and keeps `HOME` and its working directory off the volume.
- The findings schema lives in `reviewer/prompt.md`, `reviewer/findings.py` and the design
  (`docs/tooling/designs/pr-reviewer.md`). Change them together.
- `docs-site/`: the docs site's organization pipelines (`docs`, `docs-publish`). Their runs take `docs-site/` from the
  default branch and treat the repository under test as data. `compose.py` is stdlib-only and covered by
  `tests/test_docs_site.py`; `links.lua` by `tests/test_links.sh`.
- `linear/`: check Linear's organization pipeline (`linear`). Its run takes `linear/` from the default branch and never
  fetches the repository under test: `check.py`, stdlib-only, reads the pull request from GitHub and its link from
  Linear. Its PipelineRun references only Secret `linear-api-key`, plus the token workspace Octomaton binds. It runs on
  every pull request automatically, Dependabot's included, so it must never run their code.
- The reviewer's PipelineRun references only Secrets `reviewer-deepseek-api-key` and `reviewer-github-pat`, plus the
  token workspace Octomaton binds. Never add another. The token reads every repository: only the `clone` step (which
  also writes `pr.json`) and the `github` sidecar of `review` mount it (an isolated workspace), never a step that runs
  the model.
- Every reviewer step and the sidecar run in `me-west1-docker.pkg.dev/arikkfir/images/reviewer`, built from
  `reviewer/image/` and published by `reviewer-image` on a push to `main` that changes it: one image for a node to
  pull. Run its `main` tag with `imagePullPolicy: Always`, the same in every step; add tools to the image, not to
  the PipelineRun. Pin its base image by digest. A merge that changes it reaches the next review.
- Large tasks run as Linear projects (https://docs.dev.kfirs.com/CONTRIBUTING.md, "Projects"): suggest one to your
  person and create it once they agree; one issue per slice, however many pull requests it takes; slices preferably
  self-contained and deployable on their own; keep the project's status current, with an update at each milestone or
  significant change.

## Docs

- This repository's designs are in `docs/tooling/designs/`: the reviewer's (`pr-reviewer.md`), the Claude Code
  bundle's (`phase-5-tooling.md`), its sessions' access to the cluster and the docs
  (`claude-code-cluster-access.md`) and check Linear's (`linear-check.md`). A change keeps those it touches true, in
  the same pull request.
- Pages at `https://docs.dev.kfirs.com/<path>` (every repository's `docs/`) are behind the hub's sign-in. Read one with
  `curl -fsS https://storage.googleapis.com/arikkfir-docs/.layers/<repository>/<path>`, where `<repository>` is the
  path's first segment when it names a repository (`infra/terraform/gcp.md` is `infra`'s) and `docs` otherwise
  (`CONTRIBUTING.md`). Don't clone a repository to read its docs. Never write that hostname or bucket into `claude/`,
  which is public.

## Before finishing a change

```bash
python3 -m unittest discover -s tests
sh scripts/build.sh && python3 scripts/verify.py && bash tests/test_setup.sh
shellcheck setup/setup.sh scripts/build.sh tests/test_setup.sh tests/test_links.sh
sh tests/test_links.sh   # needs pandoc
```

`scripts/build.sh` archives `HEAD`: commit before building, or the bundle won't contain your change.
Add a test to `tests/test_hooks.py` for every guard rule you add or change.
