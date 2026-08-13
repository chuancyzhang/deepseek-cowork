import os
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from core import process_utils
from core.process_utils import (
    ProcessSingletonLock,
    build_process_singleton_lock_path,
    popen_external_program,
    reveal_path_in_file_manager,
    runtime_debug_logging_enabled,
    subprocess_kwargs_no_window,
    terminate_process_tree,
)


class TestProcessUtils(unittest.TestCase):
    def test_terminate_process_tree_uses_taskkill_for_live_windows_process(self):
        process = MagicMock()
        process.pid = 4321
        process.poll.side_effect = [None, None, 0]
        process.wait.return_value = 0
        with patch.object(process_utils.os, "name", "nt"), patch.object(
            process_utils.subprocess,
            "run",
        ) as run:
            self.assertTrue(terminate_process_tree(process, timeout=0.5))

        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "4321", "/T", "/F"])

    def test_reveal_path_uses_windows_shell_for_directory_and_file(self):
        calls = []

        def shell_execute(*args):
            calls.append(args)
            return 33

        with tempfile.TemporaryDirectory(prefix="中文 路径,") as temp_dir:
            file_path = os.path.join(temp_dir, "报告, final.pptx")
            with open(file_path, "wb") as handle:
                handle.write(b"pptx")
            directory_result = reveal_path_in_file_manager(
                temp_dir, system_name="Windows", shell_execute=shell_execute
            )
            file_result = reveal_path_in_file_manager(
                file_path, system_name="Windows", shell_execute=shell_execute
            )

        self.assertTrue(directory_result.ok)
        self.assertEqual(directory_result.action, "open_directory")
        self.assertEqual(calls[0][2], os.path.abspath(temp_dir))
        self.assertTrue(file_result.ok)
        self.assertEqual(file_result.action, "select_file")
        self.assertEqual(calls[1][2], "explorer.exe")
        self.assertIn('/select,"', calls[1][3])
        self.assertIn("报告, final.pptx", calls[1][3])

    def test_reveal_path_reports_missing_and_shell_errors(self):
        empty = reveal_path_in_file_manager("", system_name="Windows", shell_execute=lambda *_args: 33)
        self.assertFalse(empty.ok)
        self.assertEqual(empty.error, "路径为空。")

        missing = reveal_path_in_file_manager(
            os.path.join(tempfile.gettempdir(), "definitely-missing-cowork-file"),
            system_name="Windows",
            shell_execute=lambda *_args: 33,
        )
        self.assertFalse(missing.ok)
        self.assertIn("路径不存在", missing.error)

        with tempfile.TemporaryDirectory() as temp_dir:
            failed = reveal_path_in_file_manager(
                temp_dir, system_name="Windows", shell_execute=lambda *_args: 5
            )
        self.assertFalse(failed.ok)
        self.assertIn("错误码 5", failed.error)

    def test_subprocess_kwargs_no_window_preserves_non_windows_kwargs(self):
        kwargs = subprocess_kwargs_no_window(stdout="demo")
        self.assertEqual(kwargs["stdout"], "demo")
        if os.name == "nt":
            self.assertIn("creationflags", kwargs)
            self.assertIn("startupinfo", kwargs)
        else:
            self.assertNotIn("creationflags", kwargs)
            self.assertNotIn("startupinfo", kwargs)

    def test_external_program_launch_resets_frozen_dll_search_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = os.path.join(temp_dir, "_internal")
            system_dir = os.path.join(temp_dir, "system-bin")
            os.makedirs(bundle_dir)
            os.makedirs(system_dir)
            environment = {
                "PATH": os.pathsep.join([bundle_dir, system_dir]),
                "KEEP": "yes",
            }
            process = object()
            with patch.object(
                process_utils,
                "_frozen_windows_bundle_dir",
                return_value=bundle_dir,
            ), patch.object(
                process_utils,
                "_set_windows_dll_directory",
            ) as set_dll_directory, patch.object(
                process_utils.subprocess,
                "Popen",
                return_value=process,
            ) as popen:
                result = popen_external_program(
                    [r"C:\components\bsk.exe", "doctor"],
                    env=environment,
                )

        self.assertIs(result, process)
        self.assertEqual(
            set_dll_directory.call_args_list,
            [call(None), call(bundle_dir)],
        )
        child_environment = popen.call_args.kwargs["env"]
        self.assertEqual(child_environment["PATH"], system_dir)
        self.assertEqual(child_environment["KEEP"], "yes")
        self.assertEqual(environment["PATH"], os.pathsep.join([bundle_dir, system_dir]))

    def test_external_program_launch_restores_dll_path_after_failure(self):
        bundle_dir = os.path.abspath("frozen-bundle")
        with patch.object(
            process_utils,
            "_frozen_windows_bundle_dir",
            return_value=bundle_dir,
        ), patch.object(
            process_utils,
            "_set_windows_dll_directory",
        ) as set_dll_directory, patch.object(
            process_utils.subprocess,
            "Popen",
            side_effect=OSError("launch failed"),
        ):
            with self.assertRaisesRegex(OSError, "launch failed"):
                popen_external_program(["missing.exe"])

        self.assertEqual(
            set_dll_directory.call_args_list,
            [call(None), call(bundle_dir)],
        )

    def test_runtime_debug_logging_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(runtime_debug_logging_enabled())
        with patch.dict(os.environ, {"COWORK_RUNTIME_DEBUG_LOG": "1"}, clear=True):
            self.assertTrue(runtime_debug_logging_enabled())

    def test_process_singleton_lock_blocks_second_acquire_until_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = build_process_singleton_lock_path(temp_dir, "daemon-23333")
            first = ProcessSingletonLock(lock_path)
            second = ProcessSingletonLock(lock_path)
            third = ProcessSingletonLock(lock_path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(third.acquire())
            third.release()

    def test_process_singleton_lock_is_scoped_by_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = ProcessSingletonLock(build_process_singleton_lock_path(temp_dir, "daemon-23333"))
            second = ProcessSingletonLock(build_process_singleton_lock_path(temp_dir, "im-gateway"))

            self.assertTrue(first.acquire())
            self.assertTrue(second.acquire())

            first.release()
            second.release()

    def test_process_singleton_lock_keeps_ui_scope_independent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ui_lock = ProcessSingletonLock(build_process_singleton_lock_path(temp_dir, "ui-main"))
            daemon_lock = ProcessSingletonLock(build_process_singleton_lock_path(temp_dir, "daemon-launch-23333"))

            self.assertTrue(ui_lock.acquire())
            self.assertTrue(daemon_lock.acquire())

            ui_lock.release()
            daemon_lock.release()


if __name__ == "__main__":
    unittest.main()
