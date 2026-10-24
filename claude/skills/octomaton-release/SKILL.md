---
name: octomaton-release
description: Draft a new Octomaton release (arikkfir-org/octomaton) as a GitHub release, with the version and notes generated from the pull requests merged since the previous one. Use when asked to release, cut, tag or version Octomaton, or to draft its release notes.
---

# Releasing Octomaton

A release is a GitHub release of `arikkfir-org/octomaton`: tag `v<version>` on a commit of `main`, title `<version>`,
notes generated from the pull requests merged since the previous version. The skill drafts it; a person publishes it.
Publishing creates the tag, and the tag runs Octomaton's `tag` pipeline, which adds the version to the commit's
images, `me-west1-docker.pkg.dev/arikkfir/octomaton/octomaton:v<version>` and `…/octomaton-logs:v<version>`, without
rebuilding them. A draft has no tag, so nothing happens until it is published. Design: https://github.com/arikkfir-org/octomaton/blob/main/docs/octomaton/designs/releases.md.

## Steps

1. **Generate the notes and the version.** `release_notes.py` beside this file reads GitHub's API (no clone needed)
   and writes the notes from `notes.md`, the template beside it:

   ```bash
   python3 ~/.claude/skills/octomaton-release/release_notes.py --out /tmp/octomaton-release.md --json
   ```

   It finds the newest `vX.Y.Z` tag, lists the pull requests merged into `main` since, decides each one's kind from
   the Conventional Commits subjects of its commits, and picks the version: a breaking change (`type!:` or a
   `BREAKING CHANGE:` footer) raises the major, a `feat` the minor, anything else the patch; a first release is
   `1.0.0`. The person's wishes win: `--version X.Y.Z` sets the version, `--since vA.B.C` the previous one,
   `--target <ref>` the commit to release (default `main`). It refuses a version that is already released.

2. **Show the person the version and the notes** before anything is created, as the file: say which kind of change
   drove the version and ask only if the version or a kind looks wrong (a pull request's commits without a
   conventional subject land under chores). Fix a wrong kind by passing `--version`, not by editing the notes by hand,
   unless the person asks for an edit.

3. **Create the draft release**, never a published one:

   ```bash
   gh release create "v<version>" --repo arikkfir-org/octomaton --draft --target <target sha from --json> \
     --title "<version>" --notes-file /tmp/octomaton-release.md
   ```

   `gh` must be signed in as the person (`gh auth status`); a cloud session whose token is refused cannot create it.
   Then hand the person the notes file and the command to run themselves. Do not post the release through a raw
   `curl`: the session's proxy would author it as `claude[bot]`.

4. **Tell the person what publishing does**, with the draft's URL: publishing creates tag `v<version>` on the target
   commit, Octomaton's `tag` pipeline (check `tag` on that commit) tags the commit's two images `v<version>`, and
   nothing changes for hosts that follow `main`. It refuses a commit that is not on `main`.
   Do not publish the release yourself.

## Rules

- Draft only. Publishing is the person's step: it tags images and cannot be undone (a deleted tag can be
  recreated, but the images keep the tag).
- The tag is `v<version>`, the title `<version>`, the notes are the generated file: don't rename, don't add
  attribution, don't write release notes by hand when the script can generate them.
- The release targets `main`, or a commit of `main` the person names. Never a branch or an unmerged commit.
- The notes mention authors with `@login` and link every pull request; keep both when editing.
- Nothing here needs a clone of `octomaton`; the script and `gh` talk to GitHub.
