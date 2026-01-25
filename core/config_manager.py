import json
import os
import sys

class ConfigManager:
    def __init__(self):
        self.config_file = "config.json"
        # Determine config path (persist next to exe or in user home)
        # For simplicity, let's try next to executable/script first
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        self.config_path = os.path.join(base_dir, self.config_file)
        self.config = {
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "disabled_skills": [],
            "god_mode": False
        }
        self.load_config()

    def get_god_mode(self):
        return self.config.get("god_mode", False)

    def set_god_mode(self, enabled: bool):
        self.config["god_mode"] = enabled
        self.save_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception as e:
                print(f"Error loading config: {e}")

    def save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

    def is_skill_enabled(self, skill_name):
        return skill_name not in self.config.get("disabled_skills", [])

    def set_skill_enabled(self, skill_name, enabled):
        disabled = set(self.config.get("disabled_skills", []))
        if enabled:
            if skill_name in disabled:
                disabled.remove(skill_name)
        else:
            disabled.add(skill_name)
        self.config["disabled_skills"] = list(disabled)
        self.save_config()
