import os
import sys
import json
import shutil
import re
import shlex
from core.env_utils import get_app_data_dir

def convert_claude_skill(source_path, skill_name=None):
    """
    Convert a Claude Skill folder into a Cowork Skill.
    
    Args:
        source_path (str): Absolute path to the existing Claude Skill folder.
        skill_name (str, optional): Name for the new skill. Defaults to source folder name.
    """
    try:
        if not os.path.exists(source_path):
            return f"Error: Source path '{source_path}' does not exist."
            
        source_name = os.path.basename(os.path.normpath(source_path))
        if not skill_name:
            skill_name = source_name
            
        # Validate skill name
        if not all(c.isalnum() or c == '-' for c in skill_name):
             return "Error: Skill name must be alphanumeric (hyphens allowed)."

        # Target Directory
        data_dir = get_app_data_dir()
        skills_dir = os.path.join(data_dir, 'ai_skills')
        target_dir = os.path.join(skills_dir, skill_name)
        
        # 1. Copy Directory
        if os.path.exists(target_dir):
            return f"Error: Target skill directory '{target_dir}' already exists. Please delete it or choose a different name."
            
        shutil.copytree(source_path, target_dir)
        
        # 2. Analyze Scripts and Generate impl.py
        scripts_dir = os.path.join(target_dir, 'scripts')
        generated_tools = []
        impl_code_lines = [
            "import subprocess",
            "import sys",
            "import os",
            "import shlex",
            "",
            "def _run_script(script_name, args_str):",
            "    base_dir = os.path.dirname(__file__)",
            "    script_path = os.path.join(base_dir, 'scripts', script_name)",
            "    ",
            "    # Detect interpreter",
            "    cmd = []",
            "    if script_name.endswith('.py'):",
            "        cmd = [sys.executable, script_path]",
            "    elif script_name.endswith('.sh'):",
            "        cmd = ['bash', script_path]",
            "    elif script_name.endswith('.js'):",
            "        cmd = ['node', script_path]",
            "    else:",
            "        # Try executable",
            "        cmd = [script_path]",
            "    ",
            "    if args_str:",
            "        # Split args respecting quotes",
            "        cmd.extend(shlex.split(args_str))",
            "        ",
            "    try:",
            "        result = subprocess.run(cmd, capture_output=True, text=True, cwd=base_dir)",
            "        output = result.stdout",
            "        if result.stderr:",
            "            output += '\\n[STDERR]\\n' + result.stderr",
            "        return output",
            "    except Exception as e:",
            "        return f'Execution failed: {str(e)}'",
            ""
        ]
        
        if os.path.exists(scripts_dir):
            for file in os.listdir(scripts_dir):
                if file.startswith('.') or file.startswith('__'): continue
                if not os.path.isfile(os.path.join(scripts_dir, file)): continue
                
                # Create a wrapper tool
                base_name = os.path.splitext(file)[0]
                tool_name = f"run_{base_name.replace('-', '_')}"
                
                tool_doc = f"Executes the {file} script from the original Claude Skill."
                
                impl_code_lines.append(f"def {tool_name}(args=''):")
                impl_code_lines.append(f"    \"\"\"")
                impl_code_lines.append(f"    {tool_doc}")
                impl_code_lines.append(f"    Args:")
                impl_code_lines.append(f"        args (str): Command line arguments for the script.")
                impl_code_lines.append(f"    \"\"\"")
                impl_code_lines.append(f"    return _run_script('{file}', args)")
                impl_code_lines.append("")
                
                generated_tools.append(tool_name)
        
        # Write impl.py
        impl_path = os.path.join(target_dir, 'impl.py')
        with open(impl_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(impl_code_lines))
            
        # 3. Update SKILL.md
        md_path = os.path.join(target_dir, 'SKILL.md')
        if os.path.exists(md_path):
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Update Frontmatter
            # Regex to find allowed-tools or insert it
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
            if match:
                frontmatter = match.group(1)
                body = match.group(2)
                
                tools_str = ", ".join(generated_tools)
                
                if 'allowed-tools:' in frontmatter:
                    frontmatter = re.sub(r'allowed-tools:.*', f'allowed-tools: [{tools_str}]', frontmatter)
                else:
                    frontmatter += f"\nallowed-tools: [{tools_str}]"

                # Ensure AI generated tags are present
                if 'type:' not in frontmatter:
                    frontmatter += "\ntype: ai_generated"
                if 'created_by:' not in frontmatter:
                    frontmatter += "\ncreated_by: ai"
                    
                # Add Cowork Integration section
                body += "\n\n## Cowork Integration\n"
                body += "This skill has been adapted from a Claude Skill.\n"
                body += "The following Python tools are available to execute the original scripts:\n"
                for t in generated_tools:
                    body += f"- `{t}(args)`\n"
                    
                new_content = f"---\n{frontmatter}\n---\n{body}"
                
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
            else:
                # No frontmatter? Prepend one
                with open(md_path, 'w', encoding='utf-8') as f:
                     tools_str = ", ".join(generated_tools)
                     header = f"---\nname: {skill_name}\ndescription: Auto-converted Claude Skill.\ntype: ai_generated\ncreated_by: ai\nallowed-tools: [{tools_str}]\n---\n\n"
                     f.write(header + content)

        return f"Success: Converted '{source_path}' to '{target_dir}'. Generated {len(generated_tools)} wrapper tools."

    except Exception as e:
        return f"Error converting skill: {str(e)}"

def create_new_skill(workspace_dir, skill_name, description, tools_list, tool_code, usage_guidelines, description_cn=None):
    """
    Create a new local skill or update an existing one.
    
    Args:
        workspace_dir (str): Root workspace (unused for skill location).
        skill_name (str): Name of the skill.
        description (str): Short skill description (for frontmatter).
        tools_list (list): List of dicts, each containing 'name' and 'description' for a tool.
                           Example: [{'name': 'my_tool', 'description': 'Does something.'}]
        tool_code (str): Complete Python code implementation for impl.py.
        usage_guidelines (str): Detailed usage instructions, flows, and examples (body of SKILL.md).
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
        
        # Determine if we are creating or updating
        action = "Created"
        if os.path.exists(new_skill_dir):
            action = "Updated"
        else:
            os.makedirs(new_skill_dir)
        
        # Parse tools_list if it comes as a string (handling potential LLM stringification)
        if isinstance(tools_list, str):
            try:
                tools_list = json.loads(tools_list)
            except:
                return "Error: tools_list must be a list or a valid JSON string."
        
        # Extract tool names for frontmatter
        tool_names = [t.get('name') for t in tools_list]
        allowed_tools_str = ", ".join(tool_names)
        
        # Create SKILL.md content
        desc_cn_line = f"description_cn: {description_cn}\n" if description_cn else ""
        
        md_content = f"""---
name: {skill_name}
description: {description}
{desc_cn_line}license: Apache-2.0
type: ai_generated
created_by: ai
allowed-tools: [{allowed_tools_str}]
---

# {skill_name.capitalize()} Skill

{description}

{usage_guidelines}

## Tools

"""
        for tool in tools_list:
            t_name = tool.get('name', 'unknown')
            t_desc = tool.get('description', 'No description.')
            md_content += f"### {t_name}\n{t_desc}\n\n"

        # Write SKILL.md
        with open(os.path.join(new_skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
            f.write(md_content)
            
        # Write impl.py
        with open(os.path.join(new_skill_dir, 'impl.py'), 'w', encoding='utf-8') as f:
            f.write(tool_code)
            
        return f"Success: {action} skill '{skill_name}' at '{new_skill_dir}' with {len(tools_list)} tools."
        
    except Exception as e:
        return f"Error: {str(e)}"
