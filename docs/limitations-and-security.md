# Limitations and security

## What release-drift reads and writes

- `diff` and `source` read the artifact files given on the command line into memory. Nothing from
  an artifact is written to disk, unpacked, imported or run.
- `source` runs `git rev-parse`, `git ls-tree` and `git cat-file` in the repository given with
  `--repo`. It does not change the repository.
- `fetch` connects to `pypi.org`, `registry.npmjs.org` and the download host the registry names,
  over HTTPS only, and writes the downloaded files into the directory given. It never installs,
  unpacks or runs them.

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
  of other ecosystems are not covered.
- URL hosts are found only in plain text: a URL built at run time, encoded or compressed is not
  seen. Blobs shorter than 200 characters are not reported.
- `diff` does not report surfaces that a release removes.
- `source` maps artifact paths to repository paths by a fixed prefix. Packages whose files come
  from several directories (for example a wheel's `.data/` directories, or a tree rearranged by
  the build) show those files as `not-in-tag`. Line-ending conversion at build time shows files
  as `differs`.
- It does not check signatures, provenance attestations, maintainers or registry metadata.

## Determinism

Output depends only on the bytes of the inputs, and, for `source`, on the git objects of the ref.
It does not depend on time, locale, environment or the order of members in the archive.
