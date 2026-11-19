"""The README and the skill describe what exists; the tracked-files check works."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "check_tracked_files.py")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _tool_names() -> list[str]:
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    return [t.rsplit("__", 1)[1] for t in manifest["casa"]["provides_tools"]]


def test_readme_names_every_tool():
    readme = _read("README.md")
    names = _tool_names()
    assert len(names) == 12
    missing = [n for n in names if f"`{n}`" not in readme]
    assert missing == []


@pytest.mark.parametrize("needle", ["AGPL", "PyMuPDF 1.28.2", "0.344.0",
                                    "not a qualified electronic signature", "x86_64"])
def test_readme_states(needle):
    assert needle in _read("README.md")


def test_skill_names_the_flow_in_order():
    skill = _read("skills", "pdf-editor", "SKILL.md")
    order = ["open_pdf", "write_on_pdf", "send_pdf_to_chat", "sign_pdf"]
    positions = [skill.find(n) for n in order]
    assert -1 not in positions
    assert positions == sorted(positions)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo_with(tmp_path, files: dict[str, bytes]):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, data in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _git(repo, "add", "-A")
    return repo


def _check(cwd):
    return subprocess.run([sys.executable, SCRIPT], cwd=str(cwd),
                          capture_output=True, text=True)


def test_check_refuses_a_tracked_pdf(tmp_path):
    repo = _repo_with(tmp_path, {"tests/real.pdf": b"%PDF-1.7\n", "README.md": b"hi\n"})
    result = _check(repo)
    assert result.returncode == 1
    assert "tests/real.pdf" in result.stdout + result.stderr


def test_check_refuses_a_png_renamed_to_text(tmp_path):
    repo = _repo_with(tmp_path, {"notes.txt": PNG})
    result = _check(repo)
    assert result.returncode == 1
    assert "notes.txt" in result.stdout + result.stderr


def test_check_refuses_a_jpeg_by_its_bytes_and_an_upper_case_extension(tmp_path):
    repo = _repo_with(tmp_path, {"a.bin": b"\xff\xd8\xff\xe0" + b"\x00" * 8,
                                 "Scan.HEIC": b"anything"})
    result = _check(repo)
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "a.bin" in out and "Scan.HEIC" in out


def test_check_allows_fonts_and_vendor(tmp_path):
    repo = _repo_with(tmp_path, {"server/fonts/x.png": PNG,
                                 "server/vendor/pkg/doc.pdf": b"%PDF-1.4\n",
                                 "README.md": b"hi\n"})
    assert _check(repo).returncode == 0


def test_check_runs_from_a_subdirectory(tmp_path):
    repo = _repo_with(tmp_path, {"tests/real.pdf": b"%PDF-1.7\n", "server/a.py": b"x = 1\n"})
    assert _check(repo / "server").returncode == 1


def test_this_repo_is_clean():
    if shutil.which("git") is None or not os.path.exists(os.path.join(ROOT, ".git")):
        pytest.skip("not a git checkout")
    result = _check(ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
