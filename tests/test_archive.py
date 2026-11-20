# SPDX-License-Identifier: Apache-2.0
import os
import tarfile
import zipfile
from unittest import mock

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

    def test_backslash_name_is_refused_where_backslash_is_a_separator(self):
        # zipfile turns "\\" into "/" in ZipInfo.filename when os.sep is "\\"
        # (Windows); the reader must check the name as stored in the archive.
        path = write_zip(self.path("bad.whl"), {"a\\b": b"x"})
        with mock.patch.object(os, "sep", "\\"):
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


class Directories(TempCase):
    def tree(self, name, files):
        root = self.path(name)
        for rel, data in files.items():
            full = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(data)
        return root

    def test_unpacked_sdist_reads_like_the_archive(self):
        files = {"PKG-INFO": b"x", "demo/a.py": b"y"}
        packed = read_artifact(write_tar(self.path("demo-1.0.tar.gz"),
                                         {"demo-1.0/" + n: d for n, d in files.items()}))
        unpacked = read_artifact(self.tree("demo-1.0", files))
        self.assertEqual((unpacked.kind, unpacked.files), (SDIST, packed.files))

    def test_unpacked_wheel_and_npm_package(self):
        self.assertEqual(read_artifact(self.tree("w", wheel_files("1.0"))).kind, WHEEL)
        art = read_artifact(self.tree("n", {"package.json": b"{}", "index.js": b""}))
        self.assertEqual((art.kind, sorted(art.files)), (NPM, ["index.js", "package.json"]))

    def test_links_are_recorded_and_not_followed(self):
        root = self.tree("d", {"PKG-INFO": b"x", "sub/real.py": b"y"})
        outside = self.tree("outside", {"secret": b"s"})
        try:
            os.symlink("sub/real.py", os.path.join(root, "link.py"))
            os.symlink("sub", os.path.join(root, "linkdir"))
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available")
        art = read_artifact(root)
        self.assertEqual(art.links, {"link.py": "sub/real.py", "linkdir": "sub"})
        self.assertEqual(sorted(art.files), ["PKG-INFO", "sub/real.py"])
        os.symlink(outside, os.path.join(root, "escape"))
        with self.assertRaisesRegex(ArtifactError, "points outside the artifact"):
            read_artifact(root)

    def test_limits_apply_to_a_directory(self):
        root = self.tree("d", {"a": b"x" * 600, "b": b"x" * 600})
        with self.assertRaisesRegex(ArtifactError, "larger than 500 bytes"):
            read_artifact(root, limits=(10, 500, 10 ** 6))
        with self.assertRaisesRegex(ArtifactError, "more than 1 members"):
            read_artifact(root, limits=(1, 1000, 10 ** 6))

    def test_diff_accepts_a_directory(self):
        old = write_tar(self.path("demo-1.0.tar.gz"), {"demo-1.0/PKG-INFO": b"x"})
        new = self.tree("demo-1.1", {"PKG-INFO": b"x", "extra.py": b"y"})
        code, out, err = run_cli("diff", old, new)
        self.assertEqual((code, err), (0, b""))  # a plain added file is not a finding
        self.assertIn(b"file\tadded\textra.py", out)


class MacMetadata(TempCase):
    def test_metadata_file_beside_the_top_directory_is_kept_as_a_file(self):
        art = read_artifact(write_tar(self.path("demo-1.0.tar.gz"), {
            "._demo-1.0": b"meta", "demo-1.0/PKG-INFO": b"x", "demo-1.0/._a.py": b"m"}))
        self.assertEqual(art.kind, SDIST)
        self.assertEqual(art.files, {"._demo-1.0": b"meta", "PKG-INFO": b"x", "._a.py": b"m"})

    def test_other_top_level_files_are_still_refused(self):
        path = write_tar(self.path("d.tar.gz"), {"notes.txt": b"", "demo-1.0/PKG-INFO": b"x"})
        with self.assertRaisesRegex(ArtifactError, "top-level"):
            read_artifact(path)

    def test_metadata_name_already_inside_is_refused(self):
        path = write_tar(self.path("d.tar.gz"), {"._x": b"1", "demo-1.0/._x": b"2"})
        with self.assertRaisesRegex(ArtifactError, "duplicate member"):
            read_artifact(path)


class EdgeCases(TempCase):
    def test_empty_wheel_reads_with_no_files(self):
        art = read_artifact(write_zip(self.path("demo-1.0-py3-none-any.whl"), {}))
        self.assertEqual((art.kind, art.files, art.links), (WHEEL, {}, {}))

    def test_empty_sdist_is_an_error(self):
        with self.assertRaises(ArtifactError):
            read_artifact(write_tar(self.path("demo-1.0.tar.gz"), {}))

    def test_member_exactly_at_the_size_cap_is_read(self):
        path = write_zip(self.path("demo-1.0-py3-none-any.whl"), {"a": b"x" * 100, "b": b"y" * 100})
        art = read_artifact(path, limits=(2, 100, 200))
        self.assertEqual(sorted(art.files), ["a", "b"])
        for limits in ((1, 100, 200), (2, 99, 200), (2, 100, 199)):
            with self.assertRaises(ArtifactError):
                read_artifact(path, limits=limits)

    def test_unicode_member_names_are_kept(self):
        name = "demo-1.0/café/日本.py"
        art = read_artifact(write_tar(self.path("demo-1.0.tar.gz"), {name: b"x = 1\n",
                                                                       "demo-1.0/PKG-INFO": b""}))
        self.assertIn("café/日本.py", art.files)

    def test_sdist_without_setup_py_is_an_sdist(self):
        art = read_artifact(write_tar(self.path("demo-1.0.tar.gz"),
                                      {"demo-1.0/pyproject.toml": b"[project]\nname = 'demo'\n"}))
        self.assertEqual(art.kind, SDIST)

    def test_npm_package_without_scripts(self):
        art = read_artifact(write_tar(self.path("demo-1.0.0.tgz"),
                                      {"package/package.json": b'{"name": "demo"}'}))
        self.assertEqual(art.kind, NPM)
