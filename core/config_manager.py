import json
import os
import sys
import shutil
from .env_utils import get_app_data_dir, get_base_dir

class ConfigManager:
    def __init__(self):
        self.config_file = "config.json"
        
        # Use centralized data directory logic
        self.data_dir = get_app_data_dir()
        self.config_path = os.path.join(self.data_dir, self.config_file)
        
        # Migration: Check if config exists in old location (base_dir)
        base_dir = get_base_dir()
        old_config_path = os.path.join(base_dir, self.config_file)
        
        # If old config exists and new config doesn't, migrate it.
        # Check inequality to avoid copy error if paths are same (e.g. portable mode setup)
        if os.path.abspath(old_config_path) != os.path.abspath(self.config_path):
             if os.path.exists(old_config_path) and not os.path.exists(self.config_path):
                print(f"[Config] Migrating config from {old_config_path} to {self.config_path}")
                try:
                    shutil.copy2(old_config_path, self.config_path)
                except Exception as e:
                    print(f"[Config] Migration failed: {e}")

        self.config = {
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "disabled_skills": [],
            "god_mode": False,
            "plan_mode": False
        }
        self.load_config()

    def get_plan_mode(self):
        return self.config.get("plan_mode", False)

    def set_plan_mode(self, enabled: bool):
        self.config["plan_mode"] = enabled
        self.save_config()

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
