# Using it with other tools

This page shows the tested stacks release-drift is part of, then what two similar tools cover.

The commands on this page use [release-drift](https://github.com/jacob-adm/release-drift) and
[git-surface](https://github.com/jacob-adm/git-surface). release-drift and git-surface are not on
PyPI yet and install from GitHub:

```sh
pip install git+https://github.com/jacob-adm/release-drift
pip install git+https://github.com/jacob-adm/git-surface
```

The examples come from a test repository whose `main` has a small Python module, its tests, a
`requirements.txt`, a changelog and a Markdown guide, and six branches as an agent might push
them: `agent/a` renames a function, `agent/b` changes the same line, `agent/c` adds a module that
calls the function by its old name, `agent/ci` adds a workflow that passes the pull request title
to a step holding a secret, `agent/deps` bumps a pinned version in `requirements.txt`, and
`agent/docs` adds an HTML comment to the guide. Every command was run on that repository and the
output shown is what it printed.

**Exit status.** `git overlap`, `git pairmerge`, `git surface`, `trust-path` and
`release-drift diff` and `source` exit like `grep`: 0 nothing found, 1 found, 2 error.
`git footprint` and `release-drift fetch` exit 0 on success. With `set -o pipefail`, a pipeline
in which any step found something exits 1: treat 1 as a finding, not a failure, and only 2 as an
error. In a `for` or `while` loop the status is that of the last command only, so read the
output.

## Checking a release against its tag (release-drift)

Download every file of a PyPI release, then compare each with the tag it was built from, in a
clone of the project's repository:

```sh
mkdir dist
release-drift fetch pypi git-overlap 0.3.2 dist --type all
for f in dist/*; do release-drift source "$f" --repo git-overlap --ref v0.3.2; done
```

```text
dist/git_overlap-0.3.2-py3-none-any.whl
dist/git_overlap-0.3.2.tar.gz
```

`fetch` prints the saved paths. `source` printed nothing and exited 0 for both files: every file
in the wheel and the sdist is in the tag as it is, or is one that packaging tools generate. A
record from `source` names a file that is not in the tag, or differs from it, with its execution
surfaces, URL hosts and blobs.

**What it catches:** files in a published release that do not come from the tagged source.
**What it does not catch:** a change committed to the repository before the tag; `source` takes
the tag as the reference.

## Checking dependency bumps (git surface, then release-drift)

`git surface` reports a pinned version change as `name old->new` in the `deps` detail. The awk
recipe in git-surface's [Classes](https://github.com/jacob-adm/git-surface/blob/main/docs/classes.md) page turns those into
`NAME TAB OLD TAB NEW` lines; `release-drift fetch` downloads both versions from PyPI and
`release-drift diff` reports what the new one adds:

```sh
git for-each-ref --format='%(refname:short)' refs/heads/agent/ | git surface main | awk -F'\t' '$2 == "deps" {
    n = split($3, w, " ")
    for (i = 2; i < n; i++) if (w[i] !~ /^[+-]/ && w[i+1] ~ /->/) {
        split(w[i+1], v, "->"); print w[i] "\t" v[1] "\t" v[2]; i++
    }
}' > bumps.tsv
while IFS="$(printf '\t')" read -r name old new; do
    mkdir -p "old/$name" "new/$name"
    release-drift fetch pypi "$name" "$old" "old/$name" &&
    release-drift fetch pypi "$name" "$new" "new/$name" &&
    release-drift diff "old/$name"/* "new/$name"/*
done < bumps.tsv
```

```text
old/git-overlap/git_overlap-0.3.1.tar.gz
new/git-overlap/git_overlap-0.3.2.tar.gz
file	changed	PKG-INFO	-
file	changed	README.md	-
file	changed	git_overlap.egg-info/PKG-INFO	-
file	changed	git_overlap/__init__.py	-
file	changed	pyproject.toml	-
```

`agent/deps` bumps `git-overlap` from 0.3.1 to 0.3.2. `fetch` takes the sdist by default, so each
directory holds one file. Only `file` records came back and `diff` exited 0: the new version adds
no execution surface, URL host or blob.

**What it catches:** new install-time code, native files, URL hosts and blobs in a bumped
dependency. **What it does not catch:** requirements without a pinned version, and the
dependencies of the dependency, which are not in the diff.

## Similar tools

Two widely used free tools look at the same problem: GuardDog and Socket. This section states what
each covers, as documented by its project in November 2026, and what release-drift does
differently. Check each project's own documentation for its current state.

### GuardDog

[GuardDog](https://github.com/DataDog/guarddog) is a command-line tool from Datadog, licensed
Apache-2.0, that looks for malicious packages.

- **Ecosystems:** PyPI and npm, plus Go modules, Rust crates, RubyGems, GitHub Actions and VS Code
  extensions.
- **Source code rules:** YARA rules over the package's files, for example
  `threat-npm-preinstall-script`, `threat-setup-network-in-install`,
  `threat-runtime-obfuscation-base64exec` and `threat-network-outbound-shady-links`.
- **Metadata rules:** checks on the registry's metadata, for example typosquatting,
  `bundled_binary`, `new_install_script` (npm: a version adds install scripts that earlier
  versions did not have), `provenance_regression` (npm: a version lost the provenance attestation
  earlier versions had) and `repository_integrity_mismatch` (PyPI: files present both in the
  package and in its linked GitHub repository, at a tag matching the version, whose contents
  differ).
- **Scoring:** findings are combined into a risk score from 0 to 10 and labelled low, medium or
  high.
- **Input and output:** downloads a package by name, or scans a local directory or archive; also
  scans dependency files (`verify`). Output is text, JSON, or SARIF for `verify`.
- **Isolation:** extraction and scanning run in a kernel sandbox (Landlock on Linux, Seatbelt on
  macOS) by default.

### Socket

[Socket](https://socket.dev) is a commercial service with a free plan. Its open-source CLI
(`npm install -g socket`, Node.js 22 or later) works with the Socket API.

- **Alerts:** a list of alert types per package, including install scripts (JavaScript only),
  native code, network access, shell access, use of `eval`, obfuscated and minified code, high
  entropy strings, git, GitHub and HTTP dependencies, and manifest confusion (JavaScript only).
  Its GitHub integration comments on pull requests that add dependencies with new alerts, such
  as newly added install scripts.
- **Where analysis runs:** most commands call the Socket API, so packages are analysed by the
  service. An API token is needed for most commands.
- **Free plan:** one developer seat, 1,000 scans a month and a limited API quota; the GitHub
  integration and Socket Firewall, which blocks known malicious packages at install, are
  included.

### What release-drift adds

- **Two releases side by side.** `release-drift diff` lists every file a release adds, removes or
  changes compared with the previous release, and every execution surface (startup hooks, build
  scripts, install scripts, native binaries) that is new or whose file changed, for wheels,
  sdists and npm tarballs alike.
- **Release against tag, every file.** `release-drift source` compares every file of the artifact,
  not only those that also exist in the repository, with the tree at a git ref, by content. Files
  that are not in the repository at all are listed. It works with any git repository the user
  has, on any host. The packaging-generated files it accepts are listed in
  [Source comparison](source-comparison.md).
- **Deterministic.** The same inputs give byte-identical output. There are no scores, no
  thresholds learned from data and no AI; each record is a fact about the files that can be
  checked by hand.
- **Offline after fetch.** `diff` and `source` read local files and run local git only. The
  optional `fetch` command is the only one that uses the network, and it only downloads.
- **Reports only.** release-drift does not decide whether a package is malicious and does not
  block installs. What to do with a record is the operator's decision.

release-drift does not replace either tool: it has no malware signatures, no typosquatting or
maintainer checks and no dependency-tree scanning. Its only registry metadata check is
`provenance`: lost provenance, a changed build workflow on npm and PyPI, and a different npm
publisher on a release without provenance.
