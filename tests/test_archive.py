# SPDX-License-Identifier: Apache-2.0
import tarfile
import zipfile

from fixtures import TempCase, run_cli, wheel_files, write_tar, write_zip
from release_drift.archive import NPM, SDIST, WHEEL, ArtifactError, read_artifact


class ReadArtifact(TempCase):
    def test_wheel_is_read_in_memory_with_its_paths(self):
        art = read_artifact(write_zip(self.path("demo-1.0-py3-none-any.whl"), wheel_files("1.0")))
        self.assertEqual(art.kind, WHEEL)
        self.assertEqual(art.files["demo/core.py"], b"def add(a, b):\n    return a + b\n")

    def test_sdist_top_directory_is_removed(self):
        art = read_artifact(write_tar(self.path("demo-1.0.tar.gz"),
                                      {"demo-1.0/PKG-INFO": b"x", "demo-1.0/demo/a.py": b"y"}))
        self.assertEqual(art.kind, SDIST)
        self.assertEqual(sorted(art.files), ["PKG-INFO", "demo/a.py"])

    def test_npm_tarball_package_directory_is_removed(self):
        art = read_artifact(write_tar(self.path("demo-1.0.0.tgz"),
                                      {"package/package.json": b"{}", "package/index.js": b""}))
        self.assertEqual(art.kind, NPM)
        self.assertEqual(sorted(art.files), ["index.js", "package.json"])

    def test_unsafe_names_are_refused(self):
        for name in ("/etc/passwd", "a/../../x", "..", "C:/x", "a\\b"):
            with self.subTest(name=name):
                path = write_zip(self.path("bad.whl"), {name: b"x"})
                with self.assertRaises(ArtifactError):
                    read_artifact(path)

    def test_link_inside_the_artifact_is_kept(self):
        art = read_artifact(write_tar(self.path("d.tar.gz"), {"d/PKG-INFO": b""},
                                      links={"d/a/link": "../PKG-INFO"}))
        self.assertEqual(art.links, {"a/link": "../PKG-INFO"})

    def test_links_escaping_the_artifact_are_refused(self):
        for target in ("/etc/passwd", "../../outside", "../../../x"):
            with self.subTest(target=target):
                path = write_tar(self.path("d.tar.gz"), {"d/PKG-INFO": b""},
                                 links={"d/link": target})
                with self.assertRaises(ArtifactError):
                    read_artifact(path)

    def test_zip_symlink_escaping_is_refused(self):
        path = write_zip(self.path("d.whl"), {"a.py": b""}, links={"evil": "../../etc/passwd"})
        with self.assertRaises(ArtifactError):
            read_artifact(path)

    def test_hard_link_to_member_is_relative_link(self):
        art = read_artifact(write_tar(self.path("d.tar.gz"), {"d/PKG-INFO": b"x"},
                                      hardlinks={"d/sub/copy": "d/PKG-INFO"}))
        self.assertEqual(art.links, {"sub/copy": "../PKG-INFO"})

    def test_device_and_fifo_members_are_refused(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                path = write_tar(self.path("d.tar.gz"), {"d/PKG-INFO": b""},
                                 special={"d/dev": kind})
                with self.assertRaises(ArtifactError):
                    read_artifact(path)

    def test_duplicate_members_are_refused(self):
        path = self.path("dup.whl")
        write_zip(path, {"a.py": b"1"})
        import zipfile
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with zipfile.ZipFile(path, "a") as zf:
                zf.writestr("a.py", b"2")
        with self.assertRaisesRegex(ArtifactError, "duplicate"):
            read_artifact(path)

    def test_size_caps_are_enforced_while_reading(self):
        path = write_zip(self.path("big.whl"), {"a": b"x" * 1000, "b": b"y" * 1000})
        with self.assertRaisesRegex(ArtifactError, "larger than 999 bytes"):
            read_artifact(path, limits=(10, 999, 10 ** 6))
        with self.assertRaisesRegex(ArtifactError, "uncompressed"):
            read_artifact(path, limits=(10, 1000, 1500))
        with self.assertRaisesRegex(ArtifactError, "more than 1 members"):
            read_artifact(path, limits=(1, 1000, 10 ** 6))

    def test_two_top_directories_are_refused(self):
        path = write_tar(self.path("d.tar.gz"), {"a/x": b"", "b/y": b""})
        with self.assertRaisesRegex(ArtifactError, "top-level"):
            read_artifact(path)

    def test_corrupt_compressed_member_is_an_error(self):
        path = self.path("demo-1.0-py3-none-any.whl")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("demo/core.py", b"x = 1\n" * 2000, compress_type=zipfile.ZIP_DEFLATED)
        with open(path, "rb") as fh:
            raw = bytearray(fh.read())
        start = 30 + len("demo/core.py")
        for i in range(start, start + 8):
            raw[i] ^= 0xFF
        with open(path, "wb") as fh:
            fh.write(raw)
        with self.assertRaises(ArtifactError):
            read_artifact(path)
        code, out, err = run_cli("diff", path, path)
        self.assertEqual((code, out), (2, b""))
        self.assertNotIn(b"Traceback", err)

    def test_garbage_is_an_error(self):
        path = self.path("x.tar.gz")
        with open(path, "wb") as fh:
            fh.write(b"not an archive")
        with self.assertRaises(ArtifactError):
            read_artifact(path)
