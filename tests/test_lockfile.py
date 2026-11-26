# SPDX-License-Identifier: Apache-2.0
"""lockfile: read each format, list version changes, and diff upgraded packages from
artifacts placed in --cache, so no test uses the network."""

import json
import os
import sys
import unittest

from fixtures import MALICIOUS_PTH, TempCase, run_cli, wheel_files, write_tar, write_zip

from release_drift.lockfile import changes, read_lockfile


def npm_lock(deps):
    packages = {"": {"name": "app", "version": "1.0.0"}}
    for path, version in deps.items():
        packages["node_modules/" + path] = {
            "version": version,
            "resolved": "https://registry.npmjs.org/%s/-/x-%s.tgz" % (path.split("node_modules/")[-1], version)}
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": packages})


class Formats(TempCase):
    def read(self, name, text):
        with open(self.path(name), "w") as fh:
            fh.write(text)
        return read_lockfile(self.path(name))

    def test_npm_versions_by_install_path(self):
        registry, found, skipped = self.read("a.json", npm_lock(
            {"left-pad": "1.0.0", "@s/x": "2.0.0", "a/node_modules/left-pad": "0.9.0"}))
        self.assertEqual(registry, "npm")
        self.assertEqual(sorted(found.values()), [("@s/x", "2.0.0"), ("left-pad", "0.9.0"), ("left-pad", "1.0.0")])
        self.assertEqual(skipped, [])

    def test_npm_link_and_git_are_skipped(self):
        lock = {"lockfileVersion": 3, "packages": {
            "node_modules/local": {"resolved": "../local", "link": True},
            "node_modules/g": {"version": "1.0.0", "resolved": "git+ssh://git@github.com/x/g.git#abc"}}}
        _, found, skipped = self.read("b.json", json.dumps(lock))
        self.assertEqual(found, {})
        self.assertEqual([n for n, _ in skipped], ["local", "g"])

    def test_npm_lockfile_version_1(self):
        lock = {"lockfileVersion": 1, "dependencies": {"a": {"version": "1.0.0", "dependencies": {
            "b": {"version": "2.0.0"}}}}}
        _, found, _ = self.read("c.json", json.dumps(lock))
        self.assertEqual(sorted(found.values()), [("a", "1.0.0"), ("b", "2.0.0")])

    def test_requirements_pins_and_skips(self):
        text = ("# pip-compile output\nRequests[socks]==2.31.0 \\\n    --hash=sha256:abc\n"
                "zope.interface==6.0 ; python_version >= '3.8'\n-e .\nflask>=2\n")
        registry, found, skipped = self.read("r.txt", text)
        self.assertEqual(registry, "pypi")
        self.assertEqual(found, {"requests": ("requests", "2.31.0"), "zope-interface": ("zope-interface", "6.0")})
        self.assertEqual([n for n, _ in skipped], ["-e", "flask>=2"])

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_uv_and_poetry(self):
        uv = ('version = 1\n[[package]]\nname = "app"\nversion = "0.1.0"\nsource = { editable = "." }\n'
              '[[package]]\nname = "Idna"\nversion = "3.7"\nsource = { registry = "https://pypi.org/simple" }\n')
        _, found, skipped = self.read("uv.lock", uv)
        self.assertEqual((found, [n for n, _ in skipped]), ({"idna": ("idna", "3.7")}, ["app"]))
        poetry = ('[[package]]\nname = "six"\nversion = "1.16.0"\n\n[[package]]\nname = "x"\nversion = "1"\n'
                  '[package.source]\ntype = "git"\nurl = "https://github.com/x/x"\n')
        _, found, skipped = self.read("poetry.lock", poetry)
        self.assertEqual((found, [n for n, _ in skipped]), ({"six": ("six", "1.16.0")}, ["x"]))

    def test_changes(self):
        old = {"a": ("a", "1"), "b": ("b", "1"), "c": ("c", "1")}
        new = {"a": ("a", "2"), "b": ("b", "1"), "d": ("d", "1")}
        self.assertEqual(changes(old, new), ([("a", "1", "2")], [("d", "1")], [("c", "1")]))


class Command(TempCase):
    def setUp(self):
        super().setUp()
        self.cache = self.path("cache")

    def cached(self, registry, name, version, filename, writer, files):
        directory = os.path.join(self.cache, registry, name.replace("/", "+"), version)
        os.makedirs(directory)
        writer(os.path.join(directory, filename), files)

    def locks(self, old, new, name="requirements.txt"):
        for which, text in (("old", old), ("new", new)):
            os.makedirs(self.path(which), exist_ok=True)
            with open(os.path.join(self.path(which), name), "w") as fh:
                fh.write(text)
        return os.path.join(self.path("old"), name), os.path.join(self.path("new"), name)

    def run_lock(self, old, new, *extra):
        return run_cli("lockfile", old, new, "--cache", self.cache, *extra)

    def test_pth_added_in_an_upgrade_is_a_finding(self):
        self.cached("pypi", "demo", "1.0", "demo-1.0-py3-none-any.whl", write_zip, wheel_files("1.0"))
        self.cached("pypi", "demo", "1.1", "demo-1.1-py3-none-any.whl", write_zip,
                    wheel_files("1.1", {"demo_init.pth": MALICIOUS_PTH}))
        old, new = self.locks("demo==1.0\nsix==1.16.0\n", "demo==1.1\nsix==1.16.0\nidna==3.7\n")
        code, out, err = self.run_lock(old, new, "--type", "wheel")
        lines = out.decode().splitlines()
        self.assertEqual((code, err), (1, b""))
        self.assertTrue(any(l.startswith("demo\t1.0\t1.1\tpth\tadded\tdemo_init.pth\t") for l in lines), lines)
        self.assertIn("idna\t-\t3.7\tpackage\tadded\t-\tnot compared", lines)
        self.assertFalse(any(l.startswith("six\t") for l in lines), lines)

    def test_clean_npm_upgrade_exits_0(self):
        files = {"package/package.json": b'{"name": "left-pad", "version": "%s"}',
                 "package/index.js": b"module.exports = 1\n"}
        for v in ("1.0.0", "1.0.1"):
            self.cached("npm", "left-pad", v, "left-pad-%s.tgz" % v, write_tar,
                        {k: (d % v.encode() if k.endswith(".json") else d) for k, d in files.items()})
        old, new = self.locks(npm_lock({"left-pad": "1.0.0"}), npm_lock({"left-pad": "1.0.1"}),
                              "package-lock.json")
        code, out, err = self.run_lock(old, new, "--json")
        self.assertEqual((code, err), (0, b""))
        rows = json.loads(out)
        self.assertTrue(rows and all(r["name"] == "left-pad" and r["new"] == "1.0.1" for r in rows), rows)

    def test_a_package_that_cannot_be_compared_exits_2(self):
        os.makedirs(os.path.join(self.cache, "pypi", "demo", "1.0"))
        with open(os.path.join(self.cache, "pypi", "demo", "1.0", "demo-1.0.tar.gz"), "wb") as fh:
            fh.write(b"not an archive")
        self.cached("pypi", "demo", "1.1", "demo-1.1.tar.gz", write_tar, {"demo-1.1/setup.py": b""})
        old, new = self.locks("demo==1.0\n", "demo==1.1\n")
        code, out, _ = self.run_lock(old, new)
        self.assertEqual(code, 2)
        self.assertIn(b"demo\t1.0\t1.1\tpackage\terror\t-\t", out)

    def test_mixed_formats_are_a_usage_error(self):
        old, _ = self.locks("demo==1.0\n", "demo==1.0\n")
        _, new = self.locks("{}", npm_lock({}), "package-lock.json")
        code, out, err = self.run_lock(old, new)
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"pypi lockfile and NEW a npm one", err)


if __name__ == "__main__":
    unittest.main()
