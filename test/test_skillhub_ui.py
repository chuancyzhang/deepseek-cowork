import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import tempfile
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
        from core.skillhub import SkillHubCache
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        self.page._hub_cache = SkillHubCache(cache_dir.name)
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

    def test_disk_cache_hit_and_manual_refresh(self):
        data = {'list': {'skills': [], 'total': 12}, 'categories': []}
        self.page._hub_cache.put(('list', '', '', self.page.hub_sort.currentData(), 1), data)
        self.page._set_mode('hub')
        self.assertEqual(self.callbacks, [])
        self.assertEqual(self.page.hub_data['total'], 12)
        self.page._load_hub(force=True)
        self.assertEqual(len(self.callbacks), 1)
        self.callbacks[-1]({'ok': False, 'error': 'HTTP 429'})
        self.assertEqual(self.page.hub_error, 'HTTP 429')

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

    def test_local_cards_reflow_and_keep_management_actions(self):
        from PySide6.QtWidgets import QFrame, QLabel
        from PySide6.QtGui import QFont, QFontDatabase
        from pathlib import Path
        from ui.skillhub_widgets import ClampedText
        original_font = self.app.font()
        self.addCleanup(lambda: self.app.setFont(original_font))
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/seguisym.ttf')
        self.app.setFont(QFont('Microsoft YaHei', 10))
        self.manager.get_all_skills.return_value = [dict(
            name=f'local-{i}', display_name='资料整理助手' + str(i),
            description_cn='读取项目资料，整理会议记录与文档，提取需要跟进的事项。' * 10,
            enabled=i % 2 == 0,
            source_type='bundled_plugin',
            presentation=dict(category='search_browse', short_name='资料整理助手' + str(i),
                              summary='读取项目资料，整理会议记录与文档，提取需要跟进的事项。' * 10,
                              examples=['搜索项目资料', '整理会议记录'], access_note='读取用户选择的资料。')) for i in range(8)]
        self.manager.is_skill_editable.return_value = False
        self.page.refresh_list()
        self.page.show()
        for mode in ('library', 'mine'):
            if mode == 'mine':
                for skill in self.manager.get_all_skills.return_value:
                    skill.pop('source_type')
                self.manager.is_skill_editable.return_value = True
                self.page.refresh_list()
                self.page._set_mode(mode)
                self.page.mode_control.set_current(mode)
            for width, columns in [(560, 1), (760, 2), (1060, 3), (1600, 4)]:
                self.page.resize(width, 760)
                for _ in range(6):
                    self.app.processEvents()
                cards = [c for c in self.page.findChildren(QFrame, 'CapabilityStoreCard') if c.isVisible()]
                self.assertEqual(len(cards), 8)
                self.assertEqual(len({c.x() for c in cards}), columns)
                self.assertEqual(len({c.height() for c in cards}), 1)
                self.assertLessEqual(self.page.content.width(), self.page.scroll.viewport().width())
                for card in cards:
                    for child in card.findChildren(QPushButton) + card.findChildren(ClampedText):
                        if child.isVisible():
                            self.assertTrue(card.rect().contains(child.geometry()), (width, child.objectName()))
                folder = os.environ.get('COWORK_CAPABILITY_SCREENSHOTS')
                if folder and width in (560, 1600):
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    self.page.grab().save(str(Path(folder) / f'{mode}-{width}.png'))
            if mode == 'mine':
                self.page._export_skill = Mock()
                self.page._delete_skill = Mock()
                menu = cards[0].findChild(QPushButton, 'CapabilityMoreActions').menu()
                self.assertEqual([a.text() for a in menu.actions()], ['导出', '删除'])
                menu.actions()[0].trigger()
                menu.actions()[1].trigger()
                self.page._export_skill.assert_called_once()
                self.page._delete_skill.assert_called_once()
        from core.theme import DesignTokens
        original_background = DesignTokens.bg_main
        with patch.object(DesignTokens, 'bg_main', '#202124'):
            self.page.refresh_theme()
            for _ in range(4):
                self.app.processEvents()
            cards = [c for c in self.page.findChildren(QFrame, 'CapabilityStoreCard') if c.isVisible()]
            self.assertTrue(all('#202124' in c.styleSheet() for c in cards))
        self.page.refresh_theme()
        for _ in range(4):
            self.app.processEvents()
        cards = [c for c in self.page.findChildren(QFrame, 'CapabilityStoreCard') if c.isVisible()]
        self.assertTrue(all(original_background in c.styleSheet() for c in cards))
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

    def test_icon_disk_hit_refresh_and_invalid_image_not_saved(self):
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        icon = QImage(8, 8, QImage.Format_RGB32)
        icon.fill(0xff112233)
        icon.save(buffer, 'PNG')
        data = bytes(buffer.data())
        url = 'https://example.com/icon.png'
        operations = []
        self.page._hub_worker = lambda operation, callback: operations.append((operation, callback))
        self.page.hub_client.icon = Mock(return_value=data)
        def finish():
            operation, callback = operations.pop(0)
            callback({'ok': True, 'data': operation()})
        self.page._hub_load_icon(url)
        finish()
        self.assertEqual(self.page._hub_cache.get_icon(url), data)
        self.page.hub_icons.clear()
        self.page.hub_client.icon.reset_mock()
        self.page._hub_load_icon(url)
        finish()
        self.page.hub_client.icon.assert_not_called()
        self.page.hub_data = {'skills': [{'iconUrl': url}], 'total': 1}
        self.page._load_hub(force=True)
        operations.clear()  # List refresh is independent of the icon worker.
        self.page._hub_load_icon(url)
        finish()
        self.page.hub_client.icon.assert_called_once_with(url)
        self.page.hub_client.icon.return_value = b'not an image'
        broken = url + '?broken'
        self.page._hub_load_icon(broken)
        finish()
        self.assertIsNone(self.page._hub_cache.get_icon(broken))
        self.assertIsNone(self.page.hub_icons[broken])

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
