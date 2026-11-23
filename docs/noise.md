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

`prepare` stays an install script. In a test with npm 10.9.9, `npm install ./PACKAGE.tgz` and a
plain `npm install` with a folder dependency both ran a package's `prepare`; a plain `npm install`
with a tarball dependency did not. npm's documentation also names git dependencies.

## Limits

- One release step per package, of popular packages only. Smaller packages and longer steps (for
  example several versions at once in a lockfile update) may differ.
- A pair with a finding is not a false alarm in every case: a new dependency or install script is
  worth a look even when it is legitimate. The numbers show how often a required check would stop
  an ordinary update for a person to look at it.
