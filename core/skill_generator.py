import json
from core.config_manager import ConfigManager
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

class SkillGenerator:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.api_key = config_manager.get("api_key")
        self.base_url = config_manager.get("base_url", "https://api.deepseek.com")

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
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.2
            )
            
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            return {"error": str(e)}
