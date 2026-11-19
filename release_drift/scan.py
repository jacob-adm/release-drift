# SPDX-License-Identifier: Apache-2.0
"""Find execution surfaces and notable strings in an artifact's files.

Each finding is a tuple (category, path, detail, content_sensitive). A
content-sensitive finding is reported as "changed" by `diff` when the file it
sits in changes; the others are compared by their detail only.
"""

import hashlib
import json
import re
import unicodedata

from .archive import NPM, SDIST

NPM_INSTALL_SCRIPTS = ("preinstall", "install", "postinstall", "prepare")
STARTUP_MODULES = ("sitecustomize.py", "usercustomize.py")
BUILD_SCRIPTS = ("setup.py", "hatch_build.py")
BLOB_MIN = 200

_URL = re.compile(r"(?ai)\b(?:https?|wss?|ftp)://")
_PORT = re.compile(r":[0-9]{1,5}")
_BLOB = re.compile(rb"[A-Za-z0-9+/_-]{%d,}={0,2}" % BLOB_MIN)
_HEX = re.compile(rb"[0-9A-Fa-f]+")


def native_format(data):
    """Name the executable format the bytes start with, or None."""
    head = data[:8]
    if head.startswith(b"\x7fELF"):
        return "ELF"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "Mach-O"
    if head[:4] == b"\xca\xfe\xba\xbe" and len(head) == 8:
        # Java class files share this magic; their version number is 45 or more,
        # a universal Mach-O's architecture count is small.
        if int.from_bytes(head[4:8], "big") < 45:
            return "Mach-O"
        return None
    if head[:2] == b"MZ" and len(data) >= 0x40:
        offset = int.from_bytes(data[0x3C:0x40], "little")
        if data[offset:offset + 4] == b"PE\x00\x00":
            return "PE"
    if head[:4] == b"\x00asm":
        return "WebAssembly"
    return None


def pth_detail(data):
    """site.py executes the lines of a .pth file that start with 'import'."""
    for line in data.splitlines():
        if line.startswith((b"import ", b"import\t")):
            return "import"
    return "paths"


def _section_lines(text, header, nested=True):
    """Lines of one TOML table (and, if nested, its sub-tables), matched textually
    (no TOML parser before Python 3.11)."""
    out = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == header or (nested and stripped.startswith(header[:-1] + "."))
            if inside and stripped != header:
                out.append(stripped)
            continue
        if inside and stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


def pyproject_findings(data):
    """Build settings in pyproject.toml that decide which code runs at build time."""
    text = data.decode("utf-8", "replace")
    details = []
    for line in _section_lines(text, "[build-system]"):
        key = line.split("=", 1)[0].strip()
        if key in ("build-backend", "backend-path"):
            details.append(" ".join(line.split()))
    for line in _section_lines(text, "[tool.poetry]", nested=False):
        # Older Poetry: build = "build.py" or build = { script = "build.py" }
        if line.split("=", 1)[0].split(".", 1)[0].strip() == "build":
            details.append("tool.poetry: " + " ".join(line.split()))
    for header in ("[tool.hatch.build]", "[tool.poetry.build]", "[tool.pdm.build]"):
        for line in _section_lines(text, header):
            if line.startswith("[") and "hooks" in line:
                details.append(line)
            elif line.split("=", 1)[0].strip() in ("script", "setup-script", "build"):
                details.append(header[1:-1] + ": " + " ".join(line.split()))
    return details


def npm_script_findings(data):
    try:
        manifest = json.loads(data.decode("utf-8"))
    except ValueError:
        return ["package.json does not parse"]
    if not isinstance(manifest, dict):
        return ["package.json is not an object"]
    scripts = manifest.get("scripts")
    if not isinstance(scripts, dict):
        return []
    out = []
    for name in NPM_INSTALL_SCRIPTS:
        if name in scripts:
            out.append("%s=%s" % (name, scripts[name]))
    return out


def _is_manifest_to_scan(path):
    """The root package.json, and those of bundled dependencies."""
    if path == "package.json":
        return True
    parts = path.split("/")
    if parts[-1] != "package.json" or len(parts) < 3:
        return False
    if parts[-3] == "node_modules":
        return True
    return len(parts) >= 4 and parts[-4] == "node_modules" and parts[-3].startswith("@")


def _pyc_source(path):
    parts = path.split("/")
    name = parts[-1]
    if len(parts) >= 2 and parts[-2] == "__pycache__":
        return "/".join(parts[:-2] + [name.split(".", 1)[0] + ".py"])
    return path[:-1]


def surfaces(artifact):
    """Execution surfaces of an artifact: what can run at install, build or startup."""
    out = []
    files = artifact.files
    for path in sorted(files):
        data = files[path]
        base = path.rsplit("/", 1)[-1]
        if base.endswith(".pth"):
            out.append(("pth", path, pth_detail(data), True))
        if base in STARTUP_MODULES:
            out.append(("startup", path, base, True))
        if artifact.kind == SDIST and path in BUILD_SCRIPTS:
            out.append(("build", path, path, True))
        if artifact.kind == SDIST and path == "pyproject.toml":
            for detail in pyproject_findings(data):
                out.append(("build", path, detail, False))
        if artifact.kind == NPM and _is_manifest_to_scan(path):
            for detail in npm_script_findings(data):
                out.append(("npm-script", path, detail, False))
        if artifact.kind == NPM and base == "binding.gyp" and "/" not in path:
            out.append(("npm-script", path, "install=node-gyp rebuild (implicit)", False))
        if base.endswith(".pyc") and _pyc_source(path) not in files:
            out.append(("pyc-only", path, "no .py source", True))
        fmt = native_format(data)
        if fmt:
            out.append(("native", path, fmt, True))
    for path in sorted(artifact.links):
        out.append(("link", path, artifact.links[path], False))
    return out


def _host_char(ch):
    """Letters and digits of any script, combining marks and ._~%-: never a control character."""
    return ch.isalnum() or ch in "._~%-" or unicodedata.category(ch)[0] == "M"


def url_hosts(data):
    """Hosts (and ports) after a URL scheme, as found, with ASCII letters lower-cased."""
    if b"://" not in data:
        return
    text = data.decode("utf-8", "surrogateescape")
    for match in _URL.finditer(text):
        start = end = match.end()
        while end < len(text) and _host_char(text[end]):
            end += 1
        if end == start:
            continue
        port = _PORT.match(text, end)
        host = text[start:end] + (port.group(0) if port else "")
        yield host.encode("utf-8").lower().decode("utf-8")


def strings(artifact):
    """URL hosts and long encoded blobs, as (category, path, detail) tuples."""
    out = set()
    for path, data in artifact.files.items():
        for host in url_hosts(data):
            out.add(("url-host", path, host))
        for match in _BLOB.finditer(data):
            blob = match.group(0)
            kind = "hex" if _HEX.fullmatch(blob) else "base64"
            digest = hashlib.sha256(blob).hexdigest()[:16]
            out.add(("blob", path, "%s len=%d sha256=%s" % (kind, len(blob), digest)))
    return sorted(out)

