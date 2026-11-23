# SPDX-License-Identifier: Apache-2.0
"""Read a package artifact into memory without extracting it.

Every artifact is treated as hostile: member names are checked before use,
sizes are capped while reading (header sizes are not trusted), and nothing is
written to disk. An artifact someone else unpacked can be given as a
directory; its files are read under the same checks and limits.
"""

import os
import posixpath
import stat
import tarfile
import zipfile
import zlib

try:
    import lzma
    _DECOMPRESS_ERRORS = (zlib.error, lzma.LZMAError)
except ImportError:  # Python built without lzma
    _DECOMPRESS_ERRORS = (zlib.error,)

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
            # orig_filename is the name as stored: zipfile cuts .filename at a NUL
            # and, on Windows, turns "\\" into "/", which would hide both from check_name.
            name = info.orig_filename
            if info.is_dir():
                check_name(name)
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if info.create_system == 3 and mode == 0o120000:  # Unix symlink
                with zf.open(info) as fh:
                    target = fh.read(4097)
                reader.add_link(name, target.decode("utf-8", "surrogateescape"))
                continue
            with zf.open(info) as fh:
                reader.add(name, fh.read)


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


def _read_dir(root, reader):
    """Read an unpacked artifact. Links are recorded, never followed."""
    for folder, dirs, names in os.walk(root):
        rel = os.path.relpath(folder, root)
        prefix = "" if rel == "." else "/".join(rel.split(os.sep)) + "/"
        for name in sorted(dirs + names):
            full = os.path.join(folder, name)
            mode = os.lstat(full).st_mode
            if stat.S_ISLNK(mode):
                reader.add_link(prefix + name, os.readlink(full))
            elif stat.S_ISREG(mode):
                with open(full, "rb") as fh:
                    reader.add(prefix + name, fh.read)
            elif not stat.S_ISDIR(mode):
                raise ArtifactError("member %r is a device, FIFO or other special file"
                                    % (prefix + name))
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(folder, d)))


def _strip_top(names_files, names_links):
    """sdists and npm tarballs keep everything under one top directory. tar on
    macOS also writes "._NAME" metadata files at the top level; those stay in
    the artifact, as files at its root, so they are compared like any other."""
    extra = {n for n in names_files if "/" not in n and n.startswith("._")}
    names = [n for n in list(names_files) + list(names_links) if n not in extra]
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) != 1:
        raise ArtifactError("expected one top-level directory, found %d" % len(tops))
    if any("/" not in n for n in names):
        raise ArtifactError("expected one top-level directory, found a top-level file")
    files = {n.split("/", 1)[1]: d for n, d in names_files.items() if n not in extra}
    links = {n.split("/", 1)[1]: t for n, t in names_links.items()}
    for n in extra:
        if n in files or n in links:
            raise ArtifactError("duplicate member %r" % n)
        files[n] = names_files[n]
    return files, links


def read_artifact(path, limits=(MAX_MEMBERS, MAX_MEMBER_BYTES, MAX_TOTAL_BYTES)):
    """Read a .whl, .zip, .tar.gz, .tgz (or other tar) artifact, or the
    directory one was unpacked into, into an Artifact."""
    reader = _Reader(limits)
    lower = path.lower()
    unpacked = os.path.isdir(path)
    try:
        if unpacked:
            _read_dir(path, reader)
        elif lower.endswith((".whl", ".zip")):
            _read_zip(path, reader)
        elif tarfile.is_tarfile(path):
            _read_tar(path, reader)
        else:
            raise ArtifactError("not a wheel, zip or tar archive")
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError, RuntimeError) + _DECOMPRESS_ERRORS as exc:
        # zipfile raises RuntimeError for encrypted members; corrupt compressed
        # data raises the decompressor's own error.
        raise ArtifactError(str(exc) or exc.__class__.__name__)
    if lower.endswith(".whl"):
        return Artifact(path, WHEEL, reader.files, reader.links)
    if unpacked:
        # The directory is the artifact's root: a wheel's contents, or the
        # inside of an sdist's or npm tarball's top directory. A directory that
        # holds nothing but one directory is where an archive was unpacked
        # (`tar xzf` leaves `package/` or `NAME-VERSION/`): read from inside it,
        # as from the archive, so its files are not compared one level too high.
        files, links = reader.files, reader.links
        names = list(files) + list(links)
        if names and all("/" in n for n in names) and \
                len({n.split("/", 1)[0] for n in names}) == 1:
            files, links = _strip_top(files, links)
        if any(n.split("/")[0].endswith(".dist-info") and "/" in n for n in files):
            return Artifact(path, WHEEL, files, links)
    else:
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
