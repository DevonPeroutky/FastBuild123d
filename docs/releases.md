# Automated releases

Pushes to `main` run the full test suite with locked dependencies on Ubuntu and
Python 3.12. After tests pass, Python Semantic Release 10.7.0 calculates the next
version, updates `pyproject.toml`, refreshes `uv.lock`, generates `CHANGELOG.md`,
and commits and tags the release. Release tooling runs separately from package
and development dependencies. No dependency upgrades are permitted during a release.

The workflow validates wheel and source metadata with Twine, retains the exact
archives and checksums for 90 days, then atomically pushes the commit and tag.
A separate job downloads those archives, creates GitHub release notes, publishes
via PyPI Trusted Publishing, and attaches the same archives to GitHub. It never
rebuilds in the publishing job. Bot-created tags do not need to trigger a workflow.

## Commit conventions

Use Conventional Commits for commits merged to `main`. With squash merges, set
the **squash commit title** to the conventional title; preserve breaking-change
footers in the squash commit body. The highest-impact commit since the last tag
wins.

| Commit | Result starting at 0.1.6 |
| --- | --- |
| `fix: repair cache` or `perf: speed up builds` | 0.1.7 |
| `feat: add a control` | 0.2.0 |
| `feat!: replace an interface` | 0.2.0 |
| A commit with a `BREAKING CHANGE:` footer | 0.2.0 |
| `docs:`, `chore:`, or `test:` changes | No release |

Scopes are supported, for example `fix(cache): avoid stale results`.
A breaking-change footer overrides an otherwise non-releasing commit type.
Moving to 1.0 is an explicit maintainer decision: change `allow_zero_version` to
`false` in a reviewed change when ready. Until then, breaking changes bump the
minor version.

Preview on `main` with full Git history and tags:

```sh
uv run --locked poe release
```

This task is a dry run. It does not build, edit files, commit, tag, or publish.
Feature branches are deliberately excluded from release calculation.

## One-time setup before merging the workflow

1. In [repository environments](https://github.com/DevonPeroutky/FastBuild123d/settings/environments),
   create an environment named **`pypi`**. Choose **Selected branches and tags**,
   add a **branch** rule for `main` only, and configure **no required reviewers**
   or wait timer. Do not add a tag rule: publishing runs in the original main push.
2. As a PyPI project owner, open
   [fastbuild123 publishing settings](https://pypi.org/manage/project/fastbuild123/settings/publishing/)
   and add a GitHub Trusted Publisher with these exact values:

   | Field | Value |
   | --- | --- |
   | PyPI project | `fastbuild123` |
   | Owner | `DevonPeroutky` |
   | Repository | `FastBuild123d` |
   | Workflow filename | `release.yml` |
   | Environment | `pypi` |

   See [PyPI's registration instructions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).
   No PyPI token secret is needed.
3. Ensure repository/org policy permits the workflow's `GITHUB_TOKEN` to write
   repository contents. Branch rules must allow the release bot's commit to
   `main`; rules requiring every commit to arrive through a PR can reject it.
   Review any rule change separately with the repository administrators.
4. Merge this automation with **`chore: configure automated releases`** after
   configuring the publisher. At the current `v0.1.6` baseline this is a no-release
   change. Unreleased `feat`/`fix`/breaking commits merged alongside it would still
   cause a release.
5. Confirm that setup's workflow passes with publication skipped. After the next
   real release-worthy change, confirm the GitHub tag, GitHub release, PyPI version,
   project metadata, and lockfile version match.

Tests have read-only repository access. Only release and publish jobs get
`contents: write`; only publish gets `id-token: write`. Pull requests, failed tests,
and no-release calculations cannot publish. Main workflows are serialized through
publication without cancellation. A superseded tested revision is skipped; a
concurrent push during the build rejects the atomic commit/tag push.

## Recover a failed publish

Open the original **Test and release** run in Actions and choose **Re-run failed
jobs** (or re-run just **publish**). Keep the successful release job intact. Its
outputs identify the original tag and its artifact name uses the unchanged run ID.
The publish job checks the retained checksums and tag metadata, then reuses the
same distributions without calculating another version or rebuilding.

Publishing is retryable after a partial success: PyPI skips already-existing
files, GitHub release notes are updated, and GitHub assets are replaced from the
same retained archives. Complete recovery before the 90-day retention expires.
If artifacts have expired, stop and recover the exact original files from a
verified published copy; do not rebuild a previously published version.

Do **not** choose Re-run all jobs for publication recovery. The tested SHA is now
behind the release commit, so the stale-head guard will skip release calculation.
If the release job failed before pushing, no publication takes place. Inspect
whether the tag exists remotely before retrying that job; do not move tags or
force-push. If main advanced, the newer main run will perform release calculation.

The uv lock refresh follows the
[Python Semantic Release uv integration](https://python-semantic-release.readthedocs.io/en/stable/configuration/configuration-guides/uv_integration.html),
with an additional check that only this project's version changed in the lockfile.
