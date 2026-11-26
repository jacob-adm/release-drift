# SPDX-License-Identifier: Apache-2.0
"""Read two versions of a lockfile and list the packages whose version changed.

Formats, detected from content: npm package-lock.json or npm-shrinkwrap.json
(lockfileVersion 1-3), pinned requirements (NAME==VERSION, as pip-compile and
uv export write them), and uv.lock or poetry.lock (TOML, needs Python 3.11+)."""

import json
import os
import re

from .fetch import _FILENAME, MAX_DOWNLOAD, FetchError, _get, check_digest, fetch, pypi_files

NPM, PYPI = "npm", "pypi"
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*===?\s*([^\s;#\\]+)")


class LockfileError(Exception):
    pass


def pep503(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _npm(data):
    """{install path: (name, version)} and [(name, reason)] skipped."""
    found, skipped = {}, []
    packages = data.get("packages")
    if isinstance(packages, dict):
        entries = [(path, meta) for path, meta in packages.items() if path]
    else:  # lockfileVersion 1: nested "dependencies"
        entries, stack = [], [("", data.get("dependencies") or {})]
        while stack:
            prefix, deps = stack.pop()
            for name, meta in deps.items():
                path = prefix + "node_modules/" + name
                entries.append((path, dict(meta, name=name)))
                stack.append((path + "/", meta.get("dependencies") or {}))
    for path, meta in entries:
        if not isinstance(meta, dict):
            continue
        name = meta.get("name") or path.rsplit("node_modules/", 1)[-1]
        version, resolved = meta.get("version", ""), meta.get("resolved", "")
        if meta.get("link") or not version or "node_modules/" not in path:
            skipped.append((name, "not installed from the registry"))
        elif resolved and not resolved.startswith("https://registry.npmjs.org/"):
            skipped.append((name, "resolved from " + resolved.split("#")[0]))
        else:
            found[path] = (name, version)
    return found, skipped


def _toml(text):
    try:
        import tomllib
    except ImportError:
        raise LockfileError("uv.lock and poetry.lock need Python 3.11 or later")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LockfileError("not a TOML lockfile: %s" % exc)
    found, skipped = {}, []
    for pkg in data.get("package", []):
        name, version, source = pkg.get("name", ""), pkg.get("version", ""), pkg.get("source") or {}
        # poetry.lock: no source means PyPI; uv.lock: source.registry names the index.
        index = "https://pypi.org/" if not source else source.get("registry") or source.get("url") or ""
        if not name or not version or not index.startswith("https://pypi.org/"):
            skipped.append((name, "not installed from PyPI"))
        else:
            found[pep503(name)] = (pep503(name), version)
    return found, skipped


def _requirements(text):
    found, skipped = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "--hash")):
            continue
        if line.startswith(("-", "git+", "http:", "https:", "file:", ".", "/")):
            skipped.append((line.split()[0], "not a NAME==VERSION pin"))
            continue
        m = _PIN.match(line)
        if m:
            found[pep503(m.group(1))] = (pep503(m.group(1)), m.group(3))
        else:
            skipped.append((line.split(";")[0].strip(), "not a NAME==VERSION pin"))
    return found, skipped


def read_lockfile(path):
    """(registry, {key: (name, version)}, [(name, reason)] skipped)."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise LockfileError("%s: %s" % (path, exc))
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and "lockfileVersion" in data:
        return (NPM,) + _npm(data)
    if re.search(r"^\[\[package\]\]", text, re.M):
        return (PYPI,) + _toml(text)
    if data is not None:
        raise LockfileError("%s: JSON without lockfileVersion is not a supported lockfile" % path)
    return (PYPI,) + _requirements(text)


def changes(old, new):
    """(upgraded [(name, old, new)], added [(name, version)], removed [(name, version)]),
    each sorted and without repeats."""
    up = {(o[0], o[1], new[k][1]) for k, o in old.items() if k in new and new[k][1] != o[1]}
    added = {new[k] for k in new if k not in old}
    removed = {old[k] for k in old if k not in new}
    return sorted(up), sorted(added), sorted(removed)


def _wheel_tag(filename):
    """The part of a wheel's name after NAME-VERSION: python, ABI and platform tags."""
    return filename[:-4].split("-", 2)[2] if filename.endswith(".whl") else None


def _cached(directory, kind):
    if not os.path.isdir(directory):
        return []
    names = sorted(n for n in os.listdir(directory) if not n.startswith("."))
    if kind == "wheel":
        return [n for n in names if n.endswith(".whl")]
    return [n for n in names if not n.endswith(".whl")]


def artifact_pair(registry, name, old, new, cache, kind="sdist"):
    """Paths of the OLD and NEW artifacts of NAME, from CACHE or downloaded into it.
    Wheels are paired by file tag; sdist and npm take the one file of each version."""
    dirs = [os.path.join(cache, registry, name.replace("/", "+"), v) for v in (old, new)]
    picked = []
    for version, directory in zip((old, new), dirs):
        files = _cached(directory, kind if registry == PYPI else "sdist")
        if not files:
            os.makedirs(directory, exist_ok=True)
            if registry == PYPI and kind == "wheel":
                files = [f[0] for f in pypi_files(name, version) if f[0].endswith(".whl")]
            else:
                fetch(registry, name, version, directory, kind if registry == PYPI else "sdist")
                files = _cached(directory, kind if registry == PYPI else "sdist")
        picked.append(files)
    if registry == PYPI and kind == "wheel":
        common = sorted(set(map(_wheel_tag, picked[0])) & set(map(_wheel_tag, picked[1])))
        if not common:
            raise FetchError("%s %s and %s share no wheel tag" % (name, old, new))
        out = []
        for version, directory, files in zip((old, new), dirs, picked):
            filename = [f for f in files if _wheel_tag(f) == common[0]][0]
            path = os.path.join(directory, filename)
            if not os.path.exists(path):
                _fetch_one_wheel(name, version, filename, directory)
            out.append(path)
        return out
    for version, files in zip((old, new), picked):
        if len(files) != 1:
            raise FetchError("%s %s: expected one %s file, found %d" % (name, version, kind, len(files)))
    return [os.path.join(d, f[0]) for d, f in zip(dirs, picked)]


def _fetch_one_wheel(name, version, filename, directory):
    if not _FILENAME.match(filename):
        raise FetchError("refusing file name %r" % filename)
    for fname, url, algorithm, digest, _ in pypi_files(name, version):
        if fname == filename:
            data = _get(url, MAX_DOWNLOAD)
            check_digest(data, algorithm, digest)
            tmp = os.path.join(directory, "." + filename)
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, os.path.join(directory, filename))
            return
    raise FetchError("%s %s has no file %s" % (name, version, filename))
