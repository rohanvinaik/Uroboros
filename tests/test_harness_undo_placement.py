"""The piece U11's own tests, placed unchanged: ``admission.undo_placement`` (U11): a failed placement puts the target back as it was."""

import subprocess

from uroboros.harness.admission import undo_placement


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def make_repo(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("A = 1\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def test_a_staged_placement_is_undone(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("A = 2\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    git(repo, "add", "a.py", "tests/test_a.py")
    assert undo_placement(str(repo), ["a.py", "tests/test_a.py"], ["tests/test_a.py"], ["tests"]) == "undone"
    assert (repo / "a.py").read_text() == "A = 1\n"
    assert not (repo / "tests").exists()
    assert git(repo, "status", "--porcelain").stdout == ""


def test_an_unstaged_placement_is_undone(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "a.py").write_text("A = 3\n")
    assert undo_placement(str(repo), ["a.py"], [], []) == "undone"
    assert (repo / "a.py").read_text() == "A = 1\n"


def test_what_is_not_ours_is_left_and_the_undo_says_so(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "new.py").write_text("x = 1\n")
    (repo / "pkg" / "keep.txt").write_text("not ours\n")
    got = undo_placement(str(repo), ["pkg/new.py"], ["pkg/new.py"], ["pkg"])
    assert (repo / "pkg" / "keep.txt").exists() and not (repo / "pkg" / "new.py").exists()
    assert got == "undo_failed:dirty"


def test_a_folder_that_is_not_a_repository_fails_at_reset(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert undo_placement(str(plain), ["a.py"], [], []) == "undo_failed:reset"
