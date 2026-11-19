# SPDX-License-Identifier: Apache-2.0
"""fetch without the network: the checks that run before and after a download."""

import base64
import hashlib
import unittest
import urllib.request

from release_drift.fetch import FetchError, _get, _HttpsOnly, check_digest, fetch


class Fetch(unittest.TestCase):
    def test_digests(self):
        data = b"artifact bytes"
        check_digest(data, "sha256", hashlib.sha256(data).hexdigest().upper())
        check_digest(data, "sha512", base64.b64encode(hashlib.sha512(data).digest()).decode())
        with self.assertRaisesRegex(FetchError, "does not match"):
            check_digest(data + b"!", "sha256", hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(FetchError, "does not match"):
            check_digest(data + b"!", "sha512", base64.b64encode(hashlib.sha512(data).digest()).decode())

    def test_only_https(self):
        for url in ("http://pypi.org/x", "file:///etc/passwd", "ftp://x/y"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(FetchError, "non-https"):
                    _get(url, 10)

    def test_redirect_to_http_refused(self):
        req = urllib.request.Request("https://pypi.org/x")
        with self.assertRaisesRegex(FetchError, "non-https"):
            _HttpsOnly().redirect_request(req, None, 302, "Found", {}, "http://evil.example/x")

    def test_invalid_names_refused_before_any_request(self):
        cases = [("pypi", "../x", "1.0"), ("pypi", "x", "1.0/../../y"), ("npm", "UPPER", "1.0.0"),
                 ("npm", "@s/x/../y", "1.0.0"), ("npm", "x", "")]
        for registry, name, version in cases:
            with self.subTest(name=name, version=version):
                with self.assertRaisesRegex(FetchError, "invalid"):
                    fetch(registry, name, version, ".")
