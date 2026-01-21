import os
import re
import importlib.util
import inspect
import sys
import shutil

class SkillManager:
    def __init__(self, workspace_dir=None, config_manager=None):
        self.workspace_dir = workspace_dir
        self.config_manager = config_manager
        
        self.skills_dirs = []
        
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
                    if ':' in line:
                        key, val = line.split(':', 1)
                        meta[key.strip()] = val.strip().strip('"').strip("'")
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
            
            if body:
                self.skill_prompts.append(body)
        except Exception as e:
            print(f"Error parsing {md_path}: {e}")

    def _load_implementation(self, skill_name, impl_path):
        """Dynamic import of python module"""
        try:
            spec = importlib.util.spec_from_file_location(f"skills.{skill_name}", impl_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
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
