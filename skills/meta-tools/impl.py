import json
import os
import shutil


def list_ai_skills(_context=None):
    if not _context:
        return "Error: Context not available."
    skill_manager = _context.get("skill_manager")
    if not skill_manager:
        return "Error: SkillManager not found in context."
    items = skill_manager.get_all_skills()
    result = []
    for item in items:
        if item.get("type") == "ai_generated" or item.get("created_by") == "ai":
            result.append(
                {
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "path": item.get("path"),
                    "enabled": item.get("enabled", True),
                }
            )
    return json.dumps(result, ensure_ascii=False, indent=2)


def delete_ai_skill(skill_name, _context=None):
    if not _context:
        return "Error: Context not available."
    skill_manager = _context.get("skill_manager")
    if not skill_manager:
        return "Error: SkillManager not found in context."
    target_path = None
    for skills_dir in getattr(skill_manager, "skills_dirs", []):
        if os.path.basename(skills_dir) != "ai_skills":
            continue
        candidate = os.path.join(skills_dir, skill_name)
        if os.path.isdir(candidate):
            target_path = candidate
            break
    if not target_path:
        return f"Failed to delete '{skill_name}': AI skill not found."
    try:
        shutil.rmtree(target_path)
        skill_manager.load_skills()
        return f"AI skill '{skill_name}' deleted successfully."
    except Exception as e:
        return f"Failed to delete '{skill_name}': {e}"


def update_experience(skill_name, experience=None, description=None, instructions=None, _context=None):
    """
    Update the experience/lessons learned, description, or instructions for a specific skill.
    
    Args:
        skill_name (str): Name of the skill to update.
        experience (str, optional): New lesson learned (appended to existing).
        description (str, optional): New skill description (replaces existing).
        instructions (str, optional): New usage instructions/body (replaces existing).
    """
    if not _context:
        return "Error: Context not available."
    
    skill_manager = _context.get('skill_manager')
    if not skill_manager:
        return "Error: SkillManager not found in context."
        
    success, message = skill_manager.update_skill(
        skill_name, 
        description=description, 
        instructions=instructions, 
        experience=experience
    )
    if success:
        updates = []
        if description: updates.append("description")
        if instructions: updates.append("instructions")
        if experience: updates.append("experience")
        skill_manager.load_skills()
        return f"Successfully updated '{skill_name}': {', '.join(updates)}"
    else:
        return f"Failed to update '{skill_name}': {message}"
