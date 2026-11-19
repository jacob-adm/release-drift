# SPDX-License-Identifier: Apache-2.0
import json
import os
import subprocess

from fixtures import MALICIOUS_PTH, TempCase, run_cli, wheel_files, write_tar, write_zip
from release_drift.archive import read_artifact
from release_drift.compare import diff, normalize, source


class Diff(TempCase):
    def wheels(self, extra_new):
        old = write_zip(self.path("demo-1.0-py3-none-any.whl"), wheel_files("1.0"))
        new_files = wheel_files("1.1", extra_new)
        new = write_zip(self.path("demo-1.1-py3-none-any.whl"), new_files)
        return old, new

    def test_versioned_dist_info_lines_up(self):
        self.assertEqual(normalize("demo-1.0.dist-info/RECORD"), "demo.dist-info/RECORD")
        self.assertEqual(normalize("demo_x-2.0rc1.data/scripts/a"), "demo_x.data/scripts/a")
        self.assertEqual(normalize("demo/x-1.dist-info"), "demo/x-1.dist-info")

    def test_new_startup_hook_is_reported(self):
        old, new = self.wheels({"demo_init.pth": MALICIOUS_PTH})
        records = diff(read_artifact(old), read_artifact(new))
        self.assertIn(("pth", "added", "demo_init.pth", "import"), records)
        self.assertIn(("file", "added", "demo_init.pth", "-"), records)
        self.assertIn(("file", "changed", "demo-1.1.dist-info/METADATA", "-"), records)
        self.assertIn(("file", "changed", "demo/__init__.py", "-"), records)
        self.assertEqual([r for r in records if r[0] == "blob"], [])

    def test_changed_surface_and_new_host(self):
        old = write_tar(self.path("demo-1.0.tar.gz"),
                        {"demo-1.0/PKG-INFO": b"1", "demo-1.0/setup.py": b"setup()\n"})
        new = write_tar(self.path("demo-1.1.tar.gz"),
                        {"demo-1.1/PKG-INFO": b"2",
                         "demo-1.1/setup.py": b"fetch('https://drop.example/x')\nsetup()\n"})
        records = [r for r in diff(read_artifact(old), read_artifact(new)) if r[0] != "file"]
        self.assertEqual(records, [
            ("build", "changed", "setup.py", "setup.py"),
            ("url-host", "added", "setup.py", "drop.example"),
        ])

    def test_known_host_is_not_new(self):
        old = write_zip(self.path("a.whl"), {"a.py": b"'https://docs.example/'"})
        new = write_zip(self.path("b.whl"), {"b.py": b"'https://docs.example/other'"})
        records = diff(read_artifact(old), read_artifact(new))
        self.assertEqual(records, [("file", "added", "b.py", "-"), ("file", "removed", "a.py", "-")])

    def test_non_ascii_host_is_reported_whole(self):
        old = write_zip(self.path("a.whl"), {"a.py": b"x = 1\n"})
        new = write_zip(self.path("b.whl"), {"a.py": "u = 'https://ex\u00e4mple.com/'\n".encode("utf-8")})
        code, out, _ = run_cli("diff", old, new)
        self.assertEqual(code, 1)
        self.assertIn("url-host\tadded\ta.py\tex\u00e4mple.com\n".encode("utf-8"), out)
        code, out, _ = run_cli("diff", "--json", old, new)
        self.assertIn(b'"detail": "ex\\u00e4mple.com"', out)

    def test_npm_preinstall_added(self):
        old = write_tar(self.path("demo-1.0.0.tgz"),
                        {"package/package.json": b'{"version": "1.0.0", "scripts": {"test": "t"}}'})
        new = write_tar(self.path("demo-1.0.1.tgz"),
                        {"package/package.json":
                         b'{"version": "1.0.1", "scripts": {"test": "t", "preinstall": "node setup.js"}}',
                         "package/setup.js": b"require('child_process')\n"})
        code, out, err = run_cli("diff", old, new)
        self.assertEqual((code, err), (1, b""))
        self.assertEqual(out.decode().splitlines(), [
            "file\tadded\tsetup.js\t-",
            "file\tchanged\tpackage.json\t-",
            "npm-script\tadded\tpackage.json\tpreinstall=node setup.js",
        ])

    def test_file_changes_without_findings_exit_0(self):
        old, new = self.wheels({"demo/extra.py": b"def sub(a, b):\n    return a - b\n"})
        code, out, err = run_cli("diff", old, new)
        self.assertEqual((code, err), (0, b""))
        self.assertIn(b"file\tadded\tdemo/extra.py\t-\n", out)
        self.assertTrue(all(line.startswith(b"file\t") for line in out.splitlines()))

    def test_identical_releases_exit_0_with_no_output(self):
        old, _ = self.wheels({})
        self.assertEqual(run_cli("diff", old, old), (0, b"", b""))

    def test_output_is_sorted_and_byte_identical(self):
        old, new = self.wheels({"demo_init.pth": MALICIOUS_PTH, "z.py": b"'https://a.example'",
                                "a\tb.py": b""})
        first = run_cli("diff", old, new)
        second = run_cli("diff", old, new)
        self.assertEqual(first, second)
        self.assertEqual(first[0], 1)
        lines = first[1].split(b"\n")[:-1]
        self.assertEqual(lines, sorted(lines))
        self.assertIn(b"file\tadded\ta\\tb.py\t-", lines)
        self.assertTrue(all(line.count(b"\t") == 3 for line in lines))

    def test_json_output(self):
        old, new = self.wheels({"demo_init.pth": MALICIOUS_PTH})
        code, out, _ = run_cli("diff", "--json", old, new)
        self.assertEqual(code, 1)
        rows = json.loads(out.decode())
        self.assertIn({"category": "file", "status": "added", "path": "demo_init.pth", "detail": "-"}, rows)
        self.assertEqual([r for r in rows if r["category"] != "file"], [
            {"category": "pth", "status": "added", "path": "demo_init.pth", "detail": "import"}])

    def test_unreadable_artifact_exits_2(self):
        bad = write_zip(self.path("bad.whl"), {"../x": b""})
        code, out, err = run_cli("diff", bad, bad)
        self.assertEqual((code, out), (2, b""))
        self.assertIn(b"release-drift: error:", err)
        code, _, _ = run_cli("diff", self.path("missing.whl"), bad)
        self.assertEqual(code, 2)


class Source(TempCase):
    def git(self, *args):
        hooks = self.path("nohooks")
        os.makedirs(hooks, exist_ok=True)
        return subprocess.run(
            ["git", "-C", self.repo, "-c", "core.hooksPath=" + hooks, "-c", "commit.gpgsign=false",
             "-c", "tag.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            + list(args), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def setUp(self):
        super().setUp()
        self.repo = self.path("repo")
        os.makedirs(self.repo)
        self.tree = {
            "setup.cfg": b"# comment\n[metadata]\nname=demo\n",
            "pyproject.toml": b"[build-system]\nbuild-backend = \"setuptools.build_meta\"\n",
            "src/demo/__init__.py": b"VERSION = '1.0'\n",
            "src/demo/core.py": b"def add(a, b):\n    return a + b\n",
            "LICENSE": b"licence text\n",
            "package.json": b'{\n  "name": "demo",\n  "version": "1.0.0"\n}\n',
            "index.js": b"module.exports = 1\n",
        }
        for name, data in self.tree.items():
            full = os.path.join(self.repo, *name.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(data)
        self.git("init", "-q")
        self.git("add", "--", *sorted(self.tree))
        self.git("commit", "-q", "-m", "v1")
        self.git("tag", "v1.0")

    def run_source(self, artifact, *extra):
        return run_cli("source", artifact, "--repo", self.repo, "--ref", "v1.0", *extra)

    def test_clean_sdist_with_generated_files_exits_0(self):
        files = {"demo-1.0/" + k: v for k, v in self.tree.items()}
        # setuptools rewrites it: comments dropped, "key = value", [egg_info] added.
        files["demo-1.0/setup.cfg"] = b"[metadata]\nname = demo\n\n[egg_info]\ntag_build = \ntag_date = 0\n\n"
        files["demo-1.0/PKG-INFO"] = b"Metadata-Version: 2.1\n"
        files["demo-1.0/src/demo.egg-info/SOURCES.txt"] = b"x\n"
        files["demo-1.0/src/demo.egg-info/PKG-INFO"] = b"x\n"
        sdist = write_tar(self.path("demo-1.0.tar.gz"), files)
        self.assertEqual(self.run_source(sdist), (0, b"", b""))

    def test_extra_and_changed_files_are_reported_with_their_surfaces(self):
        files = {"demo-1.0/" + k: v for k, v in self.tree.items()}
        files["demo-1.0/PKG-INFO"] = b""
        files["demo-1.0/src/demo_init.pth"] = MALICIOUS_PTH
        files["demo-1.0/src/demo/core.py"] = b"import urllib\nurllib.urlopen('https://x.example')\n"
        files["demo-1.0/src/demo.egg-info/evil.txt"] = b""
        files["demo-1.0/setup.cfg"] = b"[metadata]\nname = demo2\n\n[egg_info]\ntag_build = \n"
        sdist = write_tar(self.path("demo-1.0.tar.gz"), files)
        code, out, _ = self.run_source(sdist)
        self.assertEqual(code, 1)
        self.assertEqual(out.decode().splitlines(), [
            "file\tdiffers\tsetup.cfg\t-",
            "file\tdiffers\tsrc/demo/core.py\t-",
            "file\tnot-in-tag\tsrc/demo.egg-info/evil.txt\t-",
            "file\tnot-in-tag\tsrc/demo_init.pth\t-",
            "pth\tnot-in-tag\tsrc/demo_init.pth\timport",
            "url-host\tdiffers\tsrc/demo/core.py\tx.example",
        ])

    def test_wheel_src_layout_and_generated_metadata(self):
        whl = write_zip(self.path("demo-1.0-py3-none-any.whl"), {
            "demo/__init__.py": self.tree["src/demo/__init__.py"],
            "demo/core.py": self.tree["src/demo/core.py"],
            "demo-1.0.dist-info/METADATA": b"m",
            "demo-1.0.dist-info/RECORD": b"r",
            "demo-1.0.dist-info/WHEEL": b"w",
            "demo-1.0.dist-info/licenses/LICENSE": self.tree["LICENSE"],
        })
        self.assertEqual(self.run_source(whl), (0, b"", b""))
        whl2 = write_zip(self.path("demo2-1.0-py3-none-any.whl"), {
            "demo/__init__.py": self.tree["src/demo/__init__.py"],
            "demo-1.0.dist-info/licenses/LICENSE": b"different\n",
            "demo-1.0.dist-info/RECORD.jws": b"sig",
        })
        # A file that is not in the tag is a finding on its own.
        code, out, _ = self.run_source(whl2)
        self.assertEqual(code, 1)
        self.assertEqual(out.decode().splitlines(), [
            "file\tnot-in-tag\tdemo-1.0.dist-info/RECORD.jws\t-",
            "file\tnot-in-tag\tdemo-1.0.dist-info/licenses/LICENSE\t-",
        ])

    def test_npm_package_json_normalisation(self):
        manifest = b'{"version":"1.0.0","name":"demo","gitHead":"abc","_id":"demo@1.0.0"}'
        tgz = write_tar(self.path("demo-1.0.0.tgz"),
                        {"package/package.json": manifest, "package/index.js": self.tree["index.js"]})
        self.assertEqual(self.run_source(tgz), (0, b"", b""))
        changed = b'{"version":"1.0.0","name":"demo","scripts":{"postinstall":"node x"}}'
        tgz = write_tar(self.path("demo-1.0.0b.tgz"), {"package/package.json": changed})
        code, out, _ = self.run_source(tgz)
        self.assertEqual(out.decode().splitlines(), [
            "file\tdiffers\tpackage.json\t-",
            "npm-script\tdiffers\tpackage.json\tpostinstall=node x",
        ])

    def test_prefix_maps_artifact_root_into_repository(self):
        whl = write_zip(self.path("demo-1.0-py3-none-any.whl"),
                        {"demo/core.py": self.tree["src/demo/core.py"]})
        code, out, _ = self.run_source(whl, "--prefix", "")
        self.assertEqual(out, b"file\tnot-in-tag\tdemo/core.py\t-\n")
        self.assertEqual(self.run_source(whl, "--prefix", "src")[0], 0)

    def test_missing_ref_and_option_like_ref_exit_2(self):
        whl = write_zip(self.path("demo-1.0-py3-none-any.whl"), {"a.py": b""})
        for ref in ("v9.9", "--output=x"):
            with self.subTest(ref=ref):
                code, out, err = run_cli("source", whl, "--repo", self.repo, "--ref=" + ref)
                self.assertEqual((code, out), (2, b""))
                self.assertIn(b"release-drift: error:", err)

    def test_sdist_symlink_compared_with_git_symlink(self):
        files = {"demo-1.0/" + k: v for k, v in self.tree.items()}
        files["demo-1.0/PKG-INFO"] = b""
        sdist = write_tar(self.path("demo-1.0.tar.gz"), files, links={"demo-1.0/alias.py": "index.js"})
        code, out, _ = self.run_source(sdist)
        self.assertEqual(out.decode().splitlines(), [
            "file\tnot-in-tag\talias.py\t-",
            "link\tnot-in-tag\talias.py\tindex.js",
        ])

    def test_files_under_a_git_directory_symlink_match_its_target(self):
        # alias -> src/demo, added through the index so no OS symlink is needed.
        oid = subprocess.run(["git", "-C", self.repo, "hash-object", "-w", "--stdin"],
                             input=b"src/demo", stdout=subprocess.PIPE, check=True).stdout
        self.git("update-index", "--add", "--cacheinfo", "120000,%s,alias" % oid.decode().strip())
        self.git("commit", "-q", "-m", "v1.1")
        self.git("tag", "v1.1")
        files = {"demo-1.1/" + k: v for k, v in self.tree.items()}
        files["demo-1.1/PKG-INFO"] = b""
        files["demo-1.1/alias/core.py"] = self.tree["src/demo/core.py"]
        files["demo-1.1/alias/__init__.py"] = b"VERSION = '9.9'\n"
        sdist = write_tar(self.path("demo-1.1.tar.gz"), files)
        code, out, _ = run_cli("source", sdist, "--repo", self.repo, "--ref", "v1.1")
        self.assertEqual((code, out.decode().splitlines()), (1, ["file\tdiffers\talias/__init__.py\t-"]))

class EdgeCaseDiffs(TempCase):
    def test_npm_scripts_without_install_hooks_are_not_findings(self):
        old = write_tar(self.path("demo-1.0.0.tgz"), {"package/package.json": b'{"name": "demo"}'})
        new = write_tar(self.path("demo-1.0.1.tgz"),
                        {"package/package.json": b'{"name": "demo", "scripts": {"test": "x"}}'})
        self.assertEqual(run_cli("diff", old, new), (0, b"file\tchanged\tpackage.json\t-\n", b""))

    def test_unicode_paths_are_printed_as_utf8(self):
        old = write_tar(self.path("demo-1.0.tar.gz"), {"demo-1.0/PKG-INFO": b""})
        new = write_tar(self.path("demo-1.1.tar.gz"),
                        {"demo-1.1/PKG-INFO": b"", "demo-1.1/café/x.pth": b"import os\n"})
        code, out, _ = run_cli("diff", old, new)
        self.assertEqual((code, out.decode("utf-8").splitlines()), (1, [
            "file\tadded\tcafé/x.pth\t-", "pth\tadded\tcafé/x.pth\timport"]))

    def test_build_backend_change_in_pyproject_only_sdist(self):
        toml = b'[build-system]\nbuild-backend = "%s"\n'
        old = write_tar(self.path("demo-1.0.tar.gz"),
                        {"demo-1.0/pyproject.toml": toml % b"hatchling.build"})
        new = write_tar(self.path("demo-1.1.tar.gz"),
                        {"demo-1.1/pyproject.toml": toml % b"local_backend",
                         "demo-1.1/local_backend.py": b"def build_wheel(*a): pass\n"})
        code, out, _ = run_cli("diff", old, new)
        self.assertEqual(code, 1)
        self.assertIn(b'build\tadded\tpyproject.toml\tbuild-backend = "local_backend"\n', out)
