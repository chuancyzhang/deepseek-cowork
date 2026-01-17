import unittest
import os
import sys
import tempfile
import shutil
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.interaction import InteractionBridge

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "config.json")
        # Mock sys.executable to control config path logic if needed, 
        # but ConfigManager logic is complex regarding paths.
        # For simplicity, we just test basic dict operations if we can bypass load.
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_set_get_config(self):
        # We need to patch where ConfigManager looks for files or just test the dict logic
        cm = ConfigManager()
        cm.config = {} # Reset
        cm.set("api_key", "sk-test")
        self.assertEqual(cm.get("api_key"), "sk-test")

class TestSkillManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir)
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_load_skills(self):
        # Create a dummy skill
        skill_name = "test-skill"
        skill_path = os.path.join(self.skills_dir, skill_name)
        os.makedirs(skill_path)
        
        with open(os.path.join(skill_path, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\n---\nTest skill description.")
            
        with open(os.path.join(skill_path, "impl.py"), "w") as f:
            f.write("def test_func():\n    return 'hello'")
            
        # Patch the skills_dirs detection
        with patch.object(SkillManager, '__init__', return_value=None) as mock_init:
            sm = SkillManager()
            sm.skills_dirs = [self.skills_dir]
            sm.tools = {}
            sm.tool_definitions = []
            sm.skill_prompts = []
            sm.config_manager = None
            
            # Call load_skills directly
            SkillManager.load_skills(sm)
            
            self.assertIn("test_func", sm.tools)
            self.assertEqual(sm.tools["test_func"](), "hello")

class TestInteractionBridge(unittest.TestCase):
    def test_bridge_singleton(self):
        from core.interaction import bridge
        self.assertIsInstance(bridge, InteractionBridge)

if __name__ == "__main__":
    unittest.main()
