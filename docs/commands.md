# Commands

```
release-drift diff OLD NEW [--json]
release-drift source ARTIFACT --repo DIR --ref REF [--prefix PATH] [--json]
release-drift fetch {pypi,npm} NAME VERSION DIR [--type {sdist,wheel,all}]
release-drift --version
```

Artifacts are local files: a wheel (`.whl`), an sdist (`.tar.gz`, `.zip` or another tar
format) or an npm tarball (`.tgz`). The kind is taken from the file: `.whl` is a wheel; a `.tgz`
with a `package.json` at its root is an npm tarball; a tar or zip with `PKG-INFO`, `setup.py` or
`pyproject.toml` at its root is an sdist; any other tar or zip with a `package.json` is an npm
tarball, and anything else is treated as an sdist. The top directory of an sdist or npm tarball
(`name-1.0/`, `package/`) is removed from every path.

## Output

One record per line, four TAB-separated fields:

```
CATEGORY	STATUS	PATH	DETAIL
```

Lines are sorted by their bytes (the order of `LC_ALL=C sort`) and repeated lines are dropped,
so the same inputs always give byte-identical output. In PATH and DETAIL, a backslash is written
`\\`, TAB `\t`, newline `\n`, carriage return `\r` and any other control character `\xHH`. A
DETAIL of `-` means there is none.

With `--json` the same records are printed as a JSON array of objects with the keys `category`,
`status`, `path` and `detail`, in the same order, ASCII only, without the escaping above.

To see only the findings of `diff`, leave out the `file` records: `grep -v '^file'`.

### Categories

| Category | Where | DETAIL |
| --- | --- | --- |
| `file` | every artifact | `-` |
| `pth` | any `*.pth` file | `import` if a line starts with `import ` or `import<TAB>` (Python's `site` module runs those lines at every interpreter start when the file is in site-packages), otherwise `paths` |
| `startup` | any `sitecustomize.py` or `usercustomize.py` | the file name; Python imports these at start-up when they are on the path |
| `build` | sdist: `setup.py` or `hatch_build.py` at the root | the file name |
| `build` | sdist: root `pyproject.toml` | the `build-backend` and `backend-path` lines of `[build-system]`; hook tables under `[tool.hatch.build]`; `script`, `setup-script` and `build` keys of `[tool.hatch.build]`, `[tool.poetry.build]` and `[tool.pdm.build]` |
| `npm-script` | npm: the root `package.json` and those under `node_modules/` (bundled dependencies) | `NAME=COMMAND` for each of `preinstall`, `install`, `postinstall`, `prepare`; `package.json does not parse` if it is not valid JSON |
| `npm-script` | npm: `binding.gyp` at the root | `install=node-gyp rebuild (implicit)`: npm runs this when a package has `binding.gyp` and no `install` or `preinstall` script |
| `pyc-only` | any `*.pyc` whose `.py` source is not in the artifact | `no .py source` |
| `native` | any file starting with an ELF, Mach-O, PE or WebAssembly header | `ELF`, `Mach-O`, `PE` or `WebAssembly` |
| `link` | a symbolic or hard link member | its target |
| `url-host` | any file containing `http://`, `https://`, `ws://`, `wss://` or `ftp://` | the host (and port), lower-cased |
| `blob` | any run of 200 or more characters from `A-Za-z0-9+/_-` (plus `=` padding) | `base64` or `hex`, its length and the first 16 hex digits of its SHA-256 |

### Exit status

| Status | Meaning |
| --- | --- |
| 0 | no findings; `diff` may still have printed `file` records |
| 1 | one or more findings |
| 2 | error: an unreadable or unsafe artifact, a git failure, a bad argument or a failed download |

A finding is any record of `diff` other than a plain `file` record: a new or changed execution
surface, a new URL host or a new blob. Every record of `source` is a finding, because each names a
file of the artifact that is not, as it is, in the tag. So `diff` between two releases that only
change ordinary files prints those files and exits 0.

On exit 2 nothing is printed to standard output and the reason is on standard error, starting
with `release-drift: error:`.

## diff

`release-drift diff OLD NEW` reports what NEW adds over OLD.

- `file` records: STATUS is `added`, `removed` or `changed` (same path, different bytes). A wheel's
  `NAME-VERSION.dist-info/` and `NAME-VERSION.data/` directories are matched without their version,
  so `six-1.16.0.dist-info/RECORD` and `six-1.17.0.dist-info/RECORD` are the same file.
- Execution surfaces (`pth`, `startup`, `build`, `npm-script`, `pyc-only`, `native`, `link`):
  `added` when NEW has a surface with a path and DETAIL that OLD does not have; `changed` when the
  surface is in both but its file's bytes differ (for `pth`, `startup`, `build` scripts,
  `pyc-only` and `native`). `npm-script` and `pyproject.toml` records are compared by DETAIL only,
  because `package.json` and `pyproject.toml` change in every release.
- `url-host` and `blob`: `added` when the host or blob does not appear anywhere in OLD; one record
  for each file of NEW it appears in.
- Surfaces that NEW drops are not reported, apart from the `file removed` record.

Two identical artifacts give no output. Releases that differ only in ordinary files give `file`
records and exit 0.

## source

`release-drift source ARTIFACT --repo DIR --ref REF` reports the files of ARTIFACT that are not,
as they are, in the tree of REF in the git repository at DIR.

- Each file maps to `PREFIX/PATH` in the tree. `--prefix PATH` sets the directory; without it,
  sdists and npm tarballs map to the repository root, and wheels map to the root or to `src/`,
  whichever holds more identical files (the root on a tie).
- STATUS is `not-in-tag` (no file at that path) or `differs` (a file, but different bytes, or a
  link where the repository has a file or the reverse). Contents are compared by git blob id, so
  the comparison works with SHA-1 and SHA-256 repositories.
- For each reported file, its execution surfaces, URL hosts and blobs are reported with the same
  STATUS.
- Files that packaging tools generate are not reported; [Source comparison](source-comparison.md)
  lists them and why each is accepted.
- Files in the repository that are missing from the artifact are not reported.

REF must not start with `-`. It can be any name `git rev-parse` accepts: a tag, branch or commit.

## fetch

`release-drift fetch pypi NAME VERSION DIR` and `release-drift fetch npm NAME VERSION DIR`
download one published version into an existing directory and print the saved paths, sorted.

- PyPI: the files of the version listed by `https://pypi.org/pypi/NAME/VERSION/json`. By default
  only the sdist; `--type wheel` downloads every wheel, `--type all` every file. Each file's SHA-256
  is checked against the digest in that response. If the version has no file of the chosen type
  (for example a release published as wheels only), nothing is downloaded and the command exits 2
  with an error naming `--type`.
- npm: `--type` is ignored; a version has one tarball.
- npm: the tarball named in `https://registry.npmjs.org/NAME/VERSION`. Its SHA-512 is checked
  against the `dist.integrity` value. npm publishes no SHA-256; a version without a SHA-512
  integrity value is refused. A scoped package `@scope/name` is saved as
  `scope-name-VERSION.tgz`.
- Only `https://` URLs are used, including redirects. Metadata is capped at 32 MiB and each file at
  512 MiB.
- A file that already exists is left alone if its bytes equal the download, otherwise the command
  fails. New files are written to a temporary name and renamed when complete.
- Nothing is installed, unpacked or run.
