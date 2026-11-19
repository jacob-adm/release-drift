# Security

`release-drift` treats every release artifact as hostile input. `diff` and `source` read artifacts
into memory and never unpack, import, install or run anything from them, and refuse archives with
unsafe member names, links that leave the artifact, special members or oversized contents.
`source` runs read-only git commands in the repository you name. `fetch` downloads over HTTPS only
and saves the files to the directory you give, without installing, unpacking or running them.
Archive headers are parsed by Python's `tarfile` and `zipfile`, so run it on untrusted artifacts as
an unprivileged user. See [Limitations and security](docs/limitations-and-security.md).

## Reporting a vulnerability

Please report security problems privately, through GitHub's "Report a vulnerability" button on the
repository's Security tab. Please do not open a public issue. You can expect a reply within a week.
