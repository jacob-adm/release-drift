# Findings on ordinary releases

How often release-drift reports a finding (exit status 1) for an ordinary release, measured on
23 November 2026 with release-drift 0.2.0, and re-run on the same files with the 0.3.0 rule. `benchmark/noise.py` reproduces it.

## Sample

The 100 most-downloaded PyPI packages (top-pypi-packages, 30 days to 1 November 2026) and the
100 most-downloaded npm packages (wooorm/npm-high-impact, top by downloads). For each, the highest
stable release and the stable release just below it, by version number. PyPI releases are compared
as sdists, npm releases as tarballs. The 200 pairs are pinned in `benchmark/noise-pairs.json`;
the per-pair results are in `benchmark/noise-results.json`.

## Results

199 of the 200 pairs were compared. The pyarrow sdist was refused: it holds `LICENSE.txt` twice.

| | Pairs with a finding |
| --- | --- |
| All 199 | 47 (23.6%) |
| PyPI (99) | 30 |
| npm (100) | 17 |

Pairs with at least one finding of each category (a pair can have several):

| Category | Pairs | What caused it in this sample |
| --- | --- | --- |
| `url-host` | 34 | New links, mostly in documentation and changelogs. 26 pairs had no other finding |
| `build` | 9 | `setup.py` changed, as it does in many sdist releases (for example the version number) |
| `dependency` | 9 | The release added a dependency |
| `npm-script` | 2 | A new `prepare` script (yallist 5.0.0, uuid 14.0.3) |
| `blob` | 2 | Generated data tables (entities 9.0.0) and a native build (@esbuild/linux-x64) |
| `native` | 1 | A new native binary (@esbuild/linux-x64) |
| `link` | 1 | A symbolic link in the sdist (tenacity) |

## Against the replayed attacks

In the replays of the 2026 attacks, the records that caught each attack were `blob` (LiteLLM
1.82.7, tensorlake 0.5.144), `pth` (LiteLLM 1.82.8), `npm-script` with `postinstall` (Cline 2.3.0,
plain-crypto-js 4.2.1) and `dependency` (axios 1.14.1). No attack was caught by `url-host` alone.

| Rule | Pairs with a finding | Replayed attacks still reported |
| --- | --- | --- |
| Every record but `file` is a finding (0.2.0) | 47 (23.6%) | All six |
| `url-host` is printed but not a finding (from 0.3.0) | 21 (10.6%): PyPI 15, npm 6 | All six |

The 9 `build` records were read by hand. Two `setup.py` changes are only a version pin or a
copyright line (boto3, six), two change the version and supported Python versions (pyyaml,
jmespath), two add or change build logic (charset-normalizer now runs a separate build hook file at
install, pillow), and three rewrite or empty the file (python-dateutil, sniffio, tenacity). No narrow
pattern separates harmless changes from the rest, and `setup.py` runs at install, so a change to it
stays a finding.

`prepare` stays an install script. In a test with npm 10.9.9, `npm install ./PACKAGE.tgz` and a
plain `npm install` with a folder dependency both ran a package's `prepare`; a plain `npm install`
with a tarball dependency did not. npm's documentation also names git dependencies.

## Other samples

The same measurement with release-drift 0.3.0, on three more pinned samples (benchmark/noise-pairs-*.json,
benchmark/noise-results-*.json):

| Sample | Compared | With a finding | Main categories |
| --- | --- | --- | --- |
| The same 100 PyPI packages, as wheels with the same file tag (`--kind wheel`) | 100 | 22 (22%) | `native` 18, `blob` 9, `dependency` 7 |
| The 100 PyPI and 100 npm packages, five versions apart (`--step 5`) | 200 | 67 (33.5%): PyPI 47, npm 20 | `dependency` 33, `build` 31, `blob` 10 |
| 100 PyPI and 100 npm packages ranked from 5001st (`--offset 5000`) | 198 | 49 (24.7%): PyPI 40, npm 9 | `build` 33, `dependency` 17, `native` 6 |

- **Wheels.** Compiled extensions are rebuilt for every release: of 227 `native` records, 224 are
  `changed` and 3 `added`. In 7 wheels a changed native file was the only finding.
- **Larger steps** add more of everything a single release adds, mostly new dependencies and
  `setup.py` changes.
- **Less popular packages.** On PyPI, `build` (a changed `setup.py` or build section) is the most
  common finding: 33 of 98 sdists.
- npm stays lower in every sample (6% to 20%), and no npm sample had an `npm-script` finding in more
  than 6 pairs.

## Optional informational records

`--informational native-changed,build-changed` (from 0.4.0) prints those records without setting
exit status 1. On the samples above, the share of ordinary updates with a finding:

| Sample | Default | `native-changed` | `build-changed` | Both |
| --- | --- | --- | --- | --- |
| Single step, sdists and tarballs | 10.6% | 10.6% | 8.0% | 8.0% |
| Wheels | 22.0% | 15.0% | 22.0% | 15.0% |
| Five versions apart | 33.5% | 33.5% | 28.5% | 28.5% |
| Ranked from 5001st | 24.7% | 22.7% | 12.6% | 10.6% |

On 125 attack samples (the 119 held-out 2026 releases in
[Replaying the 2026 attacks](https://github.com/jacob-adm/jacob-adm/blob/main/replays.md) and 6
hijacked PyPI releases from 2024 and 2025), no release that the default reports went unreported
with either option. Those samples contain almost no attack that works by changing `setup.py` or
replacing a compiled binary, so they cannot show what the options give up on that route. That is
why both stay findings by default.

## Provenance records

`release-drift provenance` (from 0.6.0) on the same 200 pinned pairs, measured on 26 November 2026
(`benchmark/provenance.py`, per-pair results in `benchmark/provenance-results.json`). It reads
registry metadata only, so all 200 pairs were compared.

| | Newer release attested | Pairs with a record | Records |
| --- | --- | --- | --- |
| PyPI (100) | 63 | 1 | `workflow-changed` 1: pluggy 1.7.0 moved its release job from `main.yml` to `deploy.yml` |
| npm (100) | 26 | 5 | `publisher-changed` 5: tslib, source-map, convert-source-map, is-glob, braces, each published by a different maintainer account without provenance |

No pair had a `removed` record. Four more npm pairs changed account when the project moved to
trusted publishing (yargs, path-to-regexp, webidl-conversions, tr46: from a maintainer to GitHub
Actions, with provenance). Those give no record, because a release with provenance is judged by
its workflow, not its account. `--informational publisher-changed` prints the account changes
without setting exit status 1.

The malicious axios and Cline releases are no longer on npm, so their registry records could not
be read and are not part of this measurement. The axios release before the incident, 1.14.0, was
published by GitHub Actions with provenance from `axios/axios` `.github/workflows/publish.yml`. The
tests use that record with a following release that has none.

## Limits

- One release step per package, of popular packages only. Smaller packages and longer steps (for
  example several versions at once in a lockfile update) may differ.
- A pair with a finding is not a false alarm in every case: a new dependency or install script is
  worth a look even when it is legitimate. The numbers show how often a required check would stop
  an ordinary update for a person to look at it.
