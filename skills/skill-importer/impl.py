import json
import os
import shutil

from core.env_utils import get_app_data_dir
from core.sandbox_runtime import install_skill_dependencies
from core.llm.factory import LLMFactory
from core.skill_adapter import discover_skill_artifacts


def _safe_read(path, limit=6000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def _collect_folder_summary(abs_path):
    artifacts = discover_skill_artifacts(abs_path)
    summary = {
        "root": abs_path,
        "files": [],
        "readme": "",
        "tool_candidates": [],
        "reference_candidates": [],
        "script_refs": artifacts["script_refs"][:30],
        "script_entries": artifacts["script_entries"][:30],
        "asset_refs": artifacts["asset_refs"][:30],
    }
    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "node_modules", "dist", "build"}]
        rel_root = os.path.relpath(root, abs_path)
        for file in files:
            rel_path = os.path.normpath(os.path.join(rel_root, file)) if rel_root != "." else file
            summary["files"].append(rel_path)
            lower = file.lower()
            if lower.startswith("readme") and not summary["readme"]:
                summary["readme"] = _safe_read(os.path.join(root, file))
            if lower.endswith((".py", ".sh", ".ps1", ".bat")):
                summary["tool_candidates"].append(rel_path)
            if lower.endswith((".md", ".json", ".yaml", ".yml", ".txt")):
                summary["reference_candidates"].append(rel_path)
    summary["files"] = summary["files"][:200]
    summary["tool_candidates"] = summary["tool_candidates"][:30]
    summary["reference_candidates"] = summary["reference_candidates"][:30]
    return summary


def _call_llm_json(config_manager, prompt, fallback):
    if not config_manager or not config_manager.get("api_key"):
        return fallback
    try:
        provider = LLMFactory.create_provider(config_manager)
        chunks = []
        for chunk in provider.chat_stream(
            [
                {"role": "system", "content": "Return valid JSON only. No markdown fences."},
                {"role": "user", "content": prompt},
            ]
        ):
            if chunk.get("type") == "content":
                chunks.append(chunk.get("content", ""))
        raw = "".join(chunks).strip()
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def _default_preview(source_path, skill_name, summary):
    tool_refs = []
    for candidate in summary["tool_candidates"][:3]:
        base = os.path.splitext(os.path.basename(candidate))[0].replace("-", "_")
        tool_refs.append(f"run_{base}")
    preview = {
        "skill_name": skill_name,
        "kind": "knowledge",
        "capability_group": "knowledge",
        "description": f"Imported from folder '{os.path.basename(source_path)}'.",
        "tags": ["imported", "folder"],
        "triggers": [skill_name.replace("-", " "), "imported folder"],
        "anti_triggers": [],
        "references": summary["reference_candidates"][:5],
        "tool_refs": tool_refs,
        "script_refs": summary.get("script_refs", [])[:10],
        "script_entries": summary.get("script_entries", [])[:10],
        "asset_refs": summary.get("asset_refs", [])[:10],
        "python_dependencies": [],
        "node_dependencies": [],
        "experience_policy": {
            "entry_storage": "experience/entries.jsonl",
            "summary_sync": "frontmatter_experience",
        },
        "disclosure_level_defaults": {
            "default_prompt_level": "brief",
            "include_references": False,
            "include_experience_entries": False,
        },
        "workflow": [
            "Review the imported folder structure and README.",
            "Pick the smallest relevant tool for the task.",
            "Avoid treating the whole imported folder as one executable workflow unless the user explicitly asks for it.",
        ],
        "creation_hints": {
            "source_folder": source_path,
            "needs_manual_review": True,
            "source_format": "generic",
        },
        "risks": [],
    }
    if not summary["tool_candidates"]:
        preview["risks"].append("No obvious executable tool source detected; generated skill will mainly be explanatory.")
    return preview


def analyze_skill_source_folder(workspace_dir, path, skill_name=None, _context=None):
    abs_path = os.path.abspath(os.path.join(workspace_dir or "", path))
    if not os.path.isdir(abs_path):
        return f"Error: Source folder '{path}' does not exist."
    inferred_name = skill_name or os.path.basename(abs_path).lower().replace("_", "-").replace(" ", "-")
    summary = _collect_folder_summary(abs_path)
    fallback = _default_preview(abs_path, inferred_name, summary)
    prompt = (
        "You are importing a source folder into a reusable AI skill.\n"
        "Return JSON with keys: skill_name, kind, capability_group, description, tags, triggers, anti_triggers, references, tool_refs, script_refs, script_entries, asset_refs, python_dependencies, node_dependencies, experience_policy, disclosure_level_defaults, workflow, creation_hints, risks.\n"
        "Prefer kind='knowledge' unless the folder clearly defines system-level importing behavior.\n"
        "Treat tools as lightweight atomic actions, not high-level workflows.\n"
        f"Requested skill name: {inferred_name}\n"
        f"Folder summary:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n"
    )
    config_manager = (_context or {}).get("config_manager")
    preview = _call_llm_json(config_manager, prompt, fallback)
    if not isinstance(preview, dict):
        preview = fallback
    preview.setdefault("skill_name", inferred_name)
    preview.setdefault("kind", "knowledge")
    preview.setdefault("capability_group", fallback["capability_group"])
    preview.setdefault("description", fallback["description"])
    preview.setdefault("references", fallback["references"])
    preview.setdefault("tool_refs", fallback["tool_refs"])
    preview.setdefault("script_refs", fallback["script_refs"])
    preview.setdefault("script_entries", fallback["script_entries"])
    preview.setdefault("asset_refs", fallback["asset_refs"])
    preview.setdefault("python_dependencies", fallback["python_dependencies"])
    preview.setdefault("node_dependencies", fallback["node_dependencies"])
    preview.setdefault("experience_policy", fallback["experience_policy"])
    preview.setdefault("disclosure_level_defaults", fallback["disclosure_level_defaults"])
    preview.setdefault("workflow", fallback["workflow"])
    preview.setdefault("creation_hints", fallback["creation_hints"])
    preview["source_path"] = abs_path
    preview["detected_files"] = summary["files"]
    return json.dumps(preview, ensure_ascii=False, indent=2)


def generate_skill_from_folder(workspace_dir, path, skill_name, approved_spec, _context=None):
    abs_path = os.path.abspath(os.path.join(workspace_dir or "", path))
    if not os.path.isdir(abs_path):
        return f"Error: Source folder '{path}' does not exist."
    try:
        spec = json.loads(approved_spec) if isinstance(approved_spec, str) else dict(approved_spec)
    except Exception as e:
        return f"Error: approved_spec must be valid JSON. {e}"

    config_manager = (_context or {}).get("config_manager")
    target_root = os.path.join(get_app_data_dir(), "ai_skills")
    os.makedirs(target_root, exist_ok=True)
    target_dir = os.path.join(target_root, skill_name)
    if os.path.exists(target_dir):
        return f"Error: Target skill '{skill_name}' already exists."
    shutil.copytree(abs_path, target_dir)

    default_md = (
        f"---\nname: {skill_name}\ndescription: {spec.get('description', 'Imported skill')}\ntype: ai_generated\ncreated_by: ai\nkind: {spec.get('kind', 'knowledge')}\n"
        f"capability_group: {spec.get('capability_group', 'knowledge')}\nallowed-tools: [{', '.join(spec.get('tool_refs', []))}]\nexperience: []\n---\n\n"
        f"# Skill Purpose\n{spec.get('description', 'Imported skill')}\n\n"
        "## When to Use\nUse this skill as guidance around the imported folder's capabilities.\n\n"
        "## When Not to Use\nDo not treat this skill as a single executable workflow unless the user explicitly asks for a broader orchestration.\n\n"
        "## Common Pitfalls\nAvoid over-generalizing the folder into one big capability; keep tools small and skills explanatory.\n\n"
        "## Experience / Lessons Learned\nAdd concrete lessons here as the skill evolves.\n\n"
        "## Recommended Workflow\nUse the imported folder as a source of experience and recommended tool usage rather than one giant executable workflow.\n\n"
        "## Recommended Tools\nReview the generated tool refs before using any lightweight tool.\n\n"
        "## Interface Details\nReview tool refs and references from the imported folder before using any lightweight tool.\n\n"
        "## Constraints and Safety Rules\nInspect imported code before executing any referenced tool source.\n\n"
        "## References\nKeep detailed source-derived notes in references/ when needed.\n"
    )
    prompt = (
        "Return JSON only with keys 'skill_json' and 'skill_md'. "
        "skill_json must be a valid Cowork skill metadata object with: version, name, kind, capability_group, description, tags, triggers, anti_triggers, references, tool_refs, script_refs, script_entries, asset_refs, python_dependencies, node_dependencies, experience_policy, disclosure_level_defaults, workflow, creation_hints. "
        "skill_md must be markdown text.\n"
        f"Approved spec:\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n"
    )
    fallback = {
        "skill_json": {
            "version": 2,
            "name": skill_name,
            "kind": spec.get("kind", "knowledge"),
            "capability_group": spec.get("capability_group", "knowledge"),
            "description": spec.get("description", "Imported skill"),
            "tags": spec.get("tags", []),
            "triggers": spec.get("triggers", []),
            "anti_triggers": spec.get("anti_triggers", []),
            "references": spec.get("references", []),
            "tool_refs": spec.get("tool_refs", []),
            "script_refs": spec.get("script_refs", []),
            "script_entries": spec.get("script_entries", []),
            "asset_refs": spec.get("asset_refs", []),
            "python_dependencies": spec.get("python_dependencies", []),
            "node_dependencies": spec.get("node_dependencies", []),
            "experience_policy": spec.get(
                "experience_policy",
                {"entry_storage": "experience/entries.jsonl", "summary_sync": "frontmatter_experience"},
            ),
            "disclosure_level_defaults": spec.get(
                "disclosure_level_defaults",
                {"default_prompt_level": "brief", "include_references": False, "include_experience_entries": False},
            ),
            "workflow": spec.get("workflow", []),
            "creation_hints": spec.get("creation_hints", {}),
        },
        "skill_md": default_md,
    }
    generated = _call_llm_json(config_manager, prompt, fallback)
    if not isinstance(generated, dict):
        generated = fallback
    skill_json = generated.get("skill_json") if isinstance(generated.get("skill_json"), dict) else fallback["skill_json"]
    skill_md = generated.get("skill_md") if isinstance(generated.get("skill_md"), str) else fallback["skill_md"]

    with open(os.path.join(target_dir, "skill.json"), "w", encoding="utf-8") as f:
        json.dump(skill_json, f, ensure_ascii=False, indent=2)
    with open(os.path.join(target_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_md)
    os.makedirs(os.path.join(target_dir, "experience"), exist_ok=True)
    dependency_status = install_skill_dependencies(
        skill_name,
        skill_json.get("python_dependencies") or [],
        skill_json.get("node_dependencies") or [],
    )
    if not dependency_status.get("ok"):
        return f"Success: Generated skill '{skill_name}' at '{target_dir}', but dependency setup is incomplete: {dependency_status.get('message')}"
    return f"Success: Generated skill '{skill_name}' at '{target_dir}'."
