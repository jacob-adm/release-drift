# SPDX-License-Identifier: Apache-2.0
"""Compare how two releases were published, from the registry's own records: whether each
has a provenance attestation, which build workflow the attestation names, and (npm) which
account published it. Reads registry metadata only; the registry checks the attestation's
Sigstore signature when the release is uploaded, and release-drift does not check it again."""

import base64
import json
import urllib.parse

from .fetch import _NPM_NAME, _PYPI_NAME, _VERSION, MAX_METADATA, NPM, PYPI, FetchError, _get

PYPI_SIMPLE = "https://pypi.org/simple/%s/"
PYPI_INTEGRITY = "https://pypi.org/integrity/"
NPM_ATTESTATIONS = "https://registry.npmjs.org/-/npm/v1/attestations/"
SLSA = ("https://slsa.dev/provenance/v1", "https://slsa.dev/provenance/v0.2")


def _json(url, accept=None):
    data = _get(url, MAX_METADATA, accept)
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise FetchError("%s did not return JSON" % url)


def _npm_workflow(attestations):
    """'REPOSITORY PATH' of the build workflow in an npm attestations document, or ''."""
    for att in attestations.get("attestations") or []:
        if att.get("predicateType") not in SLSA:
            continue
        try:
            payload = att["bundle"]["dsseEnvelope"]["payload"]
            predicate = json.loads(base64.b64decode(payload)).get("predicate") or {}
        except (KeyError, TypeError, ValueError):
            continue
        params = (predicate.get("buildDefinition") or {}).get("externalParameters") or {}
        wf = params.get("workflow") or {}
        if wf.get("repository") and wf.get("path"):
            return "%s %s" % (wf["repository"], wf["path"])
        source = (predicate.get("invocation") or {}).get("configSource") or {}  # SLSA v0.2
        if source.get("uri") and source.get("entryPoint"):
            return "%s %s" % (source["uri"].split("@")[0], source["entryPoint"])
    return ""


def read_npm(name, version):
    """{"workflow": str or None, "publisher": str}. workflow is None without provenance,
    '' when the attestation names no workflow release-drift can read."""
    if not _NPM_NAME.match(name) or not _VERSION.match(version):
        raise FetchError("invalid npm name or version")
    meta = _json(NPM % (urllib.parse.quote(name, safe="@"), version))
    publisher = (meta.get("_npmUser") or {}).get("name", "")
    att = (meta.get("dist") or {}).get("attestations")
    if not att:
        return {"workflow": None, "publisher": publisher}
    url = att.get("url", "") if isinstance(att, dict) else ""
    if not url.startswith(NPM_ATTESTATIONS):
        return {"workflow": "", "publisher": publisher}
    return {"workflow": _npm_workflow(_json(url)), "publisher": publisher}


def read_pypi(name, version):
    """{"workflow": str or None, "publisher": ""}: the publisher identities of the
    version's attested files, or None when no file of the version has provenance."""
    if not _PYPI_NAME.match(name) or not _VERSION.match(version):
        raise FetchError("invalid PyPI name or version")
    files = {e.get("filename") for e in _json(PYPI % (name, version)).get("urls", [])}
    simple = _json(PYPI_SIMPLE % name, "application/vnd.pypi.simple.v1+json")
    urls = sorted(f["provenance"] for f in simple.get("files", [])
                  if f.get("filename") in files and isinstance(f.get("provenance"), str)
                  and f["provenance"].startswith(PYPI_INTEGRITY))
    if not urls:
        return {"workflow": None, "publisher": ""}
    ids = set()
    for bundle in _json(urls[0]).get("attestation_bundles") or []:
        pub = bundle.get("publisher") or {}
        parts = [pub.get(k) or "" for k in ("kind", "repository", "workflow")]
        if pub.get("environment"):
            parts.append("environment=" + pub["environment"])
        ids.add(" ".join(p for p in parts if p))
    return {"workflow": ", ".join(sorted(ids)), "publisher": ""}


def read(registry, name, version):
    return read_pypi(name, version) if registry == "pypi" else read_npm(name, version)


def compare(old, new, old_version, new_version):
    """Records (category, status, path, detail) for what changed in how NEW was published."""
    records = []
    if old["workflow"] is not None and new["workflow"] is None:
        records.append(("provenance", "removed", "-", "%s has provenance (%s); %s has none"
                        % (old_version, old["workflow"] or "workflow not named", new_version)))
    elif old["workflow"] and new["workflow"] and old["workflow"] != new["workflow"]:
        records.append(("provenance", "workflow-changed", "-",
                        "%s -> %s" % (old["workflow"], new["workflow"])))
    # With an attestation the build itself is named, so workflow-changed covers it.
    if (new["workflow"] is None and old["publisher"] and new["publisher"]
            and old["publisher"] != new["publisher"]):
        records.append(("provenance", "publisher-changed", "-",
                        "%s -> %s" % (old["publisher"], new["publisher"])))
    return records


def provenance(registry, name, old_version, new_version):
    return compare(read(registry, name, old_version), read(registry, name, new_version),
                   old_version, new_version)
