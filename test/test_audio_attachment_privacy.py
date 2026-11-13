"""Retired transcription must not broaden earlier audio-sharing consent."""
from pathlib import Path
import pytest
from core.audio_attachments import is_audio_attachment
from core.desktop_submission import user_payload
from core.skill_manager import SkillManager, RETIRED_SKILLS


def test_protected_audio_rejected_after_transcription_retirement(tmp_path):
    audio = tmp_path / "private.m4a"
    audio.write_bytes(b"audio")
    with pytest.raises(ValueError, match="语音转文字已下线"):
        user_payload("继续处理", [str(audio)], workspace=tmp_path, keep_audio_local=True)
    assert audio.read_bytes() == b"audio"
    assert is_audio_attachment(audio)


def test_non_audio_input_remains_available_for_legacy_conversation(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("notes", encoding="utf-8")
    result = user_payload("整理记录", [str(notes)], workspace=tmp_path, keep_audio_local=True)
    assert result["meta"]["display_content"] == "整理记录"
    assert result["meta"]["user_added_files"] == [str(notes)]
    assert "语音转文字" not in result["content"]


def test_old_speech_skill_cannot_be_loaded_or_reimported(tmp_path):
    skill = tmp_path / "speech-to-text"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: speech-to-text\ndescription: old ASR\n---\nRetired", encoding="utf-8")
    manager = SkillManager.__new__(SkillManager)
    manager.loaded_skill_sources = {"speech-to-text": str(skill)}
    assert manager._find_skill_path("speech-to-text") is None
    assert not manager._is_skill_enabled_for_path("speech-to-text", str(skill))
    with pytest.raises(ValueError, match="已下线"):
        manager._import_single_skill_dir(str(skill))
    assert "speech-to-text" in RETIRED_SKILLS
