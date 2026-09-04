import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QPushButton
from main import SkillsCenterDialog


class SkillHubUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = Mock()
        self.manager.get_all_skills.return_value = []
        self.page = SkillsCenterDialog(self.manager, Mock())
        self.callbacks = []
        self.page._hub_worker = lambda operation, callback: self.callbacks.append(callback)
        self.addCleanup(self.page.deleteLater)

    def test_tab_and_stale_query_response(self):
        self.assertIn('hub', self.page.mode_control.buttons)
        self.page._set_mode('hub')
        first = self.callbacks[-1]
        self.page.search_input.setText('pdf')
        self.page.hub_timer.stop()
        self.page._load_hub()
        second = self.callbacks[-1]
        second({'ok': True, 'data': {'list': {'skills': [], 'total': 2}, 'categories': []}})
        first({'ok': True, 'data': {'list': {'skills': [], 'total': 99}, 'categories': []}})
        self.assertEqual(self.page.hub_data['total'], 2)
        self.page._set_mode('library')
        self.assertEqual(self.page.search_input.text(), '')
        self.page._set_mode('hub')
        self.assertEqual(self.page.search_input.text(), 'pdf')

    def test_error_is_visible_and_retry_exists(self):
        self.page._set_mode('hub')
        self.callbacks[-1]({'ok': False, 'error': 'HTTP 429'})
        self.assertEqual(self.page.hub_error, 'HTTP 429')
        self.assertTrue(any(b.text() == '重试' for b in self.page.findChildren(QPushButton)))

    def test_detail_version_and_theme_refresh(self):
        self.page._set_mode('hub')
        self.page._hub_open('sample')
        self.callbacks[-1]({'ok': True, 'data': {
            'skill': {'displayName': '测试技能', 'summary': '测试描述'},
            'versions': [{'version': '1.0.0', 'changelog': '初始版本'}],
            'evaluation_error': '暂无评测',
        }})
        self.page.refresh_theme()
        self.assertTrue(any(b.text() == '安装到我的能力' for b in self.page.findChildren(QPushButton)))
        self.page._hub_back()
        self.assertEqual(self.page.hub_slug, '')

    def test_theme_refresh_keeps_chosen_version_and_restores_color(self):
        from core.theme import DesignTokens
        from PySide6.QtWidgets import QComboBox, QLabel
        self.page.current_mode = 'hub'
        self.page.hub_slug = 'sample'
        self.page.hub_detail = {'skill': {'displayName': 'Sample'}, 'versions': [
            {'version': '2.0.0'}, {'version': '1.0.0'}]}
        self.page._render_content()
        box = self.page.findChild(QComboBox, 'SkillHubVersion')
        box.setCurrentIndex(1)
        original = DesignTokens.text_primary
        with patch.object(DesignTokens, 'text_primary', '#b010ab'):
            self.page.refresh_theme()
            self.assertEqual(self.page.hub_version, '1.0.0')
            self.assertTrue(any('#b010ab' in label.styleSheet() for label in self.page.findChildren(QLabel)))
        self.page.refresh_theme()
        self.assertEqual(DesignTokens.text_primary, original)
        self.assertEqual(self.page.hub_version, '1.0.0')

    def test_grid_reflows_one_to_four_columns_and_buttons_install(self):
        from ui.skillhub_widgets import SkillHubCard
        self.page.current_mode = 'hub'
        self.page.hub_data = {'total': 8, 'skills': [dict(slug=f'skill-{i}', name='很长的技能名称' * 8,
            description='多行说明' * 100, version='1.0.0') for i in range(8)]}
        self.page._hub_install = Mock()
        self.page.show()
        for width, columns in [(560, 1), (760, 2), (1060, 3), (1600, 4)]:
            self.page.resize(width, 760)
            for _ in range(4):
                self.app.processEvents()
            self.assertEqual(self.page._hub_layout_key[0], columns)
            cards = [c for c in self.page.findChildren(SkillHubCard) if c.isVisible()]
            self.assertEqual(len(cards), 8)
            self.assertEqual(len({card.x() for card in cards}), columns)
            self.assertEqual(len({card.height() for card in cards}), 1)
        cards[0].action.click()
        self.page._hub_install.assert_called_once_with('skill-0', {'version':'1.0.0'}, None)
        self.page.close()

    def test_failure_stays_with_its_skill_and_version(self):
        from PySide6.QtWidgets import QLabel
        self.page.current_mode = 'hub'
        self.page.hub_tasks['other'] = {'stage':'error','version':'1.0.0','message':'另一个技能安装失败'}
        self.page.hub_slug = 'sample'
        self.page.hub_detail = {'skill':{'displayName':'Sample'},'versions':[{'version':'1.0.0'}]}
        self.page._render_content()
        self.assertFalse(any('另一个技能安装失败' in w.text() for w in self.page.findChildren(QLabel)))
        self.page.hub_tasks['sample'] = {'stage':'error','version':'0.9.0','message':'旧版本失败','diagnostic':'details'}
        self.page._render_content()
        self.assertFalse(any(w.text() == '重试安装' and not w.isHidden() for w in self.page.findChildren(QPushButton)))

    def test_icons_are_bounded_and_invalid_images_rejected(self):
        from ui.skillhub_widgets import decode_icon
        with self.assertRaises(ValueError):
            decode_icon(b'not an image')
        for index in range(8):
            self.page._hub_load_icon(f'https://example.com/{index}.png')
        self.assertEqual(len(self.page._hub_icon_active), 4)
        self.assertEqual(len(self.page._hub_icon_queue), 4)
        callback = self.callbacks[0]
        callback({'ok':False,'error':'download failed'})
        self.assertEqual(len(self.page._hub_icon_active), 4)
        self.assertEqual(len(self.page._hub_icon_queue), 3)
        self.assertIn('https://example.com/0.png', self.page.hub_icons)


if __name__ == '__main__':
    unittest.main()
