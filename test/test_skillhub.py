import os
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from core.skill_adapter import _extract_zip_to_tempdir
from core.skill_manager import SkillManager
from core.skillhub import SkillHubClient, file_hashes, read_origin


class Config:
    def __init__(self):
        self.enabled = {}
    def is_skill_enabled(self, name, default_enabled=True):
        return self.enabled.get(name, default_enabled)
    def set_skill_enabled(self, name, value, *, persist_strict=False):
        self.enabled[name] = value


class SkillHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.target = self.root / 'installed'
        self.target.mkdir()
        self.manager = SkillManager.__new__(SkillManager)
        self.manager.config_manager = Config()
        self.manager._default_writable_skill_root = lambda: str(self.target)
        self.manager._find_skill_path = lambda name: str(self.target / name) if (self.target / name).exists() else None
        self.manager._prepare_skill_dependencies = Mock(return_value={'ok': True})
        self.write_source('first')

    def write_source(self, body):
        (self.source / 'SKILL.md').write_text('---\nname: sample\ndescription: Sample skill\n---\n' + body, encoding='utf-8')

    def install(self, version='1.0.0', **kwargs):
        return self.manager.import_skill(str(self.source), prepare_dependencies=False,
            origin={'source': 'skillhub', 'slug': 'sample', 'version': version}, **kwargs)

    def test_install_disabled_and_no_dependency_execution(self):
        ok, message = self.install(enabled=False)
        self.assertTrue(ok, message)
        self.assertFalse(self.manager.config_manager.is_skill_enabled('sample'))
        self.manager._prepare_skill_dependencies.assert_not_called()
        self.assertEqual((self.source/'SKILL.md').read_bytes(), (self.target/'sample'/'SKILL.md').read_bytes())
        self.assertEqual(read_origin(self.target/'sample')['version'], '1.0.0')
        spec = json.loads((self.target/'sample'/'skill.json').read_text(encoding='utf-8'))
        self.assertNotIn('.skillhub.json', json.dumps(spec))

    def test_update_preserves_enabled_and_replaces_version(self):
        self.assertTrue(self.install(enabled=False)[0])
        self.manager.config_manager.set_skill_enabled('sample', True)
        self.write_source('second')
        ok, message = self.install('2.0.0', update_skill='sample')
        self.assertTrue(ok, message)
        self.assertTrue(self.manager.config_manager.is_skill_enabled('sample'))
        self.assertEqual(read_origin(self.target/'sample')['version'], '2.0.0')

    def test_local_edits_block_update(self):
        self.install(enabled=False)
        (self.target/'sample'/'SKILL.md').write_text('local edit')
        ok, message = self.install('2.0.0', update_skill='sample')
        self.assertFalse(ok)
        self.assertIn('本地修改', message)
        self.assertEqual((self.target/'sample'/'SKILL.md').read_text(), 'local edit')

    def test_origin_conflict_and_duplicate_are_errors(self):
        self.install(enabled=False)
        self.assertFalse(self.install()[0])
        (self.target/'sample'/'.skillhub.json').unlink()
        self.assertFalse(self.install('2.0.0', update_skill='sample')[0])

    def test_failed_refresh_restores_old_files(self):
        self.install(enabled=False)
        before = file_hashes(self.target/'sample')
        self.write_source('second')
        commit = Mock(side_effect=[RuntimeError('refresh failed'), None])
        ok, message = self.install('2.0.0', update_skill='sample', on_commit=commit)
        self.assertFalse(ok)
        self.assertIn('refresh failed', message)
        self.assertEqual(file_hashes(self.target/'sample'), before)
        self.assertEqual(read_origin(self.target/'sample')['version'], '1.0.0')

    def test_failed_first_install_restores_configuration(self):
        ok, _ = self.install(enabled=False, on_commit=Mock(side_effect=[RuntimeError('failed'), None]))
        self.assertFalse(ok)
        self.assertFalse((self.target/'sample').exists())
        self.assertTrue(self.manager.config_manager.is_skill_enabled('sample'))

    def test_configuration_write_error_prevents_install(self):
        self.manager.config_manager.set_skill_enabled = Mock(side_effect=[OSError('disk full'), None])
        ok, message = self.install(enabled=False)
        self.assertFalse(ok)
        self.assertIn('disk full', message)
        self.assertFalse((self.target/'sample').exists())

    def test_real_config_is_persisted_and_restored_on_refresh_failure(self):
        from core.config_manager import ConfigManager
        config = ConfigManager.__new__(ConfigManager)
        config.data_dir = str(self.root)
        config.config_path = str(self.root/'config.json')
        config.config = {'disabled_skills': [], 'enabled_skills': [], 'mcp_servers': []}
        config._write_config_or_raise()
        self.manager.config_manager = config
        before = dict(config.config)
        self.assertFalse(self.install(enabled=False, on_commit=Mock(side_effect=[OSError('refresh'), None]))[0])
        self.assertEqual(json.loads(Path(config.config_path).read_text()), before)
        ok, message = self.install(enabled=False)
        self.assertTrue(ok, message)
        self.assertIn('sample', json.loads(Path(config.config_path).read_text())['disabled_skills'])

    def test_zip_is_authoritative_and_slug_can_differ_from_name(self):
        client = SkillHubClient()
        client.files = Mock(side_effect=AssertionError('must not request a second source'))
        def download(slug, version, destination):
            with zipfile.ZipFile(destination, 'w') as archive:
                archive.writestr('package/SKILL.md', (self.source/'SKILL.md').read_bytes())
                archive.writestr('package/_meta.json', json.dumps({'slug':slug,'version':version}))
        client.download = download
        result = client.install(self.manager, 'remote-slug', '1.0.0')
        self.assertEqual(result['names'], ['sample'])
        self.assertEqual(read_origin(self.target/'sample')['slug'], 'remote-slug')
        self.write_source('new ZIP content')
        client.install(self.manager, 'remote-slug', '2.0.0', update_skill='sample')
        self.assertEqual(read_origin(self.target/'sample')['version'], '2.0.0')
        client.files.assert_not_called()

    def test_corrupt_download_is_actionable(self):
        from core.skillhub import SkillHubInstallError
        client = SkillHubClient()
        client.download = lambda slug, version, destination: Path(destination).write_bytes(b'not a ZIP')
        with self.assertRaisesRegex(SkillHubInstallError, '压缩包损坏'):
            client.install(self.manager, 'sample', '1.0.0')
        self.assertFalse((self.target/'sample').exists())

    def test_transport_metadata_checked_and_excluded(self):
        (self.source/'_meta.json').write_text(json.dumps({'slug':'sample','version':'1.0.0'}))
        ok, message = self.manager.import_skill(str(self.source), enabled=False, prepare_dependencies=False,
            origin={'source':'skillhub','slug':'sample','version':'1.0.0'})
        self.assertTrue(ok, message)
        self.assertFalse((self.target/'sample'/'_meta.json').exists())
        from core.skill_adapter import discover_skill_artifacts
        self.assertNotIn('.skillhub.json', discover_skill_artifacts(str(self.target/'sample'))['references'])

    def test_transport_metadata_wrong_version_rejected(self):
        (self.source/'_meta.json').write_text(json.dumps({'slug':'sample','version':'9.0.0'}))
        ok, _ = self.manager.import_skill(str(self.source), origin={'source':'skillhub','slug':'sample','version':'1.0.0'})
        self.assertFalse(ok)

    def test_zip_rejects_traversal_links_and_windows_collisions(self):
        for paths in [['../outside'], ['C:/outside'], ['CON.txt'], ['foo.'], ['A.txt','a.txt'], ['file:stream']]:
            with self.subTest(paths=paths):
                archive = self.root/'bad.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    for path in paths:
                        z.writestr(path, 'bad')
                with self.assertRaises(ValueError):
                    _extract_zip_to_tempdir(archive)
        with zipfile.ZipFile(archive, 'w') as z:
            info = zipfile.ZipInfo('link')
            info.external_attr = 0o120777 << 16
            z.writestr(info, 'target')
        with self.assertRaises(ValueError):
            _extract_zip_to_tempdir(archive)

    @patch('core.skillhub.requests.get')
    def test_anonymous_search_and_both_response_shapes(self, get):
        response = get.return_value
        response.status_code = 200
        response.json.return_value = {'code':0,'data':{'skills':[],'total':0}}
        self.assertEqual(SkillHubClient().search('missing')['total'], 0)
        self.assertEqual(get.call_count, 1)
        self.assertNotIn('X-API-Key', get.call_args.kwargs['headers'])
        response.json.return_value = {'skill':{'slug':'sample','displayName':'Sample'}}
        self.assertEqual(SkillHubClient().detail('sample')['skill']['slug'], 'sample')

    @patch('core.skillhub.time.sleep')
    @patch('core.skillhub.requests.get')
    def test_timeout_retries_bounded(self, get, sleep):
        import requests
        get.side_effect = requests.Timeout()
        with self.assertRaisesRegex(RuntimeError, '请求失败'):
            SkillHubClient().search()
        self.assertEqual(get.call_count, 3)

    def test_download_pins_requested_version(self):
        client = SkillHubClient()
        client.files = Mock(return_value={'version':'1.0.0','files':[]})
        client.download = Mock()
        manager = Mock(last_imported_skill_names=['sample'])
        manager.import_skill.return_value = (True, 'ok')
        client.install(manager, 'sample', '1.0.0')
        self.assertEqual(client.download.call_args.args[:2], ('sample', '1.0.0'))
        self.assertFalse(manager.import_skill.call_args.kwargs['enabled'])


if __name__ == '__main__':
    unittest.main()
