# Other tools

Two widely used free tools look at the same problem: GuardDog and Socket. This page states what
each covers, as documented by its project in November 2026, and what release-drift does
differently. Check each project's own documentation for its current state.

## GuardDog

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

## Socket

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

## What release-drift adds

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

release-drift does not replace either tool: it has no malware signatures, no registry metadata
checks (maintainers, provenance, typosquatting) and no dependency-tree scanning.
