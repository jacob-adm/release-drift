# SPDX-License-Identifier: Apache-2.0
"""Read a package artifact into memory without extracting it.

Every artifact is treated as hostile: member names are checked before use,
sizes are capped while reading (header sizes are not trusted), and nothing is
written to disk.
"""

import posixpath
import tarfile
import zipfile

MAX_MEMBERS = 50000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024

WHEEL = "wheel"
SDIST = "sdist"
NPM = "npm"


class ArtifactError(Exception):
    """The artifact cannot be read safely; the message names the reason."""


class Artifact:
    """files maps each member path (top directory removed for sdists and npm
    tarballs) to its bytes; links maps link members to their targets."""

    def __init__(self, path, kind, files, links):
        self.path = path
        self.kind = kind
        self.files = files
        self.links = links


def check_name(name):
    """Return the member name as a relative POSIX path, or raise ArtifactError."""
    if not name or "\x00" in name or "\\" in name:
        raise ArtifactError("unsafe member name %r" % name)
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ArtifactError("absolute member name %r" % name)
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ArtifactError("unsafe member name %r" % name)
    return "/".join(parts)


def _link_target_ok(name, target):
    """A link must stay inside the artifact: relative, and never above its root."""
    if not target or target.startswith("/") or "\\" in target or "\x00" in target:
        return False
    if len(target) > 1 and target[1] == ":":
        return False
    depth = name.count("/")  # directories the link sits in
    for part in target.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        elif part not in ("", "."):
            depth += 1
    return True


class _Reader:
    def __init__(self, limits):
        self.max_members, self.max_member, self.max_total = limits
        self.total = 0
        self.count = 0
        self.files = {}
        self.links = {}

    def add(self, name, read):
        self.count += 1
        if self.count > self.max_members:
            raise ArtifactError("more than %d members" % self.max_members)
        name = check_name(name)
        if name in self.files or name in self.links:
            raise ArtifactError("duplicate member %r" % name)
        data = read(self.max_member + 1)
        if len(data) > self.max_member:
            raise ArtifactError("member %r is larger than %d bytes" % (name, self.max_member))
        self.total += len(data)
        if self.total > self.max_total:
            raise ArtifactError("artifact is larger than %d bytes uncompressed" % self.max_total)
        self.files[name] = data

    def add_link(self, name, target):
        self.count += 1
        if self.count > self.max_members:
            raise ArtifactError("more than %d members" % self.max_members)
        name = check_name(name)
        if name in self.files or name in self.links:
            raise ArtifactError("duplicate member %r" % name)
        if not _link_target_ok(name, target):
            raise ArtifactError("link %r points outside the artifact (%r)" % (name, target))
        self.links[name] = target


def _read_zip(path, reader):
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                check_name(info.filename)
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if info.create_system == 3 and mode == 0o120000:  # Unix symlink
                with zf.open(info) as fh:
                    target = fh.read(4097)
                reader.add_link(info.filename, target.decode("utf-8", "surrogateescape"))
                continue
            with zf.open(info) as fh:
                reader.add(info.filename, fh.read)


def _read_tar(path, reader):
    with tarfile.open(path, "r:*") as tf:
        while True:
            member = tf.next()
            if member is None:
                break
            if member.isdir():
                check_name(member.name)
            elif member.isfile():
                fh = tf.extractfile(member)
                reader.add(member.name, fh.read)
            elif member.issym():
                reader.add_link(member.name, member.linkname)
            elif member.islnk():
                # A hard link names another member by its full path in the archive.
                target = check_name(member.linkname)
                name = check_name(member.name)
                rel = posixpath.relpath(target, posixpath.dirname(name) or ".")
                reader.add_link(member.name, rel)
            else:
                raise ArtifactError("member %r is a device, FIFO or other special file" % member.name)


def _strip_top(names_files, names_links):
    """sdists and npm tarballs keep everything under one top directory."""
    tops = {n.split("/", 1)[0] for n in list(names_files) + list(names_links)}
    if len(tops) != 1:
        raise ArtifactError("expected one top-level directory, found %d" % len(tops))
    if any("/" not in n for n in list(names_files) + list(names_links)):
        raise ArtifactError("expected one top-level directory, found a top-level file")
    files = {n.split("/", 1)[1]: d for n, d in names_files.items()}
    links = {n.split("/", 1)[1]: t for n, t in names_links.items()}
    return files, links


def read_artifact(path, limits=(MAX_MEMBERS, MAX_MEMBER_BYTES, MAX_TOTAL_BYTES)):
    """Read a .whl, .zip, .tar.gz, .tgz (or other tar) artifact into an Artifact."""
    reader = _Reader(limits)
    lower = path.lower()
    try:
        if lower.endswith((".whl", ".zip")):
            _read_zip(path, reader)
        elif tarfile.is_tarfile(path):
            _read_tar(path, reader)
        else:
            raise ArtifactError("not a wheel, zip or tar archive")
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError, RuntimeError) as exc:
        # zipfile raises RuntimeError for encrypted members.
        raise ArtifactError(str(exc) or exc.__class__.__name__)
    if lower.endswith(".whl"):
        return Artifact(path, WHEEL, reader.files, reader.links)
    files, links = _strip_top(reader.files, reader.links)
    if lower.endswith(".tgz") and "package.json" in files:
        kind = NPM
    elif "PKG-INFO" in files or "setup.py" in files or "pyproject.toml" in files:
        kind = SDIST
    elif "package.json" in files:
        kind = NPM
    else:
        kind = SDIST
    return Artifact(path, kind, files, links)
