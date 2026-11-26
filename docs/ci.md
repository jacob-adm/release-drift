# Running release-drift in CI

## GitHub Actions

Copy this file to `.github/workflows/release-drift.yml`. On every pull request it finds the
lockfiles the pull request changes (`package-lock.json`, `npm-shrinkwrap.json`,
`requirements*.txt`, `uv.lock`, `poetry.lock`, in any directory), takes each one from the merge
base, and runs `release-drift lockfile` on the pair. The check fails when release-drift reports a
finding (exit status 1) or cannot compare a package (exit status 2). It passes when no lockfile
changed, and lists a lockfile that was added or removed without comparing it. Make it a required
status check in the branch rules.

```yaml
# Compare every package that a lockfile change in the pull request upgrades.
# Fails when release-drift reports a finding (exit 1) or cannot compare a package (exit 2).
name: release-drift
on:
  pull_request:
permissions:
  contents: read
jobs:
  lockfile:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0
        with:
          python-version: "3.13"
      - run: python -m pip install release-drift==0.6.0
      - name: Compare upgraded packages
        env:
          BASE_REF: ${{ github.base_ref }}
        run: |
          base=$(git merge-base "origin/$BASE_REF" HEAD)
          status=0
          while IFS= read -r -d '' file; do
            if ! git cat-file -e "$base:$file" 2>/dev/null || [ ! -f "$file" ]; then
              echo "$file: added or removed, not compared"
              continue
            fi
            git show "$base:$file" > "$RUNNER_TEMP/old-lockfile"
            echo "== $file"
            release-drift lockfile "$RUNNER_TEMP/old-lockfile" "$file" || {
              s=$?; [ "$s" -gt "$status" ] && status=$s; }
          done < <(git diff --name-only -z "$base" HEAD -- '*package-lock.json' \
            '*npm-shrinkwrap.json' '*requirements*.txt' '*uv.lock' '*poetry.lock')
          exit "$status"
```

## pre-commit

The hook runs when a commit changes a lockfile and compares the staged lockfile with the one in
`HEAD`. A lockfile that is new in the commit is skipped. Add it to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: release-drift
        name: release-drift lockfile
        language: python
        additional_dependencies: [release-drift==0.6.0]
        files: (^|/)(package-lock\.json|npm-shrinkwrap\.json|uv\.lock|poetry\.lock|requirements[^/]*\.txt)$
        entry: >-
          sh -c 'status=0; for f in "$@"; do
          git cat-file -e "HEAD:$f" 2>/dev/null || continue;
          old=$(mktemp); git show "HEAD:$f" > "$old";
          release-drift lockfile "$old" "$f" || { s=$?; [ "$s" -gt "$status" ] && status=$s; };
          rm -f "$old"; done; exit "$status"' --
```

## Tested

Tested on 26 November 2026 in a test repository on GitHub, with release-drift 0.6.0:

| Pull request | Check |
| --- | --- |
| Upgrades yallist 4.0.0 to 5.0.0 in `package-lock.json` | Fails: `npm-script added package.json prepare=tshy` |
| Upgrades ms 2.1.2 to 2.1.3 | Passes |
| Changes no lockfile (four pull requests) | Passes |

A new install script is the record that caught Cline 2.3.0 and plain-crypto-js 4.2.1 in the
replays. Those releases, and the other replayed attacks, are no longer on npm or PyPI, so a
workflow cannot download them; `tests/test_replays.py` checks them offline. The pre-commit hook
gave the same results on the same two lockfiles with pre-commit 4.7.0.

## Notes

- Pin the version, as above, and raise it on purpose. A new release can add records, and a check
  that changes under you is not a reviewed check.
- The actions are pinned to a commit, with the release as a comment. Update the commit and the
  comment together.
- A `pull_request` workflow runs the copy of the workflow file in the pull request, so a pull
  request can change or remove the check. Require review of changes under `.github/`, for example
  with a `CODEOWNERS` entry; git-surface reports such changes as `ci`.
- The workflow needs no secret and only `contents: read`. It reads the repository and, where
  stated, downloads packages from the registry; it installs or runs nothing from them.
