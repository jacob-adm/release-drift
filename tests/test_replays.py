# SPDX-License-Identifier: Apache-2.0
"""Stand-ins for the 2026 supply-chain attacks release-drift was replayed against.

Each test builds two harmless artifacts with the shape of a real attack: the clean
release before it and the attacked release, with the attack's change and no harmful
code. The base64 text decodes to a print statement, the install commands are only
strings, and no host is real. Each asserts the record release-drift reported for the
real attack, and exit status 1. The attacks are described in their advisories:
LiteLLM GHSA-5mg7-485q-xm76, tensorlake GHSA-8g63-53c9-65j2, Cline GHSA-9ppg-jx86-fqw7,
axios issue 10636 (axios/axios), which also covers plain-crypto-js.
"""

import base64
import os

from fixtures import TempCase, run_cli, wheel_files, write_tar, write_zip

# A long base64 string, as the attacks used to hide their payloads; it decodes to
# a print statement.
BLOB = base64.b64encode(b"print('stand-in payload, not code from any attack')\n" * 6)


class Replays(TempCase):
    def diff(self, old, new):
        code, out, err = run_cli("diff", old, new)
        self.assertEqual((code, err), (1, b""))
        return out.decode().splitlines()

    def npm(self, name, files):
        return write_tar(self.path(name), {"package/" + n: d for n, d in files.items()})

    def test_litellm_1_82_7_encoded_code_in_an_existing_module(self):
        # The payload was base64 text added to litellm/proxy/proxy_server.py.
        module = b"def start():\n    pass\n"
        old = write_tar(self.path("litellm-1.82.6.tar.gz"), {
            "litellm-1.82.6/PKG-INFO": b"Name: litellm\n",
            "litellm-1.82.6/litellm/proxy/proxy_server.py": module})
        new = write_tar(self.path("litellm-1.82.7.tar.gz"), {
            "litellm-1.82.7/PKG-INFO": b"Name: litellm\n",
            "litellm-1.82.7/litellm/proxy/proxy_server.py":
                module + b"_p = '" + BLOB + b"'\n"})
        out = self.diff(old, new)
        self.assertTrue(any(line.startswith("blob\tadded\tlitellm/proxy/proxy_server.py\tbase64 ")
                            for line in out), out)

    def test_litellm_1_82_8_startup_hook(self):
        # A .pth file in the wheel, which Python runs at every interpreter start.
        old = write_zip(self.path("demo-1.0-py3-none-any.whl"), wheel_files("1.0"))
        new = write_zip(self.path("demo-1.1-py3-none-any.whl"), wheel_files("1.1", {
            "demo_init.pth": b"import os; print('stand-in')\n"}))
        self.assertIn("pth\tadded\tdemo_init.pth\timport", self.diff(old, new))

    def test_tensorlake_0_5_144_encoded_code_and_a_new_host(self):
        # The replayed sample held base64 blobs in lib/Math_Symbol.js and new URL hosts.
        old = self.npm("tensorlake-0.5.143.tgz", {"package.json": b'{"name": "tensorlake"}',
                                                  "lib/Math_Symbol.js": b"module.exports = 1;\n"})
        new = self.npm("tensorlake-0.5.144.tgz", {
            "package.json": b'{"name": "tensorlake"}',
            "lib/Math_Symbol.js": b"module.exports = 1;\nconst u = 'https://stand-in.invalid/x';\n"
                                  b"const p = '" + BLOB + b"';\n"})
        out = self.diff(old, new)
        self.assertIn("url-host\tadded\tlib/Math_Symbol.js\tstand-in.invalid", out)
        self.assertTrue(any(line.startswith("blob\tadded\tlib/Math_Symbol.js\tbase64 ")
                            for line in out), out)

    def test_cline_2_3_0_install_script(self):
        old = self.npm("cline-2.2.3.tgz", {"package.json": b'{"name": "cline"}'})
        new_manifest = b'{"name": "cline", "scripts": {"postinstall": "npm install -g openclaw@latest"}}'
        new = self.npm("cline-2.3.0.tgz", {"package.json": new_manifest})
        line = "npm-script\tadded\tpackage.json\tpostinstall=npm install -g openclaw@latest"
        self.assertIn(line, self.diff(old, new))
        # The same release unpacked with tar xzf, as a directory holding package/.
        unpacked = self.path("cline-2.3.0")
        os.makedirs(os.path.join(unpacked, "package"))
        with open(os.path.join(unpacked, "package", "package.json"), "wb") as fh:
            fh.write(new_manifest)
        self.assertIn(line, self.diff(old, unpacked))

    def test_plain_crypto_js_4_2_1_install_script(self):
        # A new package: compared with an empty one of the same name.
        old = self.npm("plain-crypto-js-0.0.0.tgz", {"package.json": b'{"name": "plain-crypto-js"}'})
        new = self.npm("plain-crypto-js-4.2.1.tgz", {"package.json":
                       b'{"name": "plain-crypto-js", "scripts": {"postinstall": "node setup.js"}}'})
        self.assertIn("npm-script\tadded\tpackage.json\tpostinstall=node setup.js",
                      self.diff(old, new))

    def test_axios_1_14_1_new_dependency(self):
        # axios itself changed only package.json, adding plain-crypto-js.
        old = self.npm("axios-1.14.0.tgz", {"package.json":
                       b'{"name": "axios", "dependencies": {"follow-redirects": "^1.15.6"}}'})
        new = self.npm("axios-1.14.1.tgz", {"package.json":
                       b'{"name": "axios", "dependencies": {"follow-redirects": "^1.15.6",'
                       b' "plain-crypto-js": "^4.2.1"}}'})
        self.assertIn("dependency\tadded\tpackage.json\tdependencies plain-crypto-js",
                      self.diff(old, new))
