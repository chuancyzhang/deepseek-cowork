def record_general_experience(experience, tool_name=None, task_type=None, error_pattern=None, tags=None, _context=None):
    """
    Record a cross-task runtime lesson into the general-experience package.

    Args:
        experience (str): The lesson learned or reusable runtime guidance.
        tool_name (str, optional): Related tool name.
        task_type (str, optional): Related task type.
        error_pattern (str, optional): Related error pattern.
        tags (list, optional): Additional tags for later retrieval.
    """
    if not _context:
        return "Error: Context not available."

    skill_manager = _context.get("skill_manager")
    if not skill_manager:
        return "Error: SkillManager not found in context."

    success, message = skill_manager.record_experience(
        experience_text=experience,
        skill_name="general-experience",
        tool_name=tool_name,
        task_type=task_type,
        error_pattern=error_pattern,
        tags=tags if isinstance(tags, list) else None,
        source="general_experience_tool",
    )
    if not success:
        return f"Failed to record general experience: {message}"
    return "Successfully recorded experience in 'general-experience'."
