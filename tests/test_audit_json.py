# SPDX-License-Identifier: Apache-2.0
"""release-drift --audit-json: one audit result object built from the --json records."""

import json
import os
import subprocess
import unittest

from fixtures import ROOT, MALICIOUS_PTH, TempCase, run_cli, wheel_files, write_tar, write_zip
from test_provenance import AXIOS, npm_registry, run as run_in_process

from release_drift import __version__, audit

TOP = {"format", "tool", "target", "exit", "findings", "errors"}
FINDING = {"rule", "severity", "message", "evidence", "record"}


def check(test, code, out):
    """Parse the object and check the rules of format audit-result/1 it can break."""
    doc = json.loads(out)
    test.assertEqual(set(doc), TOP)
    test.assertEqual(doc["format"], "audit-result/1")
    test.assertEqual(doc["tool"], {"name": "release-drift", "version": __version__})
    for f in doc["findings"]:
        test.assertEqual(set(f), FINDING)
        test.assertRegex(f["rule"], r"\Arelease-drift\.[a-z-]+\.[a-z-]+\Z")
        test.assertIn(f["severity"], ("high", "medium", "info"))
        test.assertTrue(f["evidence"], f)
        if "path" in f["evidence"]:
            test.assertEqual(f["evidence"]["path"], f["record"]["path"])
    found = any(f["severity"] != "info" for f in doc["findings"])
    want = 2 if doc["errors"] else (1 if found else 0)
    test.assertEqual((doc["exit"], code), (want, want))
    return doc


def summary(doc):
    return [(f["rule"], f["severity"], f["message"]) for f in doc["findings"]]


class Diff(TempCase):
    def wheels(self, extra):
        old = write_zip(self.path("demo-1.0-py3-none-any.whl"), wheel_files("1.0"))
        files = wheel_files("1.1", extra)
        files["demo/core.py"] = b"def add(a, b):\n    return b + a\n"
        new = write_zip(self.path("demo-1.1-py3-none-any.whl"), files)
        return old, new

    def test_records_and_exit_match_json(self):
        old, new = self.wheels({"demo_init.pth": MALICIOUS_PTH})
        jcode, jout, jerr = run_cli("diff", "--json", old, new)
        code, out, err = run_cli("diff", "--audit-json", old, new)
        doc = check(self, code, out)
        self.assertEqual([f["record"] for f in doc["findings"]], json.loads(jout))
        self.assertEqual((code, err), (jcode, jerr))

    def test_default_and_json_output_unchanged(self):
        old, new = self.wheels({"demo_init.pth": MALICIOUS_PTH})
        code, out, err = run_cli("diff", old, new)
        self.assertEqual((code, err), (1, b""))
        self.assertIn(b"pth\tadded\tdemo_init.pth\timport\n", out)
        self.assertNotIn(b"severity", run_cli("diff", "--json", old, new)[1])

    def test_findings_and_info(self):
        old, new = self.wheels({"demo_init.pth": MALICIOUS_PTH,
                                "demo/links.py": b"URL = 'https://example.invalid/x'\n"})
        code, out, _ = run_cli("diff", "--audit-json", old, new)
        doc = check(self, code, out)
        self.assertEqual(doc["target"], {"kind": "artifact", "name": "demo-1.1-py3-none-any.whl",
                                         "base": "demo-1.0-py3-none-any.whl"})
        got = summary(doc)
        self.assertIn(("release-drift.pth.added", "high", "New .pth file demo_init.pth, with an "
                       "import line that Python runs at every start"), got)
        self.assertIn(("release-drift.file.changed", "info", "Changed file demo/core.py"), got)
        self.assertIn(("release-drift.url-host.added", "info",
                       "New URL host in demo/links.py: example.invalid"), got)

    def test_only_info_exits_0(self):
        old, new = self.wheels({})
        code, out, _ = run_cli("diff", "--audit-json", old, new)
        doc = check(self, code, out)
        self.assertEqual(code, 0)
        self.assertTrue(doc["findings"])
        self.assertEqual({f["severity"] for f in doc["findings"]}, {"info"})

    def test_informational_makes_a_finding_info(self):
        old = write_tar(self.path("demo-1.0.tar.gz"), {"demo-1.0/setup.py": b"setup()\n"})
        new = write_tar(self.path("demo-1.1.tar.gz"), {"demo-1.1/setup.py": b"setup(x=1)\n"})
        code, out, _ = run_cli("diff", "--audit-json", old, new)
        self.assertIn(("release-drift.build.changed", "high",
                       "Changed build script or build section in setup.py"),
                      summary(check(self, code, out)))
        code, out, _ = run_cli("diff", "--audit-json", "--informational", "build-changed", old, new)
        doc = check(self, code, out)
        self.assertEqual(code, 0)
        self.assertIn(("release-drift.build.changed", "info",
                       "Changed build script or build section in setup.py"), summary(doc))

    def test_unreadable_artifact_still_writes_the_object(self):
        bad = self.path("bad.whl")
        with open(bad, "wb") as fh:
            fh.write(b"not a zip")
        code, out, err = run_cli("diff", "--audit-json", bad, bad)
        doc = check(self, code, out)
        self.assertEqual(doc["findings"], [])
        self.assertEqual(len(doc["errors"]), 1)
        self.assertTrue(err.startswith(b"release-drift: error: "))

    def test_usage_errors_still_write_the_object(self):
        old, new = self.wheels({})
        for args, text in ((("diff", "--json", "--audit-json", old, new), "not allowed with"),
                           (("diff", "--audit-json", "--informational", "x", old, new),
                            "--informational: unknown value"),
                           (("diff", "--audit-json", old), "required")):
            with self.subTest(args[1:3]):
                code, out, err = run_cli(*args)
                doc = check(self, code, out)
                self.assertIn(text, doc["errors"][0]["message"])
                self.assertIn(b"usage:", err)

    def test_usage_error_without_the_flag_is_unchanged(self):
        code, out, err = run_cli("diff", "--informational", "x", "a", "b")
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"--informational: unknown value", err)


class Source(TempCase):
    def test_ref_and_commit(self):
        repo = self.path("repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "setup.py"), "wb") as fh:
            fh.write(b"setup()\n")
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
               "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
        for args in (["init", "-q"], ["add", "setup.py"], ["commit", "-q", "-m", "v1"],
                     ["tag", "v1.0"]):
            subprocess.run(git + args, check=True, stdout=subprocess.PIPE)
        commit = subprocess.run(git + ["rev-parse", "v1.0^{commit}"], check=True,
                                stdout=subprocess.PIPE).stdout.decode().strip()
        sdist = write_tar(self.path("demo-1.0.tar.gz"),
                          {"demo-1.0/setup.py": b"setup()\n", "demo-1.0/extra.py": b"x = 1\n"})
        code, out, _ = run_cli("source", "--audit-json", sdist, "--repo", repo, "--ref", "v1.0")
        doc = check(self, code, out)
        self.assertEqual(doc["target"], {"kind": "artifact", "name": "demo-1.0.tar.gz",
                                         "ref": "v1.0"})
        self.assertEqual([(f["rule"], f["severity"], f["message"], f["evidence"])
                          for f in doc["findings"]],
                         [("release-drift.file.not-in-tag", "medium", "File extra.py is not in v1.0",
                           {"path": "extra.py", "ref": "v1.0", "commit": commit})])


class Lockfile(TempCase):
    def setUp(self):
        super().setUp()
        self.cache = self.path("cache")

    def cached(self, version, files):
        directory = os.path.join(self.cache, "pypi", "demo", version)
        os.makedirs(directory)
        write_zip(os.path.join(directory, "demo-%s-py3-none-any.whl" % version), files)

    def test_findings_errors_and_urls(self):
        self.cached("1.0", wheel_files("1.0"))
        self.cached("1.1", wheel_files("1.1", {"demo_init.pth": MALICIOUS_PTH}))
        os.makedirs(os.path.join(self.cache, "pypi", "broken", "2.0"))
        os.makedirs(os.path.join(self.cache, "pypi", "broken", "2.1"))
        for v in ("2.0", "2.1"):
            with open(os.path.join(self.cache, "pypi", "broken", v,
                                   "broken-%s-py3-none-any.whl" % v), "wb") as fh:
                fh.write(b"not a zip")
        for name, text in (("old.txt", "demo==1.0\nbroken==2.0\ngone==1.0\n"),
                           ("new.txt", "demo==1.1\nbroken==2.1\nidna==3.7\n")):
            with open(self.path(name), "w") as fh:
                fh.write(text)
        args = ("lockfile", self.path("old.txt"), self.path("new.txt"), "--cache", self.cache,
                "--no-provenance", "--type", "wheel")
        jcode, jout, _ = run_cli(*(args + ("--json",)))
        code, out, _ = run_cli(*(args + ("--audit-json",)))
        doc = check(self, code, out)
        self.assertEqual(code, jcode)
        self.assertEqual([f["record"] for f in doc["findings"]], json.loads(jout))
        self.assertEqual(len(doc["errors"]), 1)
        self.assertTrue(doc["errors"][0]["message"].startswith("broken 2.0 -> 2.1: "))
        by_rule = {f["rule"]: f for f in doc["findings"]}
        pth = by_rule["release-drift.pth.added"]
        self.assertEqual((pth["severity"], pth["message"], pth["evidence"]),
                         ("high", "demo 1.0 -> 1.1: New .pth file demo_init.pth, with an import line "
                          "that Python runs at every start",
                          {"path": "demo_init.pth", "url": "https://pypi.org/project/demo/1.1/"}))
        self.assertEqual(by_rule["release-drift.package.added"]["message"],
                         "idna 3.7: new package, not compared")
        self.assertEqual(by_rule["release-drift.package.removed"]["message"],
                         "gone 1.0: package removed")
        self.assertEqual(by_rule["release-drift.package.error"]["severity"], "info")

    def test_unreadable_lockfile_still_writes_the_object(self):
        code, out, err = run_cli("lockfile", "--audit-json", self.path("none"), self.path("none"))
        doc = check(self, code, out)
        self.assertEqual((doc["findings"], len(doc["errors"])), ([], 1))
        self.assertTrue(err.startswith(b"release-drift: error: "))


class Provenance(unittest.TestCase):
    def test_npm_release_without_provenance(self):
        reg = npm_registry("axios", [("1.14.0", "GitHub Actions", AXIOS),
                                     ("1.14.1", "jasonsaayman", None)])
        code, lines = run_in_process(reg, "provenance", "--audit-json", "npm", "axios", "1.14.0",
                                     "1.14.1")
        doc = check(self, code, "\n".join(lines))
        self.assertEqual(doc["target"], {"kind": "package", "name": "npm/axios",
                                         "ref": "1.14.1", "base": "1.14.0"})
        self.assertEqual(summary(doc), [
            ("release-drift.provenance.publisher-changed", "high",
             "Published without provenance by a different account: GitHub Actions -> "
             "jasonsaayman"),
            ("release-drift.provenance.removed", "high",
             "Provenance removed: 1.14.0 has provenance (https://github.com/axios/axios "
             ".github/workflows/publish.yml); 1.14.1 has none")])
        self.assertEqual(doc["findings"][0]["evidence"],
                         {"url": "https://www.npmjs.com/package/axios/v/1.14.1"})

    def test_informational_publisher_changed(self):
        reg = npm_registry("axios", [("1.14.0", "a", None), ("1.14.1", "b", None)])
        code, lines = run_in_process(reg, "provenance", "--audit-json", "--informational",
                                     "publisher-changed", "npm", "axios", "1.14.0", "1.14.1")
        doc = check(self, code, "\n".join(lines))
        self.assertEqual(code, 0)
        self.assertEqual([f["severity"] for f in doc["findings"]], ["info"])


class Units(unittest.TestCase):
    def test_registry_urls(self):
        self.assertEqual(audit.registry_url("npm", "@scope/pkg", "1.0.0"),
                         "https://www.npmjs.com/package/@scope/pkg/v/1.0.0")
        self.assertEqual(audit.registry_url("pypi", "Demo_Pkg", "1.0"),
                         "https://pypi.org/project/Demo_Pkg/1.0/")

    def test_every_category_has_a_severity(self):
        for cat in ("blob", "build", "dependency", "file", "link", "native", "npm-script",
                    "provenance", "pth", "pyc-only", "startup", "url-host"):
            self.assertIn(audit.SEVERITY[cat], ("high", "medium"))

    def test_messages(self):
        cases = [
            (("npm-script", "added", "package.json", "postinstall=node x.js"),
             "New install script in package.json: postinstall=node x.js"),
            (("dependency", "added", "PKG-INFO", "Requires-Dist evil-pkg"),
             "New dependency in PKG-INFO: Requires-Dist evil-pkg"),
            (("blob", "added", "x.js", "base64 400 ab12"), "New encoded string in x.js: base64 400 ab12"),
            (("native", "changed", "a.so", "ELF"), "Changed native binary a.so: ELF"),
            (("startup", "added", "sitecustomize.py", "sitecustomize.py"),
             "New start-up module sitecustomize.py"),
            (("link", "differs", "lib/x", "../y"), "Link lib/x differs from v1: ../y"),
        ]
        for row, want in cases:
            with self.subTest(row):
                self.assertEqual(audit.message(dict(zip(("category", "status", "path", "detail"),
                                                        row)), "v1"), want)


class Docs(TempCase):
    def test_documented_example_is_real_output(self):
        old = write_tar(self.path("demo-1.0.tar.gz"),
                        {"demo-1.0/setup.py": b"setup()\n", "demo-1.0/demo.py": b"x = 1\n"})
        new = write_tar(self.path("demo-1.1.tar.gz"),
                        {"demo-1.1/setup.py": b"setup()\n", "demo-1.1/demo.py": b"x = 2\n",
                         "demo-1.1/demo_init.pth": MALICIOUS_PTH})
        _, out, _ = run_cli("diff", "--audit-json", old, new)
        got = json.loads(out)
        with open(os.path.join(ROOT, "docs", "commands.md"), encoding="utf-8") as fh:
            text = fh.read()
        block = text.split("### Audit result output", 1)[1].split("```json\n", 1)[1]
        documented = json.loads(block.split("```", 1)[0])
        # The page shows the version the flag ships in; the rest must match exactly.
        documented["tool"]["version"] = got["tool"]["version"]
        self.assertEqual(documented, got)


if __name__ == "__main__":
    unittest.main()
