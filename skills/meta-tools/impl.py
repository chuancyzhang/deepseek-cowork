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
        return f"Successfully updated '{skill_name}': {', '.join(updates)}"
    else:
        return f"Failed to update '{skill_name}': {message}"
