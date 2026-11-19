# SPDX-License-Identifier: Apache-2.0
"""Download one published version from PyPI or npm. Downloads only: nothing is
installed, unpacked or run."""

import base64
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request

PYPI = "https://pypi.org/pypi/%s/%s/json"
NPM = "https://registry.npmjs.org/%s/%s"
MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_METADATA = 32 * 1024 * 1024
TIMEOUT = 60

_PYPI_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
_NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]*$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class FetchError(Exception):
    pass


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise FetchError("refusing redirect to non-https URL %s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    return urllib.request.build_opener(_HttpsOnly())


def _get(url, limit):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise FetchError("refusing non-https URL %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "release-drift"})
    try:
        with _opener().open(req, timeout=TIMEOUT) as resp:
            data = resp.read(limit + 1)
    except OSError as exc:  # URLError and HTTPError are OSErrors
        raise FetchError("%s: %s" % (url, exc))
    if len(data) > limit:
        raise FetchError("%s is larger than %d bytes" % (url, limit))
    return data


def check_digest(data, algorithm, expected):
    """expected is a hex digest, or for sha512 an SRI base64 digest."""
    digest = hashlib.new(algorithm, data)
    if algorithm == "sha512":
        ok = base64.b64encode(digest.digest()).decode() == expected
    else:
        ok = digest.hexdigest() == expected.lower()
    if not ok:
        raise FetchError("%s digest does not match the registry's" % algorithm)


def pypi_files(name, version):
    """(filename, url, algorithm, digest, packagetype) for each file of a PyPI version."""
    if not _PYPI_NAME.match(name) or not _VERSION.match(version):
        raise FetchError("invalid PyPI name or version")
    meta = json.loads(_get(PYPI % (name, version), MAX_METADATA).decode("utf-8"))
    out = []
    for entry in meta.get("urls", []):
        sha = (entry.get("digests") or {}).get("sha256")
        if not sha:
            raise FetchError("%s has no sha256 digest" % entry.get("filename"))
        out.append((entry["filename"], entry["url"], "sha256", sha, entry.get("packagetype", "")))
    if not out:
        raise FetchError("%s %s has no files" % (name, version))
    return out


def npm_files(name, version):
    if not _NPM_NAME.match(name) or not _VERSION.match(version):
        raise FetchError("invalid npm name or version")
    quoted = urllib.parse.quote(name, safe="@")
    meta = json.loads(_get(NPM % (quoted, version), MAX_METADATA).decode("utf-8"))
    dist = meta.get("dist") or {}
    integrity = dist.get("integrity", "")
    sri = [i for i in integrity.split() if i.startswith("sha512-")]
    if not sri:
        raise FetchError("%s@%s has no sha512 integrity digest" % (name, version))
    url = dist.get("tarball", "")
    filename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    if name.startswith("@"):
        filename = name[1:].replace("/", "-") + "-" + version + ".tgz"
    return [(filename, url, "sha512", sri[0][len("sha512-"):], "npm")]


def fetch(registry, name, version, directory, kinds=None):
    """Download every file of the version into directory; return the paths."""
    files = pypi_files(name, version) if registry == "pypi" else npm_files(name, version)
    if kinds:
        files = [f for f in files if f[4] in kinds]
    if not os.path.isdir(directory):
        raise FetchError("%s is not a directory" % directory)
    saved = []
    for filename, url, algorithm, digest, _ in files:
        if not _FILENAME.match(filename):
            raise FetchError("refusing file name %r" % filename)
        target = os.path.join(directory, filename)
        data = _get(url, MAX_DOWNLOAD)
        check_digest(data, algorithm, digest)
        if os.path.exists(target):
            with open(target, "rb") as fh:
                if fh.read() != data:
                    raise FetchError("%s exists with different content" % target)
        else:
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".release-drift-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
        saved.append(target)
    return sorted(saved)
