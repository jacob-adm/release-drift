# SPDX-License-Identifier: Apache-2.0
"""The two comparisons: release against release, and release against git."""

import configparser
import hashlib
import json
import posixpath
import re
import subprocess

from .archive import NPM, SDIST, WHEEL
from .scan import strings, surfaces

_VERSIONED_DIR = re.compile(r"^(.+?)-[^-/]+\.(dist-info|data)$")

WHEEL_GENERATED = frozenset((
    "METADATA", "RECORD", "WHEEL", "entry_points.txt", "top_level.txt",
    "zip-safe", "namespace_packages.txt", "dependency_links.txt",
))
EGG_INFO_GENERATED = frozenset((
    "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "requires.txt", "top_level.txt",
    "entry_points.txt", "not-zip-safe", "zip-safe", "namespace_packages.txt",
))
LICENSE_PREFIXES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")


class CompareError(Exception):
    pass


def normalize(path):
    """Drop the version from a wheel's NAME-VERSION.dist-info and .data directories,
    so the same file lines up across two versions."""
    top, sep, rest = path.partition("/")
    match = _VERSIONED_DIR.match(top)
    if match:
        top = "%s.%s" % (match.group(1), match.group(2))
    return top + sep + rest


def _keyed(artifact):
    return {normalize(p): p for p in artifact.files}, {normalize(p): p for p in artifact.links}


def diff(old, new):
    """Records (category, status, path, detail) describing what NEW adds over OLD."""
    records = []
    old_files, old_links = _keyed(old)
    new_files, new_links = _keyed(new)
    old_content = {k: old.files[p] for k, p in old_files.items()}
    old_content.update({k: old.links[p] for k, p in old_links.items()})
    new_content = {k: new.files[p] for k, p in new_files.items()}
    new_content.update({k: new.links[p] for k, p in new_links.items()})
    new_paths = dict(new_files, **new_links)
    old_paths = dict(old_files, **old_links)

    for key, path in new_paths.items():
        if key not in old_content:
            records.append(("file", "added", path, "-"))
        elif old_content[key] != new_content[key]:
            records.append(("file", "changed", path, "-"))
    for key, path in old_paths.items():
        if key not in new_content:
            records.append(("file", "removed", path, "-"))

    old_surfaces = {(cat, normalize(path), detail) for cat, path, detail, _ in surfaces(old)}
    for cat, path, detail, content_sensitive in surfaces(new):
        key = normalize(path)
        if (cat, key, detail) not in old_surfaces:
            records.append((cat, "added", path, detail))
        elif content_sensitive and old_content.get(key) != new_content.get(key):
            records.append((cat, "changed", path, detail))

    old_strings = {(cat, detail) for cat, _, detail in strings(old)}
    for cat, path, detail in strings(new):
        if (cat, detail) not in old_strings:
            records.append((cat, "added", path, detail))
    return sorted(set(records))


# Records diff prints but that do not set exit status 1. A new URL host is common in
# ordinary releases (links in documentation and changelogs, docs/noise.md) and is
# reported for a reader to see; it is not a finding on its own.
INFORMATIONAL = ("file", "url-host")


def is_finding(command, record):
    """A finding sets exit status 1. For diff, every record except a plain file
    record or a new URL host; for source, every record, since each names a file
    not in the tag."""
    return command == "source" or record[0] not in INFORMATIONAL


# --- release against git ---------------------------------------------------

def _git(repo, *args):
    try:
        proc = subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    except OSError as exc:
        raise CompareError("cannot run git: %s" % exc)
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()
        raise CompareError("git %s failed: %s" % (args[0], msg))
    return proc.stdout


def read_tree(repo, ref):
    """Map each path in the tree of REF to (mode, object id), and return the
    hash function git uses for object ids."""
    if not ref or ref.startswith("-"):
        raise CompareError("invalid ref %r" % ref)
    _git(repo, "rev-parse", "--verify", "--quiet", ref + "^{tree}")
    try:
        fmt = _git(repo, "rev-parse", "--show-object-format").decode().strip()
    except CompareError:
        fmt = "sha1"
    if fmt not in ("sha1", "sha256"):
        raise CompareError("unknown git object format %r" % fmt)
    out = _git(repo, "ls-tree", "-r", "-z", "--full-tree", ref + "^{tree}")
    tree = {}
    for entry in out.split(b"\x00"):
        if not entry:
            continue
        meta, path = entry.split(b"\t", 1)
        mode, _type, oid = meta.decode().split(" ")
        tree[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return tree, fmt


def blob_id(data, fmt):
    h = hashlib.new(fmt)
    h.update(b"blob %d\x00" % len(data))
    h.update(data)
    return h.hexdigest()


def _blob(repo, oid):
    return _git(repo, "cat-file", "blob", oid)


def generated(kind, path):
    """True for a file the packaging tool writes and git never holds."""
    parts = path.split("/")
    if kind == WHEEL:
        return len(parts) == 2 and parts[0].endswith(".dist-info") and parts[1] in WHEEL_GENERATED
    if kind == SDIST:
        if path == "PKG-INFO":
            return True
        return len(parts) >= 2 and parts[-2].endswith(".egg-info") and parts[-1] in EGG_INFO_GENERATED
    return False


def _license_copy(kind, path):
    """A licence file copied into a wheel's .dist-info directory."""
    parts = path.split("/")
    if kind != WHEEL or not parts[0].endswith(".dist-info") or len(parts) < 2:
        return False
    return parts[1] == "licenses" or parts[1].upper().startswith(LICENSE_PREFIXES)


def setup_cfg(data):
    """setup.cfg as {section: {key: value}}, without the [egg_info] section.
    setuptools rewrites setup.cfg in the sdist with configparser: it adds
    [egg_info] and respaces 'key=value' lines, and drops comments."""
    parser = configparser.RawConfigParser(strict=True)
    parser.optionxform = str
    try:
        parser.read_string(data.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error):
        return None
    return {name: dict(parser.items(name)) for name in parser.sections() if name != "egg_info"}


def npm_manifest(data):
    """package.json with the fields npm adds or drops at publish removed."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    return {k: v for k, v in obj.items() if k != "gitHead" and not k.startswith("_")}


def _same_after_normalising(repo, kind, path, data, entry):
    """path is relative to the artifact; entry is the tree entry it maps to."""
    if entry is None:
        return kind == SDIST and path == "setup.cfg" and setup_cfg(data) == {}
    if kind == SDIST and path == "setup.cfg":
        mine = setup_cfg(data)
        return mine is not None and mine == setup_cfg(_blob(repo, entry[1]))
    if kind == NPM and path == "package.json":
        mine = npm_manifest(data)
        return mine is not None and mine == npm_manifest(_blob(repo, entry[1]))
    return False


MAX_LINK_HOPS = 40


def _lookup(repo, tree, path):
    """The tree entry for path, following directory symlinks in the tree the
    way a checkout would (a build copies the files through them). Links that
    leave the repository are not followed."""
    for _ in range(MAX_LINK_HOPS):
        entry = tree.get(path)
        if entry is not None:
            return entry
        parts = path.split("/")
        for i in range(1, len(parts)):
            head = "/".join(parts[:i])
            link = tree.get(head)
            if link is not None and link[0] == "120000":
                target = _blob(repo, link[1]).decode("utf-8", "surrogateescape")
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(head), target))
                if target.startswith("/") or resolved == ".." or resolved.startswith("../"):
                    return None
                path = resolved + "/" + "/".join(parts[i:])
                break
        else:
            return None
    return None


def _status(repo, tree, fmt, prefix, path, data, is_link):
    entry = _lookup(repo, tree, prefix + path)
    if entry is None:
        return "not-in-tag", None
    if (entry[0] == "120000") != is_link:
        return "differs", entry
    if blob_id(data, fmt) != entry[1]:
        return "differs", entry
    return None, entry


def pick_prefix(artifact, tree, fmt):
    """For a wheel, the directory of the repository its paths start in: the
    root or src/, whichever holds more identical files (the root on a tie)."""
    if artifact.kind != WHEEL:
        return ""
    best, best_count = "", -1
    for prefix in ("", "src/"):
        count = sum(1 for p, d in artifact.files.items()
                    if tree.get(prefix + p, ("", ""))[1] == blob_id(d, fmt))
        if count > best_count:
            best, best_count = prefix, count
    return best


def source(artifact, repo, ref, prefix=None):
    """Records for files of the artifact that are not, as they are, in REF."""
    tree, fmt = read_tree(repo, ref)
    if prefix is None:
        prefix = pick_prefix(artifact, tree, fmt)
    elif prefix and not prefix.endswith("/"):
        prefix += "/"
    tree_blobs = {oid for mode, oid in tree.values() if mode != "160000"}
    flagged = {}
    items = [(p, d, False) for p, d in artifact.files.items()]
    items += [(p, t.encode("utf-8", "surrogateescape"), True) for p, t in artifact.links.items()]
    for path, data, is_link in items:
        if not is_link and generated(artifact.kind, path):
            continue
        if not is_link and _license_copy(artifact.kind, path) and blob_id(data, fmt) in tree_blobs:
            continue
        status, entry = _status(repo, tree, fmt, prefix, path, data, is_link)
        if status is None:
            continue
        if not is_link and _same_after_normalising(repo, artifact.kind, path, data, entry):
            continue
        flagged[path] = status

    records = [("file", status, path, "-") for path, status in flagged.items()]
    for cat, path, detail, _ in surfaces(artifact):
        if path in flagged:
            records.append((cat, flagged[path], path, detail))
    for cat, path, detail in strings(artifact):
        if path in flagged:
            records.append((cat, flagged[path], path, detail))
    return sorted(set(records))
