import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from core.skill_install import prepare_source, validate_public_https_url
from core.skill_package import package_digest
from skills.skill_builder import impl as builder


def package(root, name, config=False):
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Useful task\n---\nDo the requested task.\n", encoding="utf-8")
    if config:
        (path / "skill.json").write_text(json.dumps({"config_fields": [{"name": "TOKEN", "kind": "secret", "required": True}]}), encoding="utf-8")
    return path


def test_zip_batch_paths_and_template_export_roundtrip(tmp_path):
    import zipfile
    from docx import Document
    from core.skill_manager import SkillManager
    original = tmp_path / "reference.docx"
    doc = Document(); doc.add_heading("{{TITLE}}", 0); doc.add_paragraph("Keep structure."); doc.save(original)
    data = tmp_path / "data"
    with patch.object(builder, "get_app_data_dir", return_value=str(data)):
        result = builder.create_new_skill(str(tmp_path), "template", "Reusable report",
            instructions_md="Copy [reference](assets/reference.docx), replace {{TITLE}} and verify.",
            resources=[{"kind": "asset", "path": "assets/reference.docx", "source_path": str(original)}],
            _context={"skill_change_publisher": lambda _: None})
    assert result.startswith("Success")
    manager = SkillManager.__new__(SkillManager)
    manager._find_skill_path = lambda name: str(data / "ai_skills" / name)
    exported = tmp_path / "template.zip"
    result = manager.export_skill("template", str(exported))
    assert result[0], result[1]
    with patch.object(builder, "get_app_data_dir", return_value=str(tmp_path / "reinstalled")):
        result = builder.install_agent_skill(str(exported), _context={"skill_change_publisher": lambda _: None})
    assert result.startswith("Success")
    retained = tmp_path / "reinstalled/ai_skills/template/assets/reference.docx"
    assert retained.read_bytes() == original.read_bytes()
    reused = Document(retained); reused.paragraphs[0].text = "New report"; reused.save(tmp_path / "new.docx")
    assert Document(tmp_path / "new.docx").paragraphs[0].text == "New report"
    assert Document(retained).paragraphs[0].text == "{{TITLE}}"
    root = tmp_path / "source"
    package(root, "one"); package(root, "two")
    archive = tmp_path / "batch.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in root.rglob("*"):
            if path.is_file(): handle.write(path, path.relative_to(root))
    with prepare_source(str(archive), skill_paths=["one", "two"]) as entries:
        assert [path.name for path, _ in entries] == ["one", "two"]


def test_git_cancellation_stops_actual_process(tmp_path):
    import subprocess
    import sys
    from core.skill_install import _git
    processes = []
    def launch(command, **kwargs):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(process)
        return process
    with patch("core.process_utils.popen_external_program", side_effect=launch):
        with pytest.raises(RuntimeError, match="cancelled"):
            _git(["init"], tmp_path, {"abort_check": lambda: bool(processes)})
    assert len(processes) == 1 and processes[0].poll() is not None


def test_remote_ref_batch_installs_one_download_with_provenance(tmp_path):
    repo = tmp_path / "repository"
    package(repo, "first", config=True)
    package(repo, "second")
    calls, events = [], []
    def git(args, directory, context):
        calls.append(args)
        if args[0] == "checkout":
            shutil.copytree(repo, directory, dirs_exist_ok=True)
        return "a" * 40 if args[0] == "rev-parse" else ""
    with patch.object(builder, "get_app_data_dir", return_value=str(tmp_path / "data")), patch("core.skill_install.validate_public_https_url", side_effect=lambda value: value), patch("core.skill_install._git", side_effect=git):
        result = builder.install_agent_skill("https://github.com/owner/repository", skill_paths=["first", "second"], ref="release/v2", _context={"skill_change_publisher": events.append})
    assert result.startswith("Success:") and "TOKEN" in result
    fetches = [call for call in calls if call[0] == "fetch"]
    assert len(fetches) == 1 and fetches[0][-1] == "release/v2"
    assert events[0]["skill_names"] == ["first", "second"]
    metadata = json.loads((tmp_path / "data/ai_skills/first/skill.json").read_text())
    assert metadata["install_source"]["commit"] == "a" * 40
    assert metadata["config_fields"][0]["required"] is True
    assert not (tmp_path / "data/skill_install_plans").exists()


def test_inspected_copy_changes_and_conflicts_do_not_install(tmp_path):
    source = package(tmp_path / "source", "sample")
    digest = package_digest(source)
    (source / "SKILL.md").write_text((source / "SKILL.md").read_text() + "Changed.")
    with patch.object(builder, "get_app_data_dir", return_value=str(tmp_path / "data")):
        result = builder.install_agent_skill(str(source), expected_sha256=digest, _context={"skill_change_publisher": lambda _: None})
    assert "changed" in result
    assert not (tmp_path / "data/ai_skills/sample").exists()


def test_batch_conflict_and_publish_failure_preserve_existing_skills(tmp_path):
    root = tmp_path / "source"
    package(root, "one")
    package(root, "two")
    existing = tmp_path / "data/ai_skills/two"
    existing.mkdir(parents=True)
    (existing / "keep").write_text("original")
    events = []
    with patch.object(builder, "get_app_data_dir", return_value=str(tmp_path / "data")):
        result = builder.install_agent_skill(str(root), skill_paths=["one", "two"], _context={"skill_change_publisher": events.append})
        assert "already exists" in result
        assert not (existing.parent / "one").exists() and not events
        assert (existing / "keep").read_text() == "original"
        def failed(_):
            raise RuntimeError("catalog unavailable")
        result = builder.install_agent_skill(str(root / "one"), _context={"skill_change_publisher": failed})
        assert "catalog unavailable" in result
        assert not (existing.parent / "one").exists()


def test_missing_resources_and_local_urls_are_rejected(tmp_path):
    source = package(tmp_path, "missing")
    (source / "SKILL.md").write_text((source / "SKILL.md").read_text() + "\nRead [reference](references/missing.md).")
    with patch.object(builder, "get_app_data_dir", return_value=str(tmp_path / "data")):
        result = builder.install_agent_skill(str(source), _context={"skill_change_publisher": lambda _: None})
    assert "Missing" in result
    for url in ("https://127.0.0.1/test.zip", "https://user:secret@example.com/test.zip", "http://example.com/test.zip"):
        with pytest.raises(ValueError):
            validate_public_https_url(url)


def test_cancelled_preparation_has_no_install_side_effect(tmp_path):
    source = package(tmp_path, "cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        with prepare_source(str(source), context={"abort_check": lambda: True}):
            pytest.fail("must not yield")


def test_single_remote_markdown_preserves_bytes_and_checks_digest(tmp_path):
    original = b"---\nname: remote\ndescription: Remote task\n---\nPerform the task.\n"
    events = []
    def download(url, target, context):
        target.write_bytes(original)
        return url
    with patch.object(builder, "get_app_data_dir", return_value=str(tmp_path / "data")), patch("core.skill_install.validate_public_https_url", side_effect=lambda value: value), patch("core.skill_install._download", side_effect=download):
        failed = builder.install_agent_skill("https://example.com/SKILL.md", expected_sha256="0" * 64, _context={"skill_change_publisher": events.append})
        assert "differs" in failed and not events
        result = builder.install_agent_skill("https://example.com/SKILL.md", _context={"skill_change_publisher": events.append})
    assert result.startswith("Success:")
    assert (tmp_path / "data/ai_skills/remote/SKILL.md").read_bytes() == original
