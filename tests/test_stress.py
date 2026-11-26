# SPDX-License-Identifier: Apache-2.0
"""The stress benchmark classes each run by how it really ended."""

import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmark"))

from stress import classify, run_one  # noqa: E402


class Classify(unittest.TestCase):
    def test_findings_with_json_output_are_ok(self):
        self.assertEqual(classify(1, "[]", ""), ("ok", ""))

    def test_error_message_with_status_2_is_a_refusal(self):
        self.assertEqual(classify(2, "", "release-drift: error: duplicate member 'a'\n"),
                         ("refused", "release-drift: error: duplicate member 'a'"))

    def test_traceback_is_a_crash_whatever_the_status(self):
        err = "Traceback (most recent call last):\n  ...\nKeyError: 'x'\n"
        self.assertEqual(classify(1, "[]", err), ("crash", "KeyError: 'x'"))

    def test_non_json_output_is_a_crash(self):
        self.assertEqual(classify(0, "half a record", ""), ("crash", "output is not JSON"))

    def test_unexpected_status_is_a_crash(self):
        self.assertEqual(classify(-9, "", ""), ("crash", "exit status -9"))


class RunOne(unittest.TestCase):
    def test_real_wheels_compare_ok_and_garbage_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for version in ("1.0", "1.1"):
                path = os.path.join(tmp, "demo-%s-py3-none-any.whl" % version)
                with zipfile.ZipFile(path, "w") as zf:
                    zf.writestr("demo/__init__.py", "VERSION = %r\n" % version)
                paths.append(path)
            self.assertEqual(run_one(paths[0], paths[1], 60)[0], "ok")
            bad = os.path.join(tmp, "demo-2.0.tar.gz")
            with open(bad, "wb") as fh:
                fh.write(b"not an archive")
            self.assertEqual(run_one(paths[0], bad, 60)[0], "refused")


if __name__ == "__main__":
    unittest.main()
