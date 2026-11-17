import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from core.capability_search import score
from core.skill_manager import SkillManager

ROOT = Path(__file__).resolve().parents[1]


def test_chinese_ranking_and_precise_exclusions():
    manager = SkillManager.__new__(SkillManager)
    manager.skill_records = {}
    for path in (ROOT / "skills").glob("*/skill.json"):
        if not (path.parent / "SKILL.md").is_file():
            continue
        metadata = json.loads(path.read_text(encoding="utf-8"))
        manager.skill_records[metadata["name"]] = {"name": metadata["name"], "meta": {}, "spec": metadata,
            "body": (path.parent / "SKILL.md").read_text(encoding="utf-8")}
    assert manager.select_relevant_skills("帮我做经营月报", 1) == ["data-analysis"]
    data_spec = manager.skill_records["data-analysis"]["spec"]
    assert manager._infer_execution_surface(data_spec) == "tool_refs"
    assert manager._infer_preferred_script_name(data_spec) == ""
    assert "data-analysis" not in manager.select_relevant_skills("只改表格颜色", 50)
    assert "data-analysis" in manager.select_relevant_skills("分析表格数据，检查留存变化", 3)
    assert score("data analysis", names=["analysis"], anti=["simple file edit"]) > 0
    assert score("please edit the report", anti=["simple file edit"], description="edit report") > 0
    assert score("template-creator", names=["template-creator"]) > score("template-creator", body="template " * 1000)


def test_status_query_is_read_only_and_does_not_disclose_credentials_or_schema():
    spec = importlib.util.spec_from_file_location("capability_impl", ROOT / "skills/capability-manager/impl.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    inventory = [dict(name="disabled", enabled=False), dict(name="config", config_status={"complete": False, "missing_required": ["TOKEN"]}),
                 dict(name="failed", dependency_status={"ok": False, "message": "private"}), dict(name="scoped"), dict(name="later"), dict(name="ready")]
    manager = SimpleNamespace(get_all_skills=lambda: inventory, loaded_skills_meta={name: {} for name in ['disabled','config','failed','scoped','ready']},
                              skill_records={}, _is_skill_allowed_by_scope=lambda name, ctx: name != "scoped")
    result = module.inspect_capabilities(_context={"skill_manager": manager})
    states = {item["name"]: item["status"] for item in result["capabilities"]}
    assert states == {"disabled": "disabled", "config": "needs_configuration", "failed": "load_or_dependency_error",
                      "scoped": "out_of_scope", "later": "not_loaded", "ready": "available"}
    assert "private" not in json.dumps(result) and "parameters" not in json.dumps(result)
    assert manager.skill_records == {} and inventory[0]["enabled"] is False
