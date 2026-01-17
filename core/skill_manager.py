import os
import re
import importlib.util
import inspect
import sys

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
        else:
            # Normal python execution
            self.skills_dirs.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"))

        self.tools = {} # name -> function
        self.tool_definitions = [] # JSON schemas for LLM
        self.skill_prompts = [] # Markdown content from SKILL.md
        
        self.load_skills()

    def set_workspace_dir(self, workspace_dir):
        self.workspace_dir = workspace_dir

    def load_skills(self):
        """Scan skills directory and load SKILL.md + implementations"""
        for skills_dir in self.skills_dirs:
            if not os.path.exists(skills_dir):
                continue

            for skill_name in os.listdir(skills_dir):
                # Check config if enabled
                if self.config_manager and not self.config_manager.is_skill_enabled(skill_name):
                    continue
    
                skill_path = os.path.join(skills_dir, skill_name)
                if not os.path.isdir(skill_path):
                    continue
                
                # 1. Parse SKILL.md
                md_path = os.path.join(skill_path, "SKILL.md")
                if os.path.exists(md_path):
                    self._parse_skill_md(md_path)
                
                # 2. Load Implementation (impl.py)
                impl_path = os.path.join(skill_path, "impl.py")
                if os.path.exists(impl_path):
                    self._load_implementation(skill_name, impl_path)

    def _parse_skill_md(self, md_path):
        """Extract frontmatter and body"""
        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Simple regex to split frontmatter
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
            if match:
                frontmatter_raw = match.group(1)
                body = match.group(2).strip()
                
                # We could parse YAML here, but for now we just extract the body 
                # to inject into System Prompt later if needed.
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
