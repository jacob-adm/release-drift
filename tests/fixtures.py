# SPDX-License-Identifier: Apache-2.0
"""Build synthetic artifacts in a temporary directory. Nothing here is real
malware: the 'malicious' contents are inert strings that are never run."""

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_zip(path, files, links=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            zf.writestr(info, files[name])
        for name, target in sorted((links or {}).items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, target)
    return path


def write_tar(path, files, links=None, special=None, hardlinks=None):
    with tarfile.open(path, "w:gz") as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, target in sorted((links or {}).items()):
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tf.addfile(info)
        for name, target in sorted((hardlinks or {}).items()):
            info = tarfile.TarInfo(name)
            info.type = tarfile.LNKTYPE
            info.linkname = target
            tf.addfile(info)
        for name, kind in sorted((special or {}).items()):
            info = tarfile.TarInfo(name)
            info.type = kind
            tf.addfile(info)
    return path


def wheel_files(version, extra=None):
    di = "demo-%s.dist-info/" % version
    files = {
        "demo/__init__.py": b"VERSION = '%s'\n" % version.encode(),
        "demo/core.py": b"def add(a, b):\n    return a + b\n",
        di + "METADATA": b"Metadata-Version: 2.1\nName: demo\nVersion: %s\n" % version.encode(),
        di + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
        di + "RECORD": b"",
    }
    files.update(extra or {})
    return files


MALICIOUS_PTH = b"import os; exec(__import__('base64').b64decode('cHJpbnQoMSk='))\n"


def run_cli(*args):
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.run([sys.executable, "-m", "release_drift"] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    return proc.returncode, proc.stdout, proc.stderr


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def path(self, name):
        return os.path.join(self.tmp, name)
