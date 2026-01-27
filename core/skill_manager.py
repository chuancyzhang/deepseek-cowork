import os
import re
import importlib.util
import inspect
import sys
import shutil
from .env_utils import get_app_data_dir, ensure_package_installed

class SkillManager:
    def __init__(self, workspace_dir=None, config_manager=None):
        self.workspace_dir = workspace_dir
        self.config_manager = config_manager
        
        self.skills_dirs = []
        
        # 1. User Data Skills (Persistence Layer - Highest Priority)
        # This allows users to add skills that survive application updates
        data_dir = get_app_data_dir()
        self.skills_dirs.append(os.path.join(data_dir, "skills"))
        self.skills_dirs.append(os.path.join(data_dir, "ai_skills"))

        # Determine the base directory
        if getattr(sys, 'frozen', False):
            # If running as a PyInstaller bundle
            if hasattr(sys, '_MEIPASS'):
                 # --onefile mode: Temp directory
                 self.skills_dirs.append(os.path.join(sys._MEIPASS, "skills"))
            else:
                 # --onedir mode
                 base_dir = os.path.dirname(sys.executable)
                 
                 # Check _internal location (PyInstaller 6+ default) - Built-in skills
                 path_internal = os.path.join(base_dir, "_internal", "skills")
                 if os.path.exists(path_internal):
                     self.skills_dirs.append(path_internal)
                 
                 # Check standard location (next to exe) - User added skills
                 self.skills_dirs.append(os.path.join(base_dir, "skills"))
                 self.skills_dirs.append(os.path.join(base_dir, "ai_skills"))
        else:
            # Normal python execution
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.skills_dirs.append(os.path.join(repo_root, "skills"))
            self.skills_dirs.append(os.path.join(repo_root, "ai_skills"))
            
            # Also check dist folder (for cross-environment visibility during dev)
            # This allows dev mode to see skills created while running the EXE
            dist_dir = os.path.join(repo_root, "dist")
            if os.path.exists(dist_dir):
                for item in os.listdir(dist_dir):
                    # Standard skills
                    candidate_path = os.path.join(dist_dir, item, "skills")
                    if os.path.isdir(candidate_path):
                        self.skills_dirs.append(candidate_path)
                    # AI skills
                    candidate_path_ai = os.path.join(dist_dir, item, "ai_skills")
                    if os.path.isdir(candidate_path_ai):
                        self.skills_dirs.append(candidate_path_ai)

        self.tools = {} # name -> function
        self.tool_definitions = [] # JSON schemas for LLM
        self.skill_prompts = [] # Markdown content from SKILL.md
        self.tool_to_skill_map = {} # tool_name -> skill_name
        self.loaded_skills_meta = {} # skill_name -> metadata dict
        
        self.load_skills()

    def set_workspace_dir(self, workspace_dir):
        self.workspace_dir = workspace_dir

    def _scan_dist_dirs(self):
        """
        Dynamically scan for new skills directories.
        - In Dev mode: Scan dist folder to pick up skills created by EXE runs.
        - In Frozen mode: Scan exe directory for newly created ai_skills folder.
        """
        if getattr(sys, 'frozen', False):
            # EXE Mode
            base_dir = os.path.dirname(sys.executable)
            
            # Check standard location (next to exe)
            candidate_paths = [
                os.path.join(base_dir, "skills"),
                os.path.join(base_dir, "ai_skills")
            ]
            
            for path in candidate_paths:
                if os.path.isdir(path) and path not in self.skills_dirs:
                    self.skills_dirs.append(path)
            return

        # Dev Mode
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dist_dir = os.path.join(repo_root, "dist")
        
        if not os.path.exists(dist_dir):
            return

        for item in os.listdir(dist_dir):
            # Standard skills
            candidate_path = os.path.join(dist_dir, item, "skills")
            if os.path.isdir(candidate_path) and candidate_path not in self.skills_dirs:
                self.skills_dirs.append(candidate_path)
            
            # AI skills
            candidate_path_ai = os.path.join(dist_dir, item, "ai_skills")
            if os.path.isdir(candidate_path_ai) and candidate_path_ai not in self.skills_dirs:
                self.skills_dirs.append(candidate_path_ai)

    def get_all_skills(self):
        """
        Scan all skill directories and return a list of skill info dictionaries.
        Includes both enabled and disabled skills.
        """
        # Dynamically check for new dist directories in dev mode
        self._scan_dist_dirs()

        all_skills = []
        seen_skills = set()
        
        for skills_dir in self.skills_dirs:
            if not os.path.exists(skills_dir):
                continue
            
            for skill_name in os.listdir(skills_dir):
                if skill_name == "__pycache__" or skill_name.startswith('.'):
                    continue

                if skill_name in seen_skills:
                    continue
                
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                
                # Default info
                skill_info = {
                    "name": skill_name,
                    "path": skill_path,
                    "description": "No description available.",
                    "enabled": True, # Default to true if not in config
                    "tools": []
                }
                
                # Check config
                if self.config_manager:
                    skill_info["enabled"] = self.config_manager.is_skill_enabled(skill_name)
                
                # Parse SKILL.md
                md_path = os.path.join(skill_path, "SKILL.md")
                if os.path.exists(md_path):
                    meta, body = self._parse_skill_md_content(md_path)
                    if meta:
                        if "description" in meta:
                            skill_info["description"] = meta["description"]
                        # Merge all other meta fields
                        skill_info.update(meta)
                
                # Force 'ai_generated' if folder name suggests (optional fallback)
                # or if user explicitly created it via tool (which we can't easily track without meta)
                
                all_skills.append(skill_info)
                seen_skills.add(skill_name)
        
        return all_skills

    def import_skill(self, source_path):
        """Import a skill from a local directory"""
        if not os.path.isdir(source_path):
            return False, "Source is not a directory"
            
        skill_name = os.path.basename(source_path)
        if not os.path.exists(os.path.join(source_path, "SKILL.md")):
            return False, "SKILL.md not found in source directory"
            
        # Use the user-writable skills directory (last in list usually)
        # If frozen, it's the one next to exe. If dev, it's repo/skills.
        target_dir = self.skills_dirs[-1]
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir)
            except Exception as e:
                return False, f"Failed to create skills directory: {e}"
                
        target_path = os.path.join(target_dir, skill_name)
        if os.path.exists(target_path):
            return False, f"Skill '{skill_name}' already exists"
            
        try:
            shutil.copytree(source_path, target_path)
            return True, f"Skill '{skill_name}' imported successfully"
        except Exception as e:
            return False, f"Import failed: {e}"

    def update_skill_experience(self, skill_name, experience_text):
        """
        Append a new experience string to the SKILL.md of the given skill.
        This enables 'Self-Evolving' capabilities.
        """
        # 1. Find the skill path
        skill_path = None
        for s_dir in self.skills_dirs:
            p = os.path.join(s_dir, skill_name)
            if os.path.isdir(p):
                skill_path = p
                break
        
        if not skill_path:
            return False, f"Skill '{skill_name}' not found."
        
        md_path = os.path.join(skill_path, "SKILL.md")
        if not os.path.exists(md_path):
            return False, f"SKILL.md not found for '{skill_name}'."

        try:
            # 2. Read existing content
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 3. Parse Frontmatter
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
            if not match:
                return False, "Invalid SKILL.md format (missing frontmatter)."
            
            frontmatter_raw = match.group(1)
            body = match.group(2)
            
            lines = frontmatter_raw.split('\n')
            new_lines = []
            exp_found = False
            
            # 4. Update Frontmatter
            # We need to robustly handle the YAML-like structure. 
            # If 'experience:' exists, we append to it.
            # If not, we add it.
            
            # Simple approach: Check if 'experience:' line exists
            # Note: This simple parser assumes one-line list or multi-line standard yaml.
            # Our parser supports [item1, item2].
            
            # Let's rebuild the frontmatter lines
            for line in lines:
                if line.strip().startswith('experience:'):
                    # Found existing experience field
                    # We need to parse the existing list and add to it
                    key, val = line.split(':', 1)
                    val = val.strip()
                    current_exp = []
                    if val.startswith('[') and val.endswith(']'):
                        inner = val[1:-1]
                        if inner.strip():
                            current_exp = [v.strip().strip('"\'') for v in inner.split(',')]
                    
                    if experience_text not in current_exp:
                        current_exp.append(experience_text)
                    
                    # Re-serialize to JSON-style list for simplicity
                    # Escape quotes in strings
                    quoted_exp = [f'"{e.replace('"', '\\"')}"' for e in current_exp]
                    new_line = f'experience: [{", ".join(quoted_exp)}]'
                    new_lines.append(new_line)
                    exp_found = True
                else:
                    new_lines.append(line)
            
            if not exp_found:
                # Add new experience field
                quoted_exp = f'"{experience_text.replace('"', '\\"')}"'
                new_lines.append(f'experience: [{quoted_exp}]')
            
            # 5. Write back
            new_frontmatter = "\n".join(new_lines)
            new_content = f"---\n{new_frontmatter}\n---\n{body}"
            
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
                
            return True, f"Experience added to '{skill_name}'."
            
        except Exception as e:
            return False, f"Failed to update experience: {e}"

    def load_skills(self):
        """Scan skills directory and load SKILL.md + implementations for enabled skills"""
        self.tools = {}
        self.tool_definitions = []
        self.skill_prompts = []
        self.tool_to_skill_map = {}
        self.loaded_skills_meta = {}
        
        for skills_dir in self.skills_dirs:
            if not os.path.exists(skills_dir):
                continue

            for skill_name in os.listdir(skills_dir):
                if skill_name == "__pycache__" or skill_name.startswith('.'):
                    continue

                # Check config if enabled
                if self.config_manager and not self.config_manager.is_skill_enabled(skill_name):
                    continue
    
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                
                # 1. Parse SKILL.md
                md_path = os.path.join(skill_path, "SKILL.md")
                if os.path.exists(md_path):
                    self._parse_skill_md(md_path, skill_name)
                
                # 2. Load Implementation (impl.py)
                impl_path = os.path.join(skill_path, "impl.py")
                if os.path.exists(impl_path):
                    self._load_implementation(skill_name, impl_path)

    def _parse_skill_md_content(self, md_path):
        """Helper to parse MD file and return meta dict and body string"""
        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
            if match:
                frontmatter_raw = match.group(1)
                body = match.group(2).strip()
                
                meta = {}
                for line in frontmatter_raw.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    
                    if ':' in line:
                        key, val = line.split(':', 1)
                        key = key.strip()
                        val = val.strip()
                        
                        # Handle simple inline list: [item1, item2]
                        if val.startswith('[') and val.endswith(']'):
                            inner = val[1:-1]
                            if not inner.strip():
                                val = []
                            else:
                                val = [v.strip().strip('"\'') for v in inner.split(',')]
                        else:
                            val = val.strip('"\'')
                            
                        meta[key] = val
                return meta, body
            return {}, content
        except Exception:
            return {}, ""

    def _parse_skill_md(self, md_path, skill_name):
        """Extract frontmatter and body"""
        try:
            meta, body = self._parse_skill_md_content(md_path)
            if meta:
                self.loaded_skills_meta[skill_name] = meta
            
            # Inject Experience into Prompt if available
            prompt_content = body
            if meta and 'experience' in meta:
                exp_list = meta['experience']
                if isinstance(exp_list, list) and exp_list:
                    exp_text = "\n\n### 🧠 Learned Experience (Self-Evolution)\nThe following lessons have been learned from previous executions:\n"
                    for exp in exp_list:
                        exp_text += f"- {exp}\n"
                    prompt_content += exp_text
            
            if prompt_content:
                self.skill_prompts.append(prompt_content)
        except Exception as e:
            print(f"Error parsing {md_path}: {e}")

    def _load_implementation(self, skill_name, impl_path):
        """Dynamic import of python module"""
        try:
            spec = importlib.util.spec_from_file_location(f"skills.{skill_name}", impl_path)
            module = importlib.util.module_from_spec(spec)
            
            try:
                spec.loader.exec_module(module)
            except ImportError as e:
                # Hot-reload logic for missing dependencies (Top-level imports)
                print(f"[SkillManager] Skill '{skill_name}' missing dependency: {e}")
                
                # Try to extract package name. e.name is reliable for ModuleNotFoundError
                missing_pkg = getattr(e, 'name', None)
                if not missing_pkg and "No module named" in str(e):
                    # Fallback parsing
                    import re
                    match = re.search(r"No module named '([^']+)'", str(e))
                    if match:
                        missing_pkg = match.group(1)
                
                if missing_pkg:
                    print(f"[SkillManager] Auto-installing missing dependency: {missing_pkg}...")
                    try:
                        # Attempt to install and hot-reload
                        ensure_package_installed(missing_pkg)
                        
                        # Retry loading the module
                        print(f"[SkillManager] Retrying load of '{skill_name}' after installation...")
                        # We need to reload the spec/module to be safe? 
                        # Actually exec_module can be called again on the same module object, 
                        # but it's cleaner to re-create if possible. 
                        # Let's try exec_module again.
                        spec.loader.exec_module(module)
                        print(f"[SkillManager] Successfully loaded '{skill_name}' after auto-install.")
                        
                    except Exception as install_err:
                        print(f"[SkillManager] Failed to auto-install dependency {missing_pkg}: {install_err}")
                        # If install fails, we re-raise the original error or the install error
                        raise e
                else:
                    raise e
            
            # Inspect functions to generate Tool Definitions
            for name, func in inspect.getmembers(module, inspect.isfunction):
                if name.startswith('_'): continue
                
                # Register tool
                # Note: We bind workspace_dir later during execution or partial
                self.tools[name] = func
                self.tool_to_skill_map[name] = skill_name
                
                # Generate JSON Schema dynamically
                sig = inspect.signature(func)
                properties = {}
                required = []
                
                for param_name, param in sig.parameters.items():
                    # Skip injected parameters
                    if param_name in ['workspace_dir', '_context']:
                        continue
                        
                    # Infer type (simple mapping)
                    param_type = "string" # default
                    description = "Parameter"
                    
                    # Check default value for type inference
                    if param.default != inspect.Parameter.empty:
                        if isinstance(param.default, bool):
                            param_type = "boolean"
                        elif isinstance(param.default, int):
                            param_type = "integer"
                        elif isinstance(param.default, list):
                            param_type = "array"
                    
                    # Heuristic type inference (override if name matches known patterns)
                    if param_name == 'tasks':
                        param_type = "array"
                        description = "List of tasks"
                    elif param_name in ['limit', 'offset']:
                        param_type = "integer"
                    elif param_name == 'recursive':
                        param_type = "boolean"
                    
                    prop_def = {
                        "type": param_type,
                        "description": description
                    }
                    
                    if param_type == "array":
                        prop_def["items"] = {"type": "string"}
                        
                    properties[param_name] = prop_def
                    
                    if param.default == inspect.Parameter.empty:
                        required.append(param_name)

                tool_def = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": func.__doc__.strip().split('\n')[0] if func.__doc__ else f"Tool {name}",
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required
                        }
                    }
                }
                self.tool_definitions.append(tool_def)
                
        except Exception as e:
            print(f"Error loading implementation {impl_path}: {e}")

    def get_skill_of_tool(self, tool_name):
        return self.tool_to_skill_map.get(tool_name)


    def get_tool_definitions(self):
        return self.tool_definitions

    def get_system_prompts(self):
        return "\n\n".join(self.skill_prompts)

    def call_tool(self, name, args, context=None):
        if name not in self.tools:
            return f"Error: Tool '{name}' not found."
        
        func = self.tools[name]
        
        # Inject workspace_dir if the function expects it
        sig = inspect.signature(func)
        if 'workspace_dir' in sig.parameters:
            args['workspace_dir'] = self.workspace_dir
            
        # Inject context if the function expects it (as _context or **kwargs)
        if context:
            if '_context' in sig.parameters:
                args['_context'] = context
            # We don't indiscriminately add to **kwargs to avoid unexpected argument errors 
            # unless we know the function signature is flexible.
            # But for our system, we can define a standard: tools wanting context should accept `_context`.
            
        try:
            return func(**args)
        except Exception as e:
            return f"Error executing {name}: {str(e)}"
