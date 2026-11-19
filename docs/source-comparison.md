# Source comparison

`release-drift source` lists the files of a release that are not, byte for byte, in the git tree
it is said to come from. Building a package writes some files that are never in git, and
rewrites a few others. This page lists every such file release-drift accepts, and why. Anything
not listed here is reported.

## Accepted without comparison

These are written by the build tool from the project's configuration; git does not hold them.

| Artifact | Path | Written by |
| --- | --- | --- |
| wheel | `NAME-VERSION.dist-info/METADATA`, `RECORD`, `WHEEL` | every wheel builder ([wheel format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)) |
| wheel | `NAME-VERSION.dist-info/entry_points.txt`, `top_level.txt`, `zip-safe`, `namespace_packages.txt`, `dependency_links.txt` | setuptools and other builders, from the project's configuration |
| sdist | `PKG-INFO` at the root | every sdist builder ([sdist format](https://packaging.python.org/en/latest/specifications/source-distribution-format/)) |
| sdist | `PKG-INFO`, `SOURCES.txt`, `dependency_links.txt`, `requires.txt`, `top_level.txt`, `entry_points.txt`, `not-zip-safe`, `zip-safe`, `namespace_packages.txt` inside any `*.egg-info/` directory | setuptools |

Only the file names above are accepted: any other file inside `.dist-info/` or `.egg-info/` (for
example a `RECORD.jws` signature, or a `.pth`) is reported.

## Accepted after comparison

| Artifact | Path | Accepted when | Why |
| --- | --- | --- | --- |
| wheel | `NAME-VERSION.dist-info/licenses/*`, and files directly in `.dist-info/` whose name starts with `LICENSE`, `LICENCE`, `COPYING`, `NOTICE` or `AUTHORS` | its bytes equal some file anywhere in the tree | builders copy licence files there from wherever the project keeps them |
| sdist | `setup.cfg` at the root | both files parse as INI and are equal section by section and key by key once the sdist's `[egg_info]` section is removed; or the repository has no `setup.cfg` and the sdist's holds only `[egg_info]` | setuptools rewrites `setup.cfg` with Python's `configparser`: it adds `[egg_info]`, writes `key = value` for `key=value` and drops comments |
| npm | `package.json` at the root | both parse as JSON objects and are equal once the top-level `gitHead` key and every top-level key starting with `_` are removed from each | npm has added `gitHead` and `_`-prefixed fields such as `_id` at publish; key order and whitespace do not matter to npm |

## Reported although often expected

These are commonly generated, but they contain code or data that a reviewer may want to see, so
release-drift reports them:

- a `setup.py` that a build backend writes into an sdist (some versions of poetry-core do);
- version files written by tools such as setuptools-scm (`_version.py`);
- compiled or bundled JavaScript (`dist/`) that is built at publish time and not committed;
- `package.json` changes made by other publishers, such as `workspace:` versions rewritten by pnpm
  or yarn, or fields replaced from `publishConfig`.

A release built from a different commit than the tag will show every file that changed in
between as `differs`.
