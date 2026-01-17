import os
import sys

def create_new_skill(workspace_dir, skill_name, description, tool_name, tool_description, tool_code):
    """
    Create a new local skill.
    
    Args:
        workspace_dir (str): Root workspace (unused for skill location).
        skill_name (str): Name of the skill.
        description (str): Skill description.
        tool_name (str): Tool function name.
        tool_description (str): Tool description.
        tool_code (str): Python code implementation.
    """
    try:
        # Determine app root
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            # D:\code\cowork\skills\skill-creator\impl.py -> D:\code\cowork
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            
        skills_dir = os.path.join(base_dir, 'skills')
        
        # Validate skill name (simple alphanumeric check)
        if not skill_name.isalnum():
             return "Error: Skill name must be alphanumeric."

        new_skill_dir = os.path.join(skills_dir, skill_name)
        if os.path.exists(new_skill_dir):
            return f"Error: Skill '{skill_name}' already exists."
            
        os.makedirs(new_skill_dir)
        
        # Create SKILL.md
        md_content = f"""# {skill_name.capitalize()} Skill

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
