"""Execute the actual installer against isolated installations, never the user's app."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.updater import build_update_plan, write_update_plan, create_windows_update_script, launch_windows_update_script
from core.runtime_logs import collect_files, iter_records
from core.process_utils import subprocess_kwargs_no_window


@unittest.skipUnless(os.name == "nt", "Windows installer integration")
class WindowsInstallerTests(unittest.TestCase):
    def test_discovery_ignores_unrelated_processes_and_finds_reverse_order_children(self):
        from core.update_installer import INSTALLER_SCRIPT
        names = [("Within", "App-Path"), ("App-Path", "Safe-Path"), ("Discover-Owned", "Live-Owned")]
        functions = "\n".join(INSTALLER_SCRIPT[INSTALLER_SCRIPT.index("function " + first):INSTALLER_SCRIPT.index("function " + second)] for first, second in names)
        probe = self.root / "discovery.ps1"
        probe.write_text("""$ErrorActionPreference='Stop'
$install='C:\\FixtureApp'; $root=$PSScriptRoot; $script:owned=@{}; $script:committed=$false; $Recover=$false
$script:probed=@()
function Write-Diagnostic($phase,$fields=@{}) {}
function Get-CimInstance {
 [PSCustomObject]@{ProcessId=202;ParentProcessId=201;ExecutablePath='C:\\Elsewhere\\child.exe'}
 foreach($number in 1000..6000) { [PSCustomObject]@{ProcessId=$number;ParentProcessId=0;ExecutablePath=$null} }
 [PSCustomObject]@{ProcessId=201;ParentProcessId=0;ExecutablePath='C:\\FixtureApp\\app.exe'}
}
function Get-Process($Id) { $script:probed+= $Id; if($Id -notin @(201,202)){throw 'unrelated process queried'}; return [PSCustomObject]@{Id=$Id} }
function Identity($process) { return 'identity-'+$process.Id }
""" + functions + "\nDiscover-Owned; if($script:owned.Count -ne 2){throw 'descendant missing'}; if($script:probed.Count -gt 4){throw 'repeated probes'}; 'DISCOVERY_OK'", encoding="utf-8-sig")
        result = subprocess.run([self.ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe)], capture_output=True, timeout=15,
                                **subprocess_kwargs_no_window())
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"DISCOVERY_OK", result.stdout)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-updater-qa-")
        self.root = Path(self.temp.name)
        self.install = self.root / "install"; self.source = self.root / "source"
        for folder in (self.install, self.source):
            (folder / "_internal").mkdir(parents=True)
            (folder / "deepseek-cowork.exe").write_bytes(b"fixture, not a runnable exe")
        (self.install / "_internal" / "data.txt").write_text("old")
        (self.source / "_internal" / "data.txt").write_text("new")
        (self.source / "_internal" / "added.txt").write_text("added")
        (self.install / "user_data").mkdir()
        (self.install / "user_data" / "private.txt").write_text("keep")
        self.ps = str(Path(os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"))
        self.children = []
        from unittest.mock import patch
        self.environment = patch.dict(os.environ, {"COWORK_APP_DATA_DIR": str(self.root / "app-data")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
        self.temp.cleanup()

    def prepare(self, layout="qt"):
        plan = build_update_plan(str(self.install), str(self.source), layout=layout)
        path = write_update_plan(plan, str(self.root))
        script = create_windows_update_script(str(self.install), str(self.source), path, current_pid=os.getpid(), target_dir=str(self.root / "updates"), version="9.0.0", layout=layout)
        return Path(script)

    def diagnostic_events(self, transaction):
        files, issues = collect_files(self.root)
        self.assertFalse(issues)
        events = []
        for file in files:
            if file.source != "update":
                continue
            for record in iter_records(file):
                self.assertFalse(record.decode_errors)
                event = json.loads(record.text.split("] ", 2)[2])
                if event.get("transaction") == transaction:
                    events.append(event)
        return events

    def test_mutex_conflict_reports_real_error_and_running_installer_without_writing(self):
        first = self.prepare()
        owner = launch_windows_update_script(str(first))
        self.children.append(owner)
        deadline = time.monotonic() + 20
        while not (first.parent / "ready.json").exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue((first.parent / "ready.json").exists(), (first.parent / "update-launch.log").read_text(encoding="utf-8"))
        second = self.prepare()
        contender = launch_windows_update_script(str(second))
        self.children.append(contender)
        self.assertEqual(contender.wait(timeout=20), 1)
        self.assertFalse((second.parent / "ready.json").exists())
        result = json.loads((second.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "startup_failed")
        self.assertEqual(result["stage"], "mutex_wait")
        self.assertFalse(result["mutex_created"])
        self.assertFalse(result["mutex_acquired"])
        self.assertIn("另一个安装程序", result["error"])
        events = self.diagnostic_events(second.parent.name)
        acquisition = next(event for event in events if event["phase"] == "mutex_acquire_result")
        self.assertFalse(acquisition["acquired"])
        snapshot = next(event for event in events if event["phase"] == "installer_process_snapshot")
        self.assertIn(owner.pid, [item["pid"] for item in snapshot["candidates"]])
        identity = next(event for event in events if event["phase"] == "installer_process_identity")
        self.assertEqual(identity["process"]["parent_pid"], os.getpid())
        self.assertNotIn("CommandLine", json.dumps(events))
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertIn("另一个安装程序", (second.parent / "update-launch.log").read_text(encoding="utf-8"))
        (first.parent / "abort.json").write_text(json.dumps({"id": first.parent.name}))
        self.assertEqual(owner.wait(timeout=5), 0)

    def test_early_configuration_and_theme_errors_are_utf8_and_exportable(self):
        for failure in ("configuration", "form_initialize"):
            with self.subTest(stage=failure):
                script = self.prepare()
                config_path = script.parent / "config.json"
                if failure == "configuration":
                    config_path.write_text("{invalid", encoding="utf-8")
                else:
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    config["theme"] = {"bg": "rgb(255, 255, 255)"}
                    config_path.write_text(json.dumps(config), encoding="utf-8")
                child = launch_windows_update_script(str(script))
                self.children.append(child)
                self.assertEqual(child.wait(timeout=20), 1)
                result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
                self.assertEqual(result["stage"], failure)
                self.assertEqual(result["phase"], "startup_failed")
                self.assertGreater(result["script_line"], 0)
                self.assertTrue(result["error_type"])
                self.assertFalse((script.parent / "ready.json").exists())
                events = self.diagnostic_events(script.parent.name)
                self.assertTrue(any(event["phase"] == "startup_failed" for event in events))
                (script.parent / "update-launch.log").read_text(encoding="utf-8")
                self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")

    def execute(self, script, commit=True, recover=False):
        if commit:
            (script.parent / "commit.json").write_text(json.dumps({"id": script.parent.name}), encoding="utf-8")
        result = subprocess.run([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(script), "-Headless", *(["-Recover"] if recover else [])], capture_output=True, timeout=75, **subprocess_kwargs_no_window())
        self.assertTrue((script.parent / "ready.json").exists(), result.stderr.decode(errors="replace"))
        return result

    def test_no_permission_no_file_mutation(self):
        script = self.prepare()
        self.execute(script, commit=False)
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "old")
        self.assertFalse((self.install / "_internal" / "added.txt").exists())
        acquisition = next(event for event in self.diagnostic_events(script.parent.name)
                           if event["phase"] == "mutex_acquire_result")
        self.assertTrue(acquisition["created_new"])
        self.assertTrue(acquisition["acquired"])

    def test_delayed_launch_after_cancel_exits_without_ready_or_file_mutation(self):
        script = self.prepare()
        (script.parent / "abort.json").write_text(json.dumps({"id": script.parent.name}))
        result = subprocess.run([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(script), "-Headless"],
                                capture_output=True, timeout=20, **subprocess_kwargs_no_window())
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertFalse((script.parent / "ready.json").exists())
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "old")
        self.assertFalse((self.install / "_internal" / "added.txt").exists())

    def test_gui_handshake_is_hidden_until_committed_parent_exits(self):
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **subprocess_kwargs_no_window())
        self.children.append(parent)
        script = self.prepare()
        config_path = script.parent / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["wait_pids"] = [parent.pid]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        # Exercise the real WinForms message loop without a visible QA window.
        source = script.read_text(encoding="utf-8-sig").replace(
            "$form.MaximizeBox=$false; $form.MinimizeBox=$true",
            "$form.MaximizeBox=$false; $form.MinimizeBox=$true; $form.Opacity=0; $form.ShowInTaskbar=$false")
        source = source.replace("$context=New-Object Windows.Forms.ApplicationContext", """
$qaCloseTimer=New-Object Windows.Forms.Timer; $qaCloseTimer.Interval=100
$qaCloseTimer.Add_Tick({ if($form.Visible -and -not $script:busy -and (Test-Path -LiteralPath (Join-Path $root 'qa-close'))) { $qaCloseTimer.Stop(); $form.Close() } })
$qaCloseTimer.Start()
$context=New-Object Windows.Forms.ApplicationContext
""")
        script.write_text(source, encoding="utf-8-sig")
        child = subprocess.Popen([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(script)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **subprocess_kwargs_no_window())
        self.children.append(child)
        deadline = time.monotonic() + 20
        while not (script.parent / "ready.json").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue((script.parent / "ready.json").exists())
        self.assertNotIn("installer_window_shown", json.dumps(self.diagnostic_events(script.parent.name)))
        (script.parent / "commit.json").write_text(json.dumps({"id": script.parent.name}))
        time.sleep(.6)
        self.assertIsNone(child.poll())
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertNotIn("installer_window_shown", json.dumps(self.diagnostic_events(script.parent.name)))
        parent.terminate(); parent.wait(timeout=5)
        deadline = time.monotonic() + 20
        while not (script.parent / "transaction.json").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        while time.monotonic() < deadline:
            transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
            if transaction["phase"] == "installed_not_opened":
                break
            time.sleep(.05)
        self.assertEqual(transaction["phase"], "installed_not_opened",
                         (script.parent / "result.json").read_text(encoding="utf-8-sig") if (script.parent / "result.json").exists() else "no failure result")
        self.assertIn("installer_window_shown", json.dumps(self.diagnostic_events(script.parent.name)))
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "new")
        # A failed window retains ownership while its recovery actions remain usable.
        second = self.prepare()
        contender = launch_windows_update_script(str(second))
        self.children.append(contender)
        self.assertEqual(contender.wait(timeout=20), 1)
        failure = json.loads((second.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(failure["stage"], "mutex_wait")
        self.assertFalse(failure["mutex_acquired"])
        # Closing that window must release ownership so a fresh install can start.
        (script.parent / "qa-close").touch()
        self.assertEqual(child.wait(timeout=5), 1)  # Closing a failed install must not report success.
        third = self.prepare()
        restarted = launch_windows_update_script(str(third))
        self.children.append(restarted)
        deadline = time.monotonic() + 20
        while not (third.parent / "ready.json").exists() and restarted.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue((third.parent / "ready.json").exists())
        (third.parent / "abort.json").write_text(json.dumps({"id": third.parent.name}))
        self.assertEqual(restarted.wait(timeout=5), 0)

    def test_hidden_gui_handshake_cancels_without_showing_or_installing(self):
        script = self.prepare()
        child = subprocess.Popen([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(script)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **subprocess_kwargs_no_window())
        self.children.append(child)
        deadline = time.monotonic() + 20
        while not (script.parent / "ready.json").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue((script.parent / "ready.json").exists())
        (script.parent / "abort.json").write_text(json.dumps({"id": script.parent.name}))
        self.assertEqual(child.wait(timeout=5), 0)
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")

    def test_hidden_wait_without_commit_times_out_and_releases_mutex(self):
        script = self.prepare()
        source = script.read_text(encoding="utf-8-sig").replace("$waitStarted.Elapsed.TotalSeconds -ge 60", "$waitStarted.Elapsed.TotalSeconds -ge 0.5")
        script.write_text(source, encoding="utf-8-sig")
        child = launch_windows_update_script(str(script))
        self.children.append(child)
        self.assertEqual(child.wait(timeout=20), 0)
        self.assertTrue((script.parent / "abort.json").exists())
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertIn("mutex_released", [e["phase"] for e in self.diagnostic_events(script.parent.name)])

    def test_committed_live_parent_times_out_without_killing_or_writing(self):
        script = self.prepare()
        source = script.read_text(encoding="utf-8-sig").replace("$script:commitObservedAt -ge 45", "$script:commitObservedAt -ge 0.5")
        source = source.replace("$form.MaximizeBox=$false; $form.MinimizeBox=$true",
            "$form.MaximizeBox=$false; $form.MinimizeBox=$true; $form.Opacity=0; $form.ShowInTaskbar=$false")
        source = source.replace("$context=New-Object Windows.Forms.ApplicationContext", """
$qaCloseTimer=New-Object Windows.Forms.Timer; $qaCloseTimer.Interval=100
$qaCloseTimer.Add_Tick({if($form.Visible -and -not $script:busy -and (Test-Path -LiteralPath (Join-Path $root 'result.json'))) {$qaCloseTimer.Stop();$form.Close()}})
$qaCloseTimer.Start()
$context=New-Object Windows.Forms.ApplicationContext
""")
        script.write_text(source, encoding="utf-8-sig")
        (script.parent / "commit.json").write_text(json.dumps({"id": script.parent.name}))
        child = launch_windows_update_script(str(script))
        self.children.append(child)
        self.assertEqual(child.wait(timeout=20), 1)
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "parent_exit_failed")
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertFalse((script.parent / "abort.json").exists())

    def test_gui_rollback_reopens_previous_version_and_exits_with_failure_result(self):
        from core.app_version import APP_VERSION
        self.compile_relaunch_fixture(receipt_version=APP_VERSION)
        (self.install / "deepseek-cowork.exe").write_bytes((self.source / "deepseek-cowork.exe").read_bytes())
        script = self.prepare()
        source = script.read_text(encoding="utf-8-sig")
        source = source.replace("$form.MaximizeBox=$false; $form.MinimizeBox=$true",
            "$form.MaximizeBox=$false; $form.MinimizeBox=$true; $form.Opacity=0; $form.ShowInTaskbar=$false")
        operation = "   Copy-File (Safe-Path $source $item.path) (Safe-Path $install $item.path)"
        source = source.replace(operation, "   if($item.path -eq '_internal/data.txt'){throw 'Injected write failure'}\n" + operation)
        script.write_text(source, encoding="utf-8-sig")
        config = json.loads((script.parent / "config.json").read_text())
        config["wait_pids"] = [2147483647]
        (script.parent / "config.json").write_text(json.dumps(config))
        (script.parent / "commit.json").write_text(json.dumps({"id": script.parent.name}))
        child = launch_windows_update_script(str(script))
        self.children.append(child)
        self.assertEqual(child.wait(timeout=25), 1)
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "rolled_back")
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertFalse((self.install / "_internal/added.txt").exists())
        self.assertIn("previous_version_opened", [e["phase"] for e in self.diagnostic_events(script.parent.name)])

    def test_actual_copy_preserves_data_and_reports_relaunch_failure(self):
        script = self.prepare()
        result = self.execute(script)
        details = (script.parent / "result.json").read_text(encoding="utf-8-sig") if (script.parent / "result.json").exists() else result.stderr.decode(errors="replace")
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "new", details)
        self.assertEqual((self.install / "user_data" / "private.txt").read_text(), "keep")
        transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(transaction["phase"], "installed_not_opened")

    def prepare_move(self):
        from core.update_coordinator import atomic_json
        from core.update_installer import write_installer
        stage = self.install / ".cowork-update" / ("a" * 32)
        stage.mkdir(parents=True)
        staged = self.source.rename(stage / "files")
        plan = stage / "plan.json"
        atomic_json(plan, build_update_plan(str(self.install), str(staged)))
        import hashlib
        script = write_installer(str(self.install), str(staged), str(plan), str(self.root / "updates"), [os.getpid()],
                                 "deepseek-cowork.exe", version="9.0.0", plan_digest=hashlib.sha256(plan.read_bytes()).hexdigest())
        return Path(script), stage

    def test_prepared_files_move_once_and_backups_clear_after_startup(self):
        self.compile_relaunch_fixture(receipt_version="9.0.0")
        script, stage = self.prepare_move()
        inode = (stage / "files/_internal/data.txt").stat().st_ino
        result = self.execute(script)
        self.assertEqual(result.returncode, 0, (script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual((self.install / "_internal/data.txt").stat().st_ino, inode)
        self.assertFalse(stage.exists())
        self.assertEqual((self.install / "user_data/private.txt").read_text(), "keep")

    def test_recover_between_backup_and_placement_moves(self):
        script, stage = self.prepare_move()
        marker = "    if($item.sha256) { Move-UpdateFile (Safe-Path $source $item.path) $dest }"
        interrupted = script.read_text(encoding="utf-8-sig").replace(
            marker, "    if($item.before_sha256) { exit 77 }\n" + marker, 1)
        script.write_text(interrupted, encoding="utf-8-sig")
        self.assertEqual(self.execute(script).returncode, 77)
        self.assertFalse((self.install / "_internal/data.txt").exists())
        self.assertEqual((stage / "backup/_internal/data.txt").read_text(), "old")
        self.execute(script, recover=True)
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertFalse((self.install / "_internal/added.txt").exists())
        self.assertEqual((stage / "files/_internal/added.txt").read_text(), "added")

    def test_long_path_copy_delete_backup_and_rollback(self):
        from core.platform_runtime import platform_runtime
        import shutil
        relative = "_internal/" + "nested/" * 40
        def native(folder, name):
            path = platform_runtime.filesystem_path(folder / relative / name)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        try:
            native(self.install, "changed.txt").write_text("old")
            native(self.source, "changed.txt").write_text("new")
            native(self.install, "removed.txt").write_text("restore me")
            native(self.source, "added.txt").write_text("addition")
            script = self.prepare()
            self.execute(script)
            details = (script.parent / "result.json").read_text(encoding="utf-8-sig") if (script.parent / "result.json").exists() else "no result"
            self.assertEqual(native(self.install, "changed.txt").read_text(), "new", details)
            self.assertFalse(native(self.install, "removed.txt").exists())
            self.assertEqual(native(self.install, "added.txt").read_text(), "addition")
            state = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
            state["phase"] = "writing"  # Simulate interruption after the writes, using its durable journal.
            (script.parent / "transaction.json").write_text(json.dumps(state))
            self.execute(script, recover=True)
            self.assertEqual(native(self.install, "changed.txt").read_text(), "old")
            self.assertEqual(native(self.install, "removed.txt").read_text(), "restore me")
            self.assertFalse(native(self.install, "added.txt").exists())
            self.assertEqual((self.install / "user_data/private.txt").read_text(), "keep")
        finally:
            # Only this test's freshly-created temporary installation is removed.
            shutil.rmtree(platform_runtime.filesystem_path(self.root))

    def compile_relaunch_fixture(self, exit_code=0, receipt_version=None):
        csc = Path(os.environ["SystemRoot"], "Microsoft.NET/Framework64/v4.0.30319/csc.exe")
        if not csc.exists():
            self.skipTest(".NET Framework C# compiler unavailable")
        code = self.root / "relaunch.cs"
        receipt = ""
        if receipt_version is not None:
            receipt = ('File.WriteAllText(Environment.GetEnvironmentVariable("COWORK_UPDATE_RECEIPT"), '
                       '"{\\\"id\\\":\\\"" + Environment.GetEnvironmentVariable("COWORK_UPDATE_ID") + '
                       f'"\\\",\\\"version\\\":\\\"{receipt_version}\\\"}}"); ')
        code.write_text('using System; using System.IO; class Program { static int Main() { '
                        'File.WriteAllText(Environment.GetEnvironmentVariable("COWORK_UPDATE_RECEIPT") + ".exited", "ready"); '
                        + receipt +
                        f'return {exit_code};' + ' } }')
        exe = self.source / "deepseek-cowork.exe"
        exe.unlink()
        result = subprocess.run([str(csc), "/nologo", "/target:winexe", "/out:" + str(exe), str(code)],
                                capture_output=True, **subprocess_kwargs_no_window())
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))

    def test_clean_duplicate_exit_waits_for_actual_ui_receipt(self):
        self.compile_relaunch_fixture()
        script = self.prepare()
        receipt = script.parent / "opened.json"
        stop = threading.Event()
        def actual_owner():
            deadline = time.monotonic() + 20
            while not Path(str(receipt) + ".exited").exists():
                if stop.wait(.02) or time.monotonic() >= deadline:
                    return
            if not stop.wait(.7):
                temporary = receipt.with_suffix(".tmp")
                temporary.write_text(json.dumps({"id": script.parent.name, "version": "9.0.0", "pid": os.getpid()}))
                temporary.replace(receipt)
        owner = threading.Thread(target=actual_owner)
        owner.start()
        try:
            result = self.execute(script)
            transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(transaction["phase"], "complete", result.stderr.decode(errors="replace"))
            events = self.diagnostic_events(script.parent.name)
            self.assertTrue(any(event["phase"] == "relaunch_process_exited" and event["exit_code"] == 0 for event in events))
        finally:
            stop.set(); owner.join(timeout=2)

    def test_electron_enterprise_install_preserves_binding_and_user_data(self):
        connection = {"schema_version": 1, "config_version": 1, "platform_id": "installer-qa",
            "platform_name": "安装验证", "platform_url": "https://installer.example.invalid", "distribution_channel": "enterprise"}
        for directory in (self.install, self.source):
            for name in ("resources/app.asar", "resources/service/cowork-service.exe"):
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("old" if directory == self.install else "new")
            (directory / "desktop-release.json").write_text(json.dumps({"schema_version": 1, "app_layout": "electron",
                "target": "windows-x64", "version": "9.0.0"}), encoding="utf-8")
            (directory / "enterprise_connection.json").write_text(json.dumps(connection), encoding="utf-8")
        self.compile_relaunch_fixture(receipt_version="9.0.0")
        script = self.prepare(layout="electron")
        self.execute(script)
        transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(transaction["phase"], "complete")
        self.assertEqual((self.install / "resources/app.asar").read_text(), "new")
        self.assertEqual(json.loads((self.install / "enterprise_connection.json").read_text()), connection)
        self.assertEqual((self.install / "user_data/private.txt").read_text(), "keep")

    def test_nonzero_relaunch_exit_preserves_install_and_records_exit_code(self):
        self.compile_relaunch_fixture(exit_code=23)
        script = self.prepare()
        # A retry must not accept a stale successful receipt from an earlier launch.
        (script.parent / "opened.json").write_text(json.dumps({"id": script.parent.name, "version": "9.0.0"}))
        self.execute(script)
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "installed_not_opened")
        self.assertEqual(result["exit_code"], 23)
        self.assertGreater(result["app_pid"], 0)
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "new")

    def test_actual_version_mismatch_cannot_complete_update(self):
        self.compile_relaunch_fixture(receipt_version="8.0.0")
        script = self.prepare()
        self.execute(script)
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "installed_not_opened")
        self.assertIn("8.0.0", result["error"])
        self.assertIn("9.0.0", result["error"])
        self.assertIn("不一致", result["error"])

    def test_hash_error_keeps_original_before_write(self):
        script = self.prepare()
        (self.source / "_internal" / "data.txt").write_text("tampered")
        self.execute(script)
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "old")
        self.assertFalse((self.install / "_internal" / "added.txt").exists())
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "failed")

    def test_unrelated_file_lock_never_kills_owner(self):
        script = self.prepare()
        target = self.install / "_internal" / "data.txt"
        holder = self.root / "holder.ps1"
        ready = self.root / "holder.ready"
        # This process is outside the installation and isn't an application descendant.
        holder.write_text("$f=[IO.File]::Open('"+str(target).replace("'", "''")+"','Open','ReadWrite','None'); [IO.File]::WriteAllText('"+str(ready)+"','ready'); Start-Sleep -Seconds 90", encoding="utf-8-sig")
        child = subprocess.Popen([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(holder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **subprocess_kwargs_no_window())
        self.children.append(child)
        import time
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(ready.exists())
        self.execute(script)
        self.assertIsNone(child.poll())
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertIn("占用", result["error"])

    def test_failed_second_write_rolls_back_first_write(self):
        script = self.prepare()
        # Inject a failure at the second write, with real backup/copy/rollback IO.
        source = script.read_text(encoding="utf-8-sig")
        operation = "   Copy-File (Safe-Path $source $item.path) (Safe-Path $install $item.path)"
        self.assertIn(operation, source)
        source = source.replace(operation, "   if($item.path -eq '_internal/data.txt') { throw 'Injected disk write failure' }\n" + operation)
        script.write_text(source, encoding="utf-8-sig")
        self.execute(script)
        self.assertFalse((self.install / "_internal" / "added.txt").exists())
        result = json.loads((script.parent / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result["phase"], "rolled_back")
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "old")

    def test_owned_exe_dll_and_orphan_child_are_released(self):
        import time
        csc = Path(os.environ["SystemRoot"], "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe")
        if not csc.exists():
            self.skipTest(".NET Framework C# compiler unavailable")
        code = self.root / "fixture.cs"
        code.write_text(r'''
using System; using System.IO; using System.Diagnostics; using System.Reflection;
class Program {
 static void Main(string[] args) {
  if(args.Length>0 && args[0]=="child") {
   using(var f=File.Open(args[1],FileMode.Open,FileAccess.Read,FileShare.Read)) {
    File.WriteAllText(args[2],Process.GetCurrentProcess().Id.ToString()); System.Threading.Thread.Sleep(55000);
   } return;
  }
  if(args.Length>0 && args[0]=="hold") {
   Assembly.LoadFile(Path.GetFullPath(args[1]));
   var p=Process.Start(new ProcessStartInfo(args[2], "child \""+args[3]+"\" \""+args[4]+"\"") {UseShellExecute=false,CreateNoWindow=true});
   System.Threading.Thread.Sleep(7000); return;
  }
  string receipt=Environment.GetEnvironmentVariable("COWORK_UPDATE_RECEIPT");
  string id=Environment.GetEnvironmentVariable("COWORK_UPDATE_ID");
  if(!String.IsNullOrEmpty(receipt)) File.WriteAllText(receipt,"{\"id\":\""+id+"\",\"version\":\"9.0.0\"}");
  System.Threading.Thread.Sleep(1000);
 }
}''', encoding="utf-8")
        exe = self.install / "deepseek-cowork.exe"
        exe.unlink()
        compiled = subprocess.run([str(csc), "/nologo", "/target:winexe", "/out:"+str(exe), str(code)], capture_output=True, **subprocess_kwargs_no_window())
        self.assertEqual(compiled.returncode, 0, compiled.stdout.decode(errors="replace"))
        (self.source / exe.name).write_bytes(exe.read_bytes())
        external = self.root / "external.exe"; external.write_bytes(exe.read_bytes())
        dll_source = self.root / "fixture_lib.cs"; dll_source.write_text('public class CoworkFixture { public const string Version="old"; }')
        dll = self.install / "_internal" / "fixture.dll"
        compiled = subprocess.run([str(csc), "/nologo", "/target:library", "/out:"+str(dll), str(dll_source)], capture_output=True, **subprocess_kwargs_no_window())
        self.assertEqual(compiled.returncode, 0)
        dll_source.write_text('public class CoworkFixture { public const string Version="new"; }')
        compiled = subprocess.run([str(csc), "/nologo", "/target:library", "/out:"+str(self.source / "_internal" / "fixture.dll"), str(dll_source)], capture_output=True, **subprocess_kwargs_no_window())
        self.assertEqual(compiled.returncode, 0)
        ready = self.root / "child.pid"
        parent = subprocess.Popen([str(exe), "hold", str(dll), str(external), str(self.install / "_internal" / "data.txt"), str(ready)], **subprocess_kwargs_no_window())
        self.children.append(parent)
        deadline=time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline:
            time.sleep(.05)
        self.assertTrue(ready.exists())
        script = self.prepare()
        result = self.execute(script)
        transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig")) if (script.parent / "transaction.json").exists() else {}
        error = (script.parent / "result.json").read_text(encoding="utf-8-sig") if (script.parent / "result.json").exists() else result.stderr.decode(errors="replace")
        self.assertEqual(transaction.get("phase"), "complete", error)
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "new")
        self.assertEqual(dll.read_bytes(), (self.source / "_internal" / "fixture.dll").read_bytes())
        # The relaunched fixture acknowledges startup, then exits after one second.
        # Let that process release its EXE before TemporaryDirectory removes it.
        time.sleep(1.2)

    def test_interrupted_write_can_restore_from_persisted_journal(self):
        script = self.prepare()
        interrupted = script.read_text(encoding="utf-8-sig").replace(
            "   $index++; Status '正在安装更新…'", "   exit 77\n   $index++; Status '正在安装更新…'", 1)
        script.write_text(interrupted, encoding="utf-8-sig")
        result = self.execute(script)
        self.assertEqual(result.returncode, 77)
        self.assertTrue((self.install / "_internal" / "added.txt").exists())
        self.execute(script, recover=True)
        self.assertFalse((self.install / "_internal" / "added.txt").exists())
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "old")
        transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(transaction["phase"], "rolled_back")
