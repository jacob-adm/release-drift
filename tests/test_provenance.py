# SPDX-License-Identifier: Apache-2.0
"""provenance: compare how two releases were published. Registry responses are served
from a table shaped like the real npm and PyPI answers (recorded 2026-11-26 from axios,
left-pad and sigstore), so no test uses the network."""

import base64
import io
import json
import sys
import unittest
from unittest import mock

from fixtures import TempCase, write_tar
from test_lockfile import Command as LockCommand, npm_lock

from release_drift import cli
from release_drift.fetch import FetchError

NPM_ATT = "https://registry.npmjs.org/-/npm/v1/attestations/"


def npm_version(name, version, user, workflow=None):
    meta = {"name": name, "version": version, "_npmUser": {"name": user, "email": "x@example.com"},
            "dist": {"tarball": "https://registry.npmjs.org/%s/-/%s-%s.tgz" % (name, name, version)}}
    if workflow:
        meta["dist"]["attestations"] = {"url": NPM_ATT + "%s@%s" % (name, version),
                                        "provenance": {"predicateType": "https://slsa.dev/provenance/v1"}}
    return meta


def npm_attestations(repository, path):
    payload = {"predicate": {"buildDefinition": {"externalParameters": {
        "workflow": {"ref": "refs/heads/main", "repository": repository, "path": path}}}}}
    return {"attestations": [
        {"predicateType": "https://github.com/npm/attestation/tree/main/specs/publish/v0.1",
         "bundle": {"dsseEnvelope": {"payload": base64.b64encode(b"{}").decode()}}},
        {"predicateType": "https://slsa.dev/provenance/v1",
         "bundle": {"dsseEnvelope": {"payload": base64.b64encode(json.dumps(payload).encode()).decode()}}}]}


class Registry:
    """Answers _get from a {url: json} table; any other URL is a 404, as the registry says."""

    def __init__(self, table):
        self.table, self.urls = table, []

    def __call__(self, url, limit, accept=None):
        self.urls.append(url)
        if url not in self.table:
            raise FetchError("%s: HTTP Error 404: Not Found" % url)
        return json.dumps(self.table[url]).encode()


def npm_registry(name, versions):
    """versions: [(version, user, (repository, path) or None)]"""
    table = {}
    for version, user, wf in versions:
        table["https://registry.npmjs.org/%s/%s" % (name, version)] = npm_version(name, version, user, wf)
        if wf:
            table[NPM_ATT + "%s@%s" % (name, version)] = npm_attestations(*wf)
    return Registry(table)


def pypi_registry(name, versions):
    """versions: [(version, publisher dict or None)]"""
    table, files = {}, []
    for version, publisher in versions:
        filename = "%s-%s.tar.gz" % (name, version)
        table["https://pypi.org/pypi/%s/%s/json" % (name, version)] = {"urls": [{"filename": filename}]}
        url = "https://pypi.org/integrity/%s/%s/%s/provenance" % (name, version, filename)
        files.append({"filename": filename, "provenance": url if publisher else None})
        if publisher:
            table[url] = {"attestation_bundles": [{"publisher": publisher, "attestations": []}]}
    table["https://pypi.org/simple/%s/" % name] = {"files": files}
    return Registry(table)


def run(registry, *args):
    """cli.main in this process, so the registry table stands in for the network."""
    out = io.BytesIO()
    with mock.patch("release_drift.provenance._get", registry), \
            mock.patch.object(sys, "stdout", mock.Mock(buffer=out)), \
            mock.patch.object(sys, "stderr", io.StringIO()):
        code = cli.main(list(args))
    return code, out.getvalue().decode().splitlines()


AXIOS = ("https://github.com/axios/axios", ".github/workflows/publish.yml")
GH = {"kind": "GitHub", "repository": "sigstore/sigstore-python", "workflow": "release.yml", "environment": None}


class Npm(unittest.TestCase):
    def test_release_without_the_provenance_the_previous_had_exits_1(self):
        reg = npm_registry("axios", [("1.14.0", "GitHub Actions", AXIOS), ("1.14.1", "jasonsaayman", None)])
        code, lines = run(reg, "provenance", "npm", "axios", "1.14.0", "1.14.1")
        self.assertEqual(code, 1)
        self.assertEqual(lines, [
            "provenance\tpublisher-changed\t-\tGitHub Actions -> jasonsaayman",
            "provenance\tremoved\t-\t1.14.0 has provenance (https://github.com/axios/axios "
            ".github/workflows/publish.yml); 1.14.1 has none"])

    def test_attested_by_another_repository_is_workflow_changed(self):
        reg = npm_registry("axios", [("1.0.0", "a", AXIOS), ("1.0.1", "a", ("https://github.com/evil/axios", AXIOS[1]))])
        code, lines = run(reg, "provenance", "npm", "axios", "1.0.0", "1.0.1", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads("\n".join(lines)), [{
            "category": "provenance", "status": "workflow-changed", "path": "-",
            "detail": "https://github.com/axios/axios .github/workflows/publish.yml -> "
                      "https://github.com/evil/axios .github/workflows/publish.yml"}])

    def test_move_to_trusted_publishing_is_not_a_finding(self):
        # yargs 18.1.0 -> 18.2.0 in the ordinary-release sample: a maintainer, then GitHub Actions with provenance.
        reg = npm_registry("yargs", [("18.1.0", "shadowspawn", None),
                                     ("18.2.0", "GitHub Actions", ("https://github.com/yargs/yargs", "r.yml"))])
        self.assertEqual(run(reg, "provenance", "npm", "yargs", "18.1.0", "18.2.0"), (0, []))

    def test_new_account_without_provenance_can_be_informational(self):
        reg = npm_registry("braces", [("3.0.2", "doowb", None), ("3.0.3", "jonschlinkert", None)])
        self.assertEqual(run(reg, "provenance", "npm", "braces", "3.0.2", "3.0.3")[0], 1)
        code, lines = run(reg, "provenance", "npm", "braces", "3.0.2", "3.0.3",
                          "--informational", "publisher-changed")
        self.assertEqual((code, lines), (0, ["provenance\tpublisher-changed\t-\tdoowb -> jonschlinkert"]))

    def test_attestation_url_off_the_registry_is_not_fetched(self):
        reg = npm_registry("x", [("1.0.0", "a", None)])
        meta = npm_version("x", "1.0.1", "a", AXIOS)
        meta["dist"]["attestations"]["url"] = "https://attacker.example/att"
        reg.table["https://registry.npmjs.org/x/1.0.1"] = meta
        code, lines = run(reg, "provenance", "npm", "x", "1.0.1", "1.0.0")
        self.assertNotIn("https://attacker.example/att", reg.urls)
        self.assertEqual((code, lines), (1, [
            "provenance\tremoved\t-\t1.0.1 has provenance (workflow not named); 1.0.0 has none"]))

    def test_unknown_version_exits_2(self):
        reg = npm_registry("x", [("1.0.0", "a", None)])
        self.assertEqual(run(reg, "provenance", "npm", "x", "1.0.0", "9.9.9"), (2, []))


class Pypi(unittest.TestCase):
    def test_release_without_provenance_exits_1(self):
        reg = pypi_registry("sigstore", [("3.6.1", GH), ("3.6.2", None)])
        code, lines = run(reg, "provenance", "pypi", "sigstore", "3.6.1", "3.6.2")
        self.assertEqual((code, lines), (1, [
            "provenance\tremoved\t-\t3.6.1 has provenance (GitHub sigstore/sigstore-python "
            "release.yml); 3.6.2 has none"]))

    def test_other_publisher_is_workflow_changed_and_same_is_clean(self):
        other = dict(GH, repository="someone/sigstore-python")
        reg = pypi_registry("sigstore", [("3.6.1", GH), ("3.6.2", GH), ("3.6.3", other)])
        self.assertEqual(run(reg, "provenance", "pypi", "sigstore", "3.6.1", "3.6.2"), (0, []))
        code, lines = run(reg, "provenance", "pypi", "sigstore", "3.6.2", "3.6.3")
        self.assertEqual((code, lines), (1, [
            "provenance\tworkflow-changed\t-\tGitHub sigstore/sigstore-python release.yml -> "
            "GitHub someone/sigstore-python release.yml"]))

    def test_neither_with_provenance_is_clean(self):
        reg = pypi_registry("requests", [("2.32.3", None), ("2.32.4", None)])
        self.assertEqual(run(reg, "provenance", "pypi", "requests", "2.32.3", "2.32.4"), (0, []))


class Lockfile(LockCommand):
    def setUp(self):
        super().setUp()
        files = {"package/package.json": b'{"name": "axios", "version": "%s"}',
                 "package/index.js": b"module.exports = 1\n"}
        for v in ("1.14.0", "1.14.1"):
            self.cached("npm", "axios", v, "axios-%s.tgz" % v, write_tar,
                        {k: (d % v.encode() if k.endswith(".json") else d) for k, d in files.items()})
        self.lock_paths = self.locks(npm_lock({"axios": "1.14.0"}), npm_lock({"axios": "1.14.1"}),
                                     "package-lock.json")

    def test_upgrade_that_drops_provenance_is_a_finding(self):
        reg = npm_registry("axios", [("1.14.0", "GitHub Actions", AXIOS), ("1.14.1", "GitHub Actions", None)])
        code, lines = run(reg, "lockfile", *self.lock_paths, "--cache", self.cache)
        self.assertEqual(code, 1)
        self.assertEqual([l for l in lines if "\tprovenance\t" in l], [
            "axios\t1.14.0\t1.14.1\tprovenance\tremoved\t-\t1.14.0 has provenance "
            "(https://github.com/axios/axios .github/workflows/publish.yml); 1.14.1 has none"])

    def test_no_provenance_asks_the_registry_nothing(self):
        reg = Registry({})
        code, lines = run(reg, "lockfile", *self.lock_paths, "--cache", self.cache, "--no-provenance")
        self.assertEqual((code, reg.urls), (0, []))


if __name__ == "__main__":
    unittest.main()
