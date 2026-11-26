# SPDX-License-Identifier: Apache-2.0
"""Command line: release-drift diff | source | lockfile | fetch."""

import argparse
import json
import sys
import tempfile

from . import __version__
from .archive import ArtifactError, read_artifact
from .compare import OPTIONAL_INFORMATIONAL, CompareError, diff, is_finding, source
from .fetch import FetchError, fetch
from .lockfile import LockfileError, artifact_pair, changes, read_lockfile

_ESCAPES = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


def escape(text):
    """Make a field safe for one TAB-separated line."""
    out = []
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


FIELDS = ("category", "status", "path", "detail")
LOCK_FIELDS = ("name", "old", "new") + FIELDS


def render(records, as_json, fields=FIELDS):
    """Bytes to print: sorted, the same for the same records."""
    if as_json:
        rows = [dict(zip(fields, r)) for r in sorted(records)]
        text = json.dumps(rows, indent=1, sort_keys=True, ensure_ascii=True) + "\n"
        return text.encode("ascii")
    lines = ["\t".join(escape(f) for f in r).encode("utf-8", "surrogateescape") for r in records]
    return b"".join(line + b"\n" for line in sorted(lines))


def _parser():
    p = argparse.ArgumentParser(
        prog="release-drift",
        description="Report what changed between two releases of a package, what new code "
                    "runs at install or startup, and which files are not in the tagged source.")
    p.add_argument("--version", action="version", version="release-drift " + __version__)
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    d = sub.add_parser("diff", help="compare two artifacts of a package")
    d.add_argument("old", metavar="OLD")
    d.add_argument("new", metavar="NEW")

    s = sub.add_parser("source", help="files of an artifact that are not in git at a ref")
    s.add_argument("artifact", metavar="ARTIFACT")
    s.add_argument("--repo", required=True, metavar="DIR")
    s.add_argument("--ref", required=True, metavar="REF")
    s.add_argument("--prefix", metavar="PATH",
                   help="directory of the repository the artifact's root maps to")

    k = sub.add_parser("lockfile", help="compare every package a lockfile change upgrades")
    k.add_argument("old", metavar="OLD")
    k.add_argument("new", metavar="NEW")
    k.add_argument("--type", choices=("sdist", "wheel"), dest="kind", default="sdist",
                   help="PyPI only: compare sdists (default) or wheels with the same file tag")
    k.add_argument("--cache", metavar="DIR",
                   help="keep downloads in DIR and reuse files already there (default: a temporary "
                        "directory, removed afterwards)")

    for cmd in (d, s, k):
        cmd.add_argument("--json", action="store_true", help="print a JSON array")
    for cmd in (d, k):
        cmd.add_argument("--informational", metavar="LIST", default="",
                         help="comma-separated records to print without setting exit status 1: "
                              + ", ".join(sorted(OPTIONAL_INFORMATIONAL)))

    f = sub.add_parser("fetch", help="download one version from PyPI or npm (no install)")
    f.add_argument("registry", choices=("pypi", "npm"))
    f.add_argument("name", metavar="NAME")
    f.add_argument("version", metavar="VERSION")
    f.add_argument("directory", metavar="DIR")
    f.add_argument("--type", choices=("sdist", "wheel", "all"), dest="kind", default="sdist",
                   help="PyPI only: the sdist (default), the wheels, or every file")
    return p


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    quiet = set()
    for name in filter(None, getattr(args, "informational", "").split(",")):
        if name.strip() not in OPTIONAL_INFORMATIONAL:
            parser.error("--informational: unknown value %r (choose from %s)"
                         % (name.strip(), ", ".join(sorted(OPTIONAL_INFORMATIONAL))))
        quiet.add(OPTIONAL_INFORMATIONAL[name.strip()])
    out = sys.stdout.buffer
    if args.command == "lockfile":
        return run_lockfile(args, quiet, out)
    try:
        if args.command == "fetch":
            for path in fetch(args.registry, args.name, args.version, args.directory, args.kind):
                out.write(escape(path).encode("utf-8", "surrogateescape") + b"\n")
            return 0
        if args.command == "diff":
            records = diff(read_artifact(args.old), read_artifact(args.new))
        else:
            records = source(read_artifact(args.artifact), args.repo, args.ref, args.prefix)
    except (ArtifactError, CompareError, FetchError, OSError) as exc:
        sys.stderr.write("release-drift: error: %s\n" % exc)
        return 2
    out.write(render(records, args.json))
    out.flush()
    return 1 if any(is_finding(args.command, r, quiet) for r in records) else 0


def run_lockfile(args, quiet, out):
    """Diff every upgraded package. Exit 2 if any package could not be compared,
    else 1 if any has a finding under the diff rules, else 0."""
    try:
        old_reg, old, _ = read_lockfile(args.old)
        new_reg, new, skipped = read_lockfile(args.new)
    except LockfileError as exc:
        sys.stderr.write("release-drift: error: %s\n" % exc)
        return 2
    if old_reg != new_reg:
        sys.stderr.write("release-drift: error: OLD is a %s lockfile and NEW a %s one\n" % (old_reg, new_reg))
        return 2
    upgraded, added, removed = changes(old, new)
    records, failed, found = set(), False, False
    with tempfile.TemporaryDirectory(prefix="release-drift-") as tmp:
        cache = args.cache or tmp
        for name, before, after in upgraded:
            try:
                paths = artifact_pair(new_reg, name, before, after, cache, args.kind)
                pkg = diff(read_artifact(paths[0]), read_artifact(paths[1]))
            except (ArtifactError, CompareError, FetchError, OSError) as exc:
                records.add((name, before, after, "package", "error", "-", str(exc)))
                failed = True
                continue
            records.update((name, before, after) + r for r in pkg)
            found = found or any(is_finding("diff", r, quiet) for r in pkg)
            if not pkg:
                records.add((name, before, after, "package", "unchanged", "-", "-"))
    records.update((n, "-", v, "package", "added", "-", "not compared") for n, v in added)
    records.update((n, v, "-", "package", "removed", "-", "-") for n, v in removed)
    records.update((n, "-", "-", "package", "skipped", "-", reason) for n, reason in skipped)
    out.write(render(records, args.json, LOCK_FIELDS))
    out.flush()
    return 2 if failed else 1 if found else 0
