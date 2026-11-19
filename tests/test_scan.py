# SPDX-License-Identifier: Apache-2.0
import base64
import unittest

from release_drift.archive import NPM, SDIST, WHEEL, Artifact
from release_drift.scan import native_format, pth_detail, pyproject_findings, strings, surfaces


def art(kind, files, links=None):
    return Artifact("x", kind, files, links or {})


def cats(findings):
    return sorted((c, p, d) for c, p, d, _ in findings)


class Surfaces(unittest.TestCase):
    def test_pth_with_import_line_is_executable(self):
        self.assertEqual(pth_detail(b"# c\nimport os; os.system('x')\n"), "import")
        self.assertEqual(pth_detail(b"import\tos\n"), "import")
        self.assertEqual(pth_detail(b"/opt/lib\n  import os\nimportlib\n"), "paths")

    def test_wheel_surfaces(self):
        found = cats(surfaces(art(WHEEL, {
            "evil.pth": b"import x\n",
            "sitecustomize.py": b"",
            "pkg/__pycache__/m.cpython-312.pyc": b"",
            "pkg/n.pyc": b"",
            "pkg/n2.py": b"",
            "pkg/n2.pyc": b"",
            "pkg/lib.so": b"\x7fELF\x02\x01\x01",
            "setup.py": b"",  # not a build script in a wheel
        })))
        self.assertEqual(found, [
            ("native", "pkg/lib.so", "ELF"),
            ("pth", "evil.pth", "import"),
            ("pyc-only", "pkg/__pycache__/m.cpython-312.pyc", "no .py source"),
            ("pyc-only", "pkg/n.pyc", "no .py source"),
            ("startup", "sitecustomize.py", "sitecustomize.py"),
        ])

    def test_sdist_build_surfaces(self):
        pyproject = (b'[build-system]\nrequires = ["x"]\nbuild-backend = "backend"\n'
                     b'backend-path = ["."]\n\n[tool.hatch.build.hooks.custom]\npath = "h.py"\n'
                     b'[tool.poetry.build]\nscript = "build.py"\n')
        found = cats(surfaces(art(SDIST, {"setup.py": b"", "pyproject.toml": pyproject,
                                          "sub/setup.py": b""})))
        self.assertEqual(found, [
            ("build", "pyproject.toml", "[tool.hatch.build.hooks.custom]"),
            ("build", "pyproject.toml", "backend-path = [\".\"]"),
            ("build", "pyproject.toml", "build-backend = \"backend\""),
            ("build", "pyproject.toml", "tool.poetry.build: script = \"build.py\""),
            ("build", "setup.py", "setup.py"),
        ])

    def test_pyproject_without_build_system(self):
        self.assertEqual(pyproject_findings(b"[project]\nname = 'x'\n"), [])

    def test_npm_install_scripts_root_and_bundled(self):
        pkg = b'{"scripts": {"preinstall": "node evil.js", "test": "x", "prepare": "tsc"}}'
        found = cats(surfaces(art(NPM, {
            "package.json": pkg,
            "node_modules/dep/package.json": b'{"scripts": {"postinstall": "node p.js"}}',
            "node_modules/@s/dep/package.json": b'{"scripts": {"install": "make"}}',
            "test/fixture/package.json": b'{"scripts": {"install": "ignored"}}',
            "binding.gyp": b"{}",
        })))
        self.assertEqual(found, [
            ("npm-script", "binding.gyp", "install=node-gyp rebuild (implicit)"),
            ("npm-script", "node_modules/@s/dep/package.json", "install=make"),
            ("npm-script", "node_modules/dep/package.json", "postinstall=node p.js"),
            ("npm-script", "package.json", "preinstall=node evil.js"),
            ("npm-script", "package.json", "prepare=tsc"),
        ])

    def test_broken_package_json_is_reported(self):
        found = cats(surfaces(art(NPM, {"package.json": b"{"})))
        self.assertEqual(found, [("npm-script", "package.json", "package.json does not parse")])

    def test_native_formats(self):
        pe = bytearray(b"MZ" + b"\x00" * 0x3E + b"PE\x00\x00")
        pe[0x3C:0x40] = (0x40).to_bytes(4, "little")
        self.assertEqual(native_format(bytes(pe)), "PE")
        self.assertEqual(native_format(b"MZ just text" + b" " * 80), None)
        self.assertEqual(native_format(b"\xcf\xfa\xed\xfe...."), "Mach-O")
        self.assertEqual(native_format(b"\xca\xfe\xba\xbe\x00\x00\x00\x02"), "Mach-O")
        self.assertEqual(native_format(b"\xca\xfe\xba\xbe\x00\x00\x00\x34"), None)  # Java class
        self.assertEqual(native_format(b"\x00asm\x01\x00\x00\x00"), "WebAssembly")
        self.assertEqual(native_format(b"print(1)"), None)

    def test_links_are_listed(self):
        self.assertEqual(cats(surfaces(art(SDIST, {}, {"a": "b"}))), [("link", "a", "b")])


class Strings(unittest.TestCase):
    def test_hosts_and_blobs(self):
        blob = base64.b64encode(bytes(range(256)) * 2)
        hexblob = b"ab12" * 60
        found = strings(art(WHEEL, {
            "a.py": b"u = 'https://Evil.Example:8443/x'; v = 'http://ok.test'\n",
            "b.py": b"x = '" + blob + b"'\ny = '" + hexblob + b"'\nshort = 'QUJD'\n",
        }))
        hosts = [f for f in found if f[0] == "url-host"]
        self.assertEqual(hosts, [("url-host", "a.py", "evil.example:8443"),
                                 ("url-host", "a.py", "ok.test")])
        blobs = sorted(f[2].split(" ")[0] + " " + f[2].split(" ")[1] for f in found if f[0] == "blob")
        self.assertEqual(blobs, ["base64 len=%d" % len(blob), "hex len=240"])

    def test_non_ascii_hosts_are_kept_whole(self):
        text = ("a = 'https://ex\u00e4mple.com/'\n"
                "b = 'https://\u00c4BC.Example:8080/'\n"
                "c = 'http://\u0645\u062b\u0627\u0644.test'\n"
                "d = 'https://cafe\u0301.test'\n"
                "e = \u201chttps://quoted.test\u201d\n"
                "f = 'https://bidi\u202etxt.test'\n"
                "g = 'https://c1\u0085.test'\n")
        found = strings(art(WHEEL, {"a.py": text.encode("utf-8"),
                                    "b.py": b"https://bad\xff.test caf\xc3"}))
        hosts = sorted(f[2] for f in found if f[0] == "url-host")
        self.assertEqual(hosts, ["bad", "bidi", "c1", "cafe\u0301.test",
                                 "ex\u00e4mple.com", "quoted.test", "\u00c4bc.example:8080",
                                 "\u0645\u062b\u0627\u0644.test"])
