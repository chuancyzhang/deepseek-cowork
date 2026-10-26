"""Execute the actual installer against isolated installations, never the user's app."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from core.updater import build_update_plan, write_update_plan, create_windows_update_script
from core.process_utils import subprocess_kwargs_no_window


@unittest.skipUnless(os.name == "nt", "Windows installer integration")
class WindowsInstallerTests(unittest.TestCase):
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

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
        self.temp.cleanup()

    def prepare(self):
        plan = build_update_plan(str(self.install), str(self.source))
        path = write_update_plan(plan, str(self.root))
        script = create_windows_update_script(str(self.install), str(self.source), path, current_pid=os.getpid(), target_dir=str(self.root / "updates"), version="9.0.0")
        return Path(script)

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
        script.write_text(source, encoding="utf-8-sig")
        child = subprocess.Popen([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(script)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **subprocess_kwargs_no_window())
        self.children.append(child)
        deadline = time.monotonic() + 20
        while not (script.parent / "ready.json").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue((script.parent / "ready.json").exists())
        log = script.parent / "update.log"
        self.assertNotIn("installer_window_shown", log.read_text(encoding="utf-8-sig") if log.exists() else "")
        (script.parent / "commit.json").write_text(json.dumps({"id": script.parent.name}))
        time.sleep(.6)
        self.assertIsNone(child.poll())
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "old")
        self.assertNotIn("installer_window_shown", log.read_text(encoding="utf-8-sig") if log.exists() else "")
        parent.terminate(); parent.wait(timeout=5)
        deadline = time.monotonic() + 20
        while not (script.parent / "transaction.json").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        while time.monotonic() < deadline:
            transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
            if transaction["phase"] == "installed_not_opened":
                break
            time.sleep(.05)
        self.assertEqual(transaction["phase"], "installed_not_opened")
        self.assertIn("installer_window_shown", log.read_text(encoding="utf-8-sig"))
        self.assertEqual((self.install / "_internal/data.txt").read_text(), "new")

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

    def test_actual_copy_preserves_data_and_reports_relaunch_failure(self):
        script = self.prepare()
        result = self.execute(script)
        details = (script.parent / "result.json").read_text(encoding="utf-8-sig") if (script.parent / "result.json").exists() else result.stderr.decode(errors="replace")
        self.assertEqual((self.install / "_internal" / "data.txt").read_text(), "new", details)
        self.assertEqual((self.install / "user_data" / "private.txt").read_text(), "keep")
        transaction = json.loads((script.parent / "transaction.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(transaction["phase"], "installed_not_opened")

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
