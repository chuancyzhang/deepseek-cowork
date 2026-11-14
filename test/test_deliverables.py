import unittest
from core.ppt_agent import build_ppt_agent_prompt, PPT_AGENT_OUTPUT_PPTX


class PresentationPromptTests(unittest.TestCase):
    def test_direct_pptx_prompt_defines_template_content_boundaries(self):
        result = build_ppt_agent_prompt(
            "生成汇报",
            template_file=r"D:\template.pptx",
            output_format=PPT_AGENT_OUTPUT_PPTX,
            renderer="none",
            visual_validation=False,
        )

        prompt = result["prompt"]
        self.assertIn("模板仅提供品牌规范、背景、色彩、页眉页脚和版式语言", prompt)
        self.assertIn("示例文案、示例图形、参考流程、金字塔、图标和占位符不需要作为默认保留内容", prompt)
        self.assertIn("不得在原示例元素上直接叠加新内容", prompt)
        self.assertIn("标题安全区", prompt)
        self.assertIn("品牌框架的空白页重建", prompt)
