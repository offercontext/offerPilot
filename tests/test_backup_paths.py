"""Portable archive paths, with one independently recorded node per unsafe case."""

import stat
from zipfile import ZipInfo

import pytest

from offerpilot.backup_paths import archive_parts, validate_archive_index


def test_portable_paths():
    assert archive_parts("resumes/简历.pdf") == ("resumes", "简历.pdf")
    validate_archive_index([ZipInfo("resumes/"), ZipInfo("resumes/file.pdf")])


@pytest.mark.parametrize("name", [
    "", "/", "../a", "a/../b", "/a", "C:/a", "a\\b", "a//b",
    "a/./b", "a.", "a ", "a\x00b", "CON.txt", "aux", "COM¹.txt",
    "NUL .txt", "a:b", "x?y", "a\n",
])
def test_unsafe_names(name):
    with pytest.raises(ValueError):
        archive_parts(name)


@pytest.mark.parametrize("names", [
    ("A", "a"), ("é", "e\u0301"), ("a", "a/b"),
    ("a/b", "a"), ("a/", "a"), ("a", "a/"),
], ids=["case-alias", "unicode-alias", "file-parent", "parent-file", "dir-file", "file-dir"])
def test_aliases_and_parent_collisions_in_both_orders(names):
    with pytest.raises(ValueError):
        validate_archive_index([ZipInfo(name) for name in names])


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK],
                         ids=["symlink", "fifo", "character-device", "socket"])
def test_special_entries(mode):
    entry = ZipInfo("file")
    entry.external_attr = (mode | 0o600) << 16
    with pytest.raises(ValueError):
        validate_archive_index([entry])


def test_encrypted_entry():
    encrypted = ZipInfo("file")
    encrypted.flag_bits = 1
    with pytest.raises(ValueError):
        validate_archive_index([encrypted])


def test_original_name_nul():
    with pytest.raises(ValueError):
        validate_archive_index([ZipInfo("file\x00hidden")])


@pytest.mark.parametrize("name", [
    "/".join(["x"] * 33), "x" * 241, "简" * 81, "/".join(["x" * 240] * 9),
], ids=["depth", "segment-bytes", "unicode-segment-bytes", "total-bytes"])
def test_path_resource_limits(name):
    with pytest.raises(ValueError):
        archive_parts(name)


def test_maximum_path_depth():
    assert len(archive_parts("/".join(["x"] * 32))) == 32


def test_entry_limit():
    with pytest.raises(ValueError):
        validate_archive_index([ZipInfo("a"), ZipInfo("b")], max_entries=1)
