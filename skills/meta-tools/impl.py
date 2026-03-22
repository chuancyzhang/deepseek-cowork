def update_experience(skill_name=None, experience=None, description=None, instructions=None, tool_name=None, task_type=None, error_pattern=None, tags=None, _context=None):
    """
    Update the experience/lessons learned, description, or instructions for a specific skill.
    
    Args:
        skill_name (str, optional): Name of the skill to update. If omitted, record into general-experience.
        experience (str, optional): New lesson learned (appended to existing).
        description (str, optional): New skill description (replaces existing).
        instructions (str, optional): New usage instructions/body (replaces existing).
    """
    if not _context:
        return "Error: Context not available."
    
    skill_manager = _context.get('skill_manager')
    if not skill_manager:
        return "Error: SkillManager not found in context."
        
    updates = []

    if description or instructions:
        target_skill = skill_name or "general-experience"
        success, message = skill_manager.update_skill(
            target_skill,
            description=description,
            instructions=instructions,
        )
        if not success:
            return f"Failed to update '{target_skill}': {message}"
        if description:
            updates.append("description")
        if instructions:
            updates.append("instructions")

    if experience:
        success, message = skill_manager.record_experience(
            experience_text=experience,
            skill_name=skill_name,
            tool_name=tool_name,
            task_type=task_type,
            error_pattern=error_pattern,
            tags=tags if isinstance(tags, list) else None,
            source="meta_tool",
        )
        if not success:
            target_skill = skill_name or "general-experience"
            return f"Failed to update '{target_skill}': {message}"
        updates.append("experience")

    if not updates:
        return "No changes requested."

    skill_manager.load_skills()
    target_name = skill_name or "general-experience"
    return f"Successfully updated '{target_name}': {', '.join(updates)}"
