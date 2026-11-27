# SPDX-License-Identifier: Apache-2.0
"""--audit-json: one audit result object (format audit-result/1) for a release-drift run.

Each record becomes a finding that keeps the --json record unchanged under "record" and adds
a rule id, a severity fixed by its category, one sentence and its evidence. A record that is
not a finding (is_finding is False) is listed with severity "info" and does not set exit 1.
"""

import os
from urllib.parse import quote

from . import __version__

FORMAT = "audit-result/1"

# Fixed per category; see docs/commands.md.
SEVERITY = {
    "build": "high", "npm-script": "high", "provenance": "high", "pth": "high",
    "pyc-only": "high", "startup": "high",
    "blob": "medium", "dependency": "medium", "file": "medium", "link": "medium",
    "native": "medium", "url-host": "medium",
}

NOUN = {
    "blob": "encoded string", "build": "build script or build section",
    "dependency": "dependency", "file": "file", "link": "link", "native": "native binary",
    "npm-script": "install script", "pth": ".pth file", "pyc-only": "compiled file without source",
    "startup": "start-up module", "url-host": "URL host",
}

# Surfaces found inside a file, named "in PATH"; the others are the file itself.
_IN_FILE = ("blob", "build", "dependency", "npm-script", "url-host")

# What a .pth file does when it is in site-packages (its DETAIL).
_PTH = {
    "import": ", with an import line that Python runs at every start",
    "paths": ", which adds directories to the import path",
}

_PROVENANCE = {
    "removed": "Provenance removed: %s",
    "workflow-changed": "Published from a different workflow: %s",
    "publisher-changed": "Published without provenance by a different account: %s",
}

_PACKAGE = {
    "unchanged": "no difference found",
    "added": "new package, not compared",
    "removed": "package removed",
    "skipped": "not compared: %s",
    "error": "could not be compared: %s",
}


def registry_url(registry, name, version):
    """The registry's page for one version."""
    if registry == "npm":
        return "https://www.npmjs.com/package/%s/v/%s" % (quote(name, safe="@/"),
                                                         quote(version, safe=""))
    return "https://pypi.org/project/%s/%s/" % (quote(name, safe=""), quote(version, safe=""))


def message(row, ref=None):
    """Return one sentence that says what a four-field record means."""
    cat, status, path, detail = row["category"], row["status"], row["path"], row["detail"]
    if cat == "provenance":
        return _PROVENANCE.get(status, "Provenance " + status + ": %s") % detail
    noun = NOUN.get(cat, cat)
    where = "" if path == "-" else " " + path
    if status == "not-in-tag":
        text = "%s%s is not in %s" % (noun.capitalize(), where, ref)
    elif status == "differs":
        text = "%s%s differs from %s" % (noun.capitalize(), where, ref)
    else:
        verb = {"added": "New", "changed": "Changed", "removed": "Removed"}.get(status, status)
        # A surface that is a whole file is named by its path; the others are found in a file.
        joint = " in" if cat in _IN_FILE and where else ""
        text = "%s %s%s%s" % (verb, noun, joint, where)
    if cat == "pth" and detail in _PTH:
        return text + _PTH[detail]
    # setup.py and start-up modules carry their own file name as DETAIL; it adds nothing.
    if detail != "-" and detail != path.rsplit("/", 1)[-1]:
        text += ": " + detail
    return text


def finding(row, is_finding, ref=None, commit=None, url=None, prefix=""):
    """Return the audit finding for one --json record (four fields, or seven for lockfile)."""
    rule = "release-drift.%s.%s" % (row["category"], row["status"])
    evidence = {}
    if row["path"] != "-":
        evidence["path"] = row["path"]
    if ref is not None:
        evidence["ref"] = ref
    if commit is not None:
        evidence["commit"] = commit
    if url is not None:
        evidence["url"] = url
    if row["category"] == "package":
        text = _PACKAGE.get(row["status"], row["status"])
        text = text % row["detail"] if "%s" in text else text
    else:
        text = message(row, ref)
    return {
        "rule": rule,
        "severity": SEVERITY.get(row["category"], "high") if is_finding else "info",
        "message": prefix + text,
        "evidence": evidence,
        "record": row,
    }


def target(command, args):
    """The target object for a command's arguments."""
    if command == "provenance":
        return {"kind": "package", "name": "%s/%s" % (args.registry, args.name),
                "ref": args.new, "base": args.old}
    if command == "source":
        return {"kind": "artifact", "name": os.path.basename(args.artifact), "ref": args.ref}
    return {"kind": "artifact", "name": os.path.basename(args.new),
            "base": os.path.basename(args.old)}


def result(target_, findings, errors):
    """Return the audit result object; ERRORS are message strings."""
    found = any(f["severity"] != "info" for f in findings)
    return {
        "format": FORMAT,
        "tool": {"name": "release-drift", "version": __version__},
        "target": target_,
        "exit": 2 if errors else (1 if found else 0),
        "findings": findings,
        "errors": [{"message": m} for m in errors],
    }
