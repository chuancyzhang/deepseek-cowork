import json
from core.config_manager import ConfigManager
from core.llm.deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    build_deepseek_request_options,
)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

class SkillGenerator:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.api_key = config_manager.get("api_key")
        self.base_url = config_manager.get("base_url", DEFAULT_DEEPSEEK_BASE_URL)
        self.model_name = config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL)
        self.deepseek_thinking_enabled = config_manager.get("deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED)
        self.deepseek_reasoning_effort = config_manager.get("deepseek_reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT)

    def _build_chat_params(self, messages):
        params = {
            "model": self.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        params.update(
            build_deepseek_request_options(
                self.model_name,
                self.base_url,
                thinking_enabled=self.deepseek_thinking_enabled,
                reasoning_effort=self.deepseek_reasoning_effort,
            )
        )
        return params

    def refactor_code(self, code: str) -> dict:
        """
        Refactor the given code into a reusable skill function using LLM.
        Returns a dictionary with keys: tool_name, description, description_cn, code, skill_name
        """
        if not self.api_key or not OPENAI_AVAILABLE:
            return {"error": "LLM not available or API key missing"}

        system_prompt = """You are a Python Expert and Skill Creator.
Your task is to refactor the provided Python code snippet into a standalone, reusable function (Tool) for an AI Agent.

Requirements:
1. **Generalization**: Extract hardcoded values (paths, filenames, numbers) into function arguments.
2. **Structure**: The output must be a valid Python function.
3. **Naming**: Provide a snake_case function name (tool_name) and a kebab-case skill name (folder name).
4. **Documentation**: Provide a concise English description and a Chinese description.
5. **Imports**: Include all necessary imports inside the function or at the top of the code snippet.

Output Format:
Return ONLY a JSON object with the following structure (no markdown, no extra text):
{
    "skill_name": "example-skill-name",
    "tool_name": "example_function_name",
    "description": "English description of what the function does.",
    "description_cn": "中文描述该功能的作用。",
    "code": "def example_function_name(...):\\n    ..."
}
"""

        user_prompt = f"Refactor this code into a reusable skill:\n\n```python\n{code}\n```"

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                **self._build_chat_params([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ])
            )
            
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            return {"error": str(e)}

    def generate_skill_from_repo(self, repo_context: str, user_requirement: str) -> dict:
        """
        Generate a wrapper skill for a GitHub repository based on its analysis context.
        """
        if not self.api_key or not OPENAI_AVAILABLE:
            return {"error": "LLM not available or API key missing"}

        system_prompt = """You are an Expert Python Developer and AI Skill Creator.
Your task is to create a Python Wrapper Skill for an open-source project based on the provided Repository Analysis.

The wrapper should expose the project's core functionality as a Python function that can be called by an AI Agent.

Strategies:
1. **CLI Wrapper**: If the project is a CLI tool (has main.py, argparse, or usage in README), use `subprocess` to call it.
   - If it is a standard pip package (e.g., yt-dlp), prefer `subprocess.run(['executable', ...])`.
   - If it is a standalone script, use `subprocess.run([sys.executable, 'path/to/script.py', ...])`.
2. **Library Wrapper**: If it's a library, import it and use its API.

Requirements:
1. **Functionality**: Fulfill the user's specific requirement (or the main capability of the repo).
2. **Dependencies**: The code MUST include a check to install dependencies if missing.
   Example:
   ```python
   try:
       import some_lib
   except ImportError:
       import subprocess, sys
       subprocess.check_call([sys.executable, "-m", "pip", "install", "some_lib"])
       import some_lib
   ```
3. **Robustness**: Handle errors and return string output.
4. **Naming**: Snake_case for function, kebab-case for skill name.

Output Format:
Return ONLY a JSON object:
{
    "skill_name": "repo-name-wrapper",
    "tool_name": "tool_function_name",
    "description": "English description.",
    "description_cn": "Chinese description.",
    "code": "def tool_function_name(...): ..."
}
"""

        user_prompt = f"User Requirement: {user_requirement}\n\nRepository Context:\n{repo_context}"

        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.chat.completions.create(
                **self._build_chat_params([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ])
            )
            
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            return {"error": str(e)}
