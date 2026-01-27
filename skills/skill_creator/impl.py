import os
import sys
from core.env_utils import get_app_data_dir

def create_new_skill(workspace_dir, skill_name, description, tool_name, tool_description, tool_code, description_cn=None):
    """
    Create a new local skill.
    
    Args:
        workspace_dir (str): Root workspace (unused for skill location).
        skill_name (str): Name of the skill.
        description (str): Skill description.
        tool_name (str): Tool function name.
        tool_description (str): Tool description.
        tool_code (str): Python code implementation.
        description_cn (str, optional): Chinese description of the skill.
    """
    try:
        # Use persistent User Data Directory for new skills
        data_dir = get_app_data_dir()
        skills_dir = os.path.join(data_dir, 'ai_skills')
        os.makedirs(skills_dir, exist_ok=True)
        
        # Validate skill name (alphanumeric and hyphens)
        if not all(c.isalnum() or c == '-' for c in skill_name):
             return "Error: Skill name must be alphanumeric (hyphens allowed)."

        new_skill_dir = os.path.join(skills_dir, skill_name)
        if os.path.exists(new_skill_dir):
            return f"Error: Skill '{skill_name}' already exists."
            
        os.makedirs(new_skill_dir)
        
        # Create SKILL.md
        desc_cn_line = f"description_cn: {description_cn}\n" if description_cn else ""
        md_content = f"""---
name: {skill_name}
description: {description}
{desc_cn_line}license: Apache-2.0
type: ai_generated
created_by: ai
allowed-tools: {tool_name}
---

# {skill_name.capitalize()} Skill

{description}

## Tools

### {tool_name}
{tool_description}

"""
        with open(os.path.join(new_skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
            f.write(md_content)
            
        # Create impl.py
        with open(os.path.join(new_skill_dir, 'impl.py'), 'w', encoding='utf-8') as f:
            f.write(tool_code)
            
        return f"Success: Created skill '{skill_name}' at '{new_skill_dir}'."
        
    except Exception as e:
        return f"Error: {str(e)}"
