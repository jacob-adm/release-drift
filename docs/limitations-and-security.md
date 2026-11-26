# Limitations and security

## What release-drift reads and writes

- `diff` and `source` read the artifact files given on the command line into memory. Nothing from
  an artifact is written to disk, unpacked, imported or run. A directory given as an artifact is
  read the same way: regular files only, under the same size limits, without following links.
- `source` runs `git rev-parse`, `git ls-tree` and `git cat-file` in the repository given with
  `--repo`. It does not change the repository.
- `fetch` connects to `pypi.org`, `registry.npmjs.org` and the download host the registry names,
  over HTTPS only, and writes the downloaded files into the directory given. It never installs,
  unpacks or runs them.
- `provenance`, and `lockfile` unless `--no-provenance` is given, read version metadata, the
  JSON simple index and provenance attestations from `pypi.org` and `registry.npmjs.org` over
  HTTPS. They write nothing.

## Hostile artifacts

Every artifact is treated as hostile. An artifact is refused (exit 2) when it has:

- a member name that is absolute (`/x`, `C:x`), contains `..`, a backslash or a NUL byte;
- a symbolic or hard link whose target is absolute or leads outside the artifact;
- a device, FIFO or other special member;
- two members with the same path;
- more than 50,000 members, a member larger than 64 MiB, or more than 512 MiB in total once
  decompressed. Sizes are counted while reading, not taken from the archive's headers.

Links that stay inside the artifact are kept as links, with their targets, and never followed;
`diff` reports new ones as `link` records.

The Python `tarfile` and `zipfile` modules parse the archive headers. A tar header with a very
large extended (pax) header is read into memory by `tarfile` before the caps above apply.

Run release-drift on artifacts you do not trust with the same care as any other parser of
untrusted input: as an unprivileged user, and with Python's isolated mode (`python3 -I`) when
the current directory holds downloaded files.

## What it does not detect

release-drift lists facts about files. It does not decide whether a package is malicious.

- It does not read code: a changed `install.js` that a `postinstall` script runs is a
  `file changed` record, not an `npm-script` record. Script commands are not followed.
- Execution surfaces are the ones listed in [Commands](commands.md#categories). Code that runs on
  `import` of an ordinary module, entry points and console scripts, and install-time behaviour
  of other ecosystems are not covered. This is the gap real attacks fall through: in a test on 119
  hijacked releases from 2026 that the tool had not been tuned on, all 5 misses were PyPI wheels
  whose payload is ordinary Python added to a package's `__init__.py` (see
  [Replaying the 2026 attacks](https://github.com/jacob-adm/jacob-adm/blob/main/replays.md)). A
  rule that reports an `__init__.py` newly importing `subprocess`, `socket`, `ctypes`,
  `urllib.request`, `http.client`, `requests` or `httpx` was tested and not adopted: on 6 other
  hijacked PyPI releases (2024 to 2025) it caught nothing release-drift missed, and on new packages
  it fired for 34 of 100 malicious ones and 12 of 100 ordinary ones.
- URL hosts are found only in plain text: a URL built at run time, encoded or compressed is not
  seen. Blobs shorter than 200 characters are not reported.
- Files are read as UTF-8: a host in another encoding ends at its first byte that is not
  UTF-8. A host written in punycode (`xn--`) and the same host written in Unicode count as two
  hosts.
- `diff` does not report surfaces that a release removes.
- `source` maps artifact paths to repository paths by a fixed prefix. Packages whose files come
  from several directories (for example a wheel's `.data/` directories, or a tree rearranged by
  the build) show those files as `not-in-tag`. Line-ending conversion at build time shows files
  as `differs`.
- `lockfile` compares only packages whose version changed. A package that a lockfile change adds
  is listed, not compared, and does not set exit status 1. Packages from other registries or
  indexes than registry.npmjs.org and pypi.org are listed as `skipped`.
- It does not check signatures itself. `provenance` reads whether a release has a provenance
  attestation and which workflow it names, as the registry records them; the registry checked the
  signature at publication.
- `provenance` cannot tell a stolen account from a maintainer handover, and a release built by
  the project's own workflow after its CI was compromised has the same provenance as an honest
  one. It reports only a change between the two versions compared, so a package that never had
  provenance gives no `removed` record.
- PyPI: the workflow is read from one attested file of each version; files of the same version
  attested by different workflows are not compared with each other.

## Determinism

Output depends only on the bytes of the inputs, and, for `source`, on the git objects of the ref.
It does not depend on time, locale, environment or the order of members in the archive.
