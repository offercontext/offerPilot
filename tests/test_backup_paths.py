"""Pure standard-library tests also runnable before application dependencies install."""

import stat
import unittest
from zipfile import ZipInfo

from offerpilot.backup_paths import archive_parts, validate_archive_index


class BackupPathTests(unittest.TestCase):
    def test_portable_paths(self):
        self.assertEqual(archive_parts("resumes/简历.pdf"), ("resumes", "简历.pdf"))
        validate_archive_index([ZipInfo("resumes/"), ZipInfo("resumes/file.pdf")])

    def test_unsafe_names(self):
        for name in ("", "/", "../a", "a/../b", "/a", "C:/a", "a\\b", "a//b",
                     "a/./b", "a.", "a ", "a\x00b", "CON.txt", "aux", "COM¹.txt",
                     "NUL .txt", "a:b", "x?y", "a\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                archive_parts(name)

    def test_aliases_and_parent_collisions_in_both_orders(self):
        for names in (("A", "a"), ("é", "e\u0301"), ("a", "a/b"),
                      ("a/b", "a"), ("a/", "a"), ("a", "a/")):
            with self.subTest(names=names), self.assertRaises(ValueError):
                validate_archive_index([ZipInfo(name) for name in names])

    def test_special_entries_and_encryption(self):
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK):
            entry = ZipInfo("file")
            entry.external_attr = (mode | 0o600) << 16
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_archive_index([entry])
        encrypted = ZipInfo("file")
        encrypted.flag_bits = 1
        with self.assertRaises(ValueError):
            validate_archive_index([encrypted])
        with self.assertRaises(ValueError):
            validate_archive_index([ZipInfo("file\x00hidden")])

    def test_entry_limit(self):
        with self.assertRaises(ValueError):
            validate_archive_index([ZipInfo("a"), ZipInfo("b")], max_entries=1)


if __name__ == "__main__":
    unittest.main()
