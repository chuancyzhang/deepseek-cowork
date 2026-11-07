"""Contract tests plus native POSIX checks (run on macOS/Linux, not mocked Windows)."""
import base64
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core.credential_protection import CredentialProtectionError, KeyringProtector
from core.platform_capabilities import desktop_target
from core.update_manifest import release_filename, validate_manifest


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.writes = 0

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value
        self.writes += 1


class CredentialTests(unittest.TestCase):
    def test_authenticated_round_trip_and_fresh_nonce(self):
        with tempfile.TemporaryDirectory() as root:
            backend = MemoryKeyring()
            first = KeyringProtector(root, backend)
            second = KeyringProtector(root, backend)
            ciphertext = first.protect("密钥值".encode())
            self.assertNotEqual(ciphertext, second.protect("密钥值".encode()))
            self.assertNotIn("密钥值".encode(), ciphertext)
            self.assertEqual(second.unprotect(ciphertext), "密钥值".encode())
            self.assertEqual(backend.writes, 1)
            damaged = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
            with self.assertRaisesRegex(CredentialProtectionError, "校验失败"):
                first.unprotect(damaged)

    def test_missing_key_never_recreated_on_read(self):
        with tempfile.TemporaryDirectory() as root:
            backend = MemoryKeyring()
            protector = KeyringProtector(root, backend)
            encrypted = protector.protect(b"secret")
            backend.values.clear()
            with self.assertRaisesRegex(CredentialProtectionError, "缺少解密密钥"):
                protector.unprotect(encrypted)
            self.assertEqual(backend.writes, 1)

    def test_keyring_failure_does_not_publish_secret(self):
        from core.variable_store import VariableStore, VariableStoreError
        with tempfile.TemporaryDirectory() as root:
            backend = MemoryKeyring()
            store = VariableStore(root, KeyringProtector(root, backend))
            store.upsert("ordinary", "text", "keep")
            before = Path(store.path).read_bytes()
            with patch.object(backend, "set_password", side_effect=RuntimeError("locked")):
                with self.assertRaises(VariableStoreError):
                    store.upsert("token", "secret", "do-not-persist")
            self.assertEqual(Path(store.path).read_bytes(), before)

    def test_corrupt_key_and_foreign_envelope_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            backend = MemoryKeyring()
            protector = KeyringProtector(root, backend)
            backend.set_password(protector.service, protector.account, base64.b64encode(b"short").decode())
            with self.assertRaises(CredentialProtectionError):
                protector.protect(b"value")
            with self.assertRaisesRegex(CredentialProtectionError, "Windows"):
                protector.unprotect(b"DPAPI-data")


class PlatformContractTests(unittest.TestCase):
    def test_terminal_closes_descriptor_when_process_inspection_fails(self):
        from core.posix_terminal import PosixPTY
        terminal = object.__new__(PosixPTY)
        terminal._lock = threading.RLock()
        terminal._closed = False
        terminal.master = 12345
        with patch.object(terminal, "_owned_process_groups", side_effect=RuntimeError("ps failed")), \
             patch("core.posix_terminal.os.close") as close:
            with self.assertRaisesRegex(RuntimeError, "ps failed"):
                terminal.cancel_io()
        close.assert_called_once_with(12345)

    def test_frozen_external_program_restores_library_environment(self):
        from core import process_utils
        from types import SimpleNamespace
        fake_os = SimpleNamespace(name="posix", environ=os.environ, path=os.path, pathsep=os.pathsep)
        environment = {"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/system/lib",
                       "DYLD_LIBRARY_PATH": "/bundle", "PATH": "/usr/bin", "KEEP": "yes"}
        with patch.object(process_utils, "os", fake_os), patch.object(process_utils.sys, "frozen", True, create=True), \
             patch.object(process_utils.sys, "_MEIPASS", "/bundle", create=True), \
             patch.object(process_utils.subprocess, "Popen") as launch:
            process_utils.popen_external_program(["soffice"], env=environment)
        child = launch.call_args.kwargs["env"]
        self.assertEqual(child["LD_LIBRARY_PATH"], "/system/lib")
        self.assertNotIn("DYLD_LIBRARY_PATH", child)
        self.assertEqual(environment["LD_LIBRARY_PATH"], "/bundle")

    def test_supported_architectures_and_manifest_cannot_cross_platforms(self):
        for system, machine, target in (("Windows", "AMD64", "windows-x64"),
                                         ("Darwin", "arm64", "macos-arm64"),
                                         ("Linux", "x86_64", "linux-x64")):
            self.assertEqual(desktop_target(system, machine), target)
            target_os, arch = target.split("-")
            manifest = dict(schema_version=1, version="5.3.4", target_os=target_os, arch=arch,
                            filename=release_filename("5.3.4", target), size=100, sha256="ab" * 32)
            self.assertEqual(validate_manifest(manifest, target)["version"], "5.3.4")
            if target != "windows-x64":
                with self.assertRaises(ValueError):
                    validate_manifest(manifest)
        for system, machine in (("Darwin", "x86_64"), ("Linux", "aarch64"), ("Windows", "arm64")):
            with self.assertRaises(RuntimeError):
                desktop_target(system, machine)

    def test_non_windows_speech_requires_explicit_remote_configuration(self):
        from core.speech_to_text_config import validate_speech_to_text_config
        with patch("core.speech_to_text_config.is_windows", return_value=False):
            with self.assertRaisesRegex(ValueError, "还缺少"):
                validate_speech_to_text_config({})
            with self.assertRaisesRegex(ValueError, "尚未上传"):
                validate_speech_to_text_config({"ASR_BACKEND": "local"})
            self.assertEqual(validate_speech_to_text_config(dict(
                ASR_API_URL="https://example.test/transcribe", ASR_MODEL_NAME="asr", ASR_API_KEY="key"))["backend"], "openai_compatible")

    def test_runtime_archive_rejects_escape_and_preserves_executable(self):
        from core.runtime_components import safe_extract_runtime_tar
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "runtime.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                item = tarfile.TarInfo("bin/node")
                item.mode, item.size = 0o755, 3
                stream.addfile(item, io.BytesIO(b"abc"))
            target = Path(root) / "valid"
            safe_extract_runtime_tar(archive, target)
            self.assertEqual((target / "bin/node").read_bytes(), b"abc")
            if os.name != "nt":
                self.assertTrue(os.access(target / "bin/node", os.X_OK))
            with tarfile.open(archive, "w:gz") as stream:
                item = tarfile.TarInfo("../escape")
                stream.addfile(item, io.BytesIO())
            with self.assertRaises(tarfile.FilterError):
                safe_extract_runtime_tar(archive, Path(root) / "invalid")
            self.assertFalse((Path(root) / "escape").exists())


@unittest.skipIf(os.name == "nt", "requires a real POSIX kernel")
class NativePosixTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("soffice"), "optional LibreOffice not installed")
    def test_real_libreoffice_readonly_conversion_and_cancel(self):
        from docx import Document
        from core.document_preview import render_office_pdf
        from core.workbench import WorkbenchError
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory(prefix="中文 office ") as root:
            source = Path(root) / "中文 report.docx"
            document = Document()
            document.add_paragraph("Cross platform preview 中文预览")
            document.save(source)
            before = source.read_bytes()
            output, version = render_office_pdf(source, root=Path(root) / "cache")
            self.assertEqual(source.read_bytes(), before)
            self.assertTrue(PdfReader(output).pages)
            self.assertEqual(render_office_pdf(source, root=Path(root) / "cache"), (output, version))
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(WorkbenchError) as raised:
                render_office_pdf(source, root=Path(root) / "cache", cancel=cancel, force=True)
            self.assertEqual(raised.exception.code, "cancelled")
            self.assertEqual(source.read_bytes(), before)

    def test_lock_survives_atomic_replace_and_nested_acquisition(self):
        from core.file_write_locks import file_write_lock
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "file.txt"
            target.write_text("original")
            child = None
            with file_write_lock(target):
                with file_write_lock(target):
                    replacement = Path(root) / "replacement"
                    replacement.write_text("new")
                    os.replace(replacement, target)
                child = subprocess.Popen([sys.executable, "-B", "-c",
                    "import sys; from core.file_write_locks import file_write_lock; "
                    "print('ready',flush=True); "
                    "\nwith file_write_lock(sys.argv[1]): print('acquired',flush=True)", str(target)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(child.stdout.readline().strip(), "ready")
                time.sleep(.15)
                self.assertIsNone(child.poll())
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            self.assertIn("acquired", out)

    def test_pty_unicode_resize_interrupt_exit_and_cleanup(self):
        from core.posix_terminal import PosixPTY
        with tempfile.TemporaryDirectory(prefix="中文 terminal ") as root:
            with patch("core.sandbox_runtime.get_runtime_executable", return_value=sys.executable):
                terminal = PosixPTY("/bin/bash", ["--noprofile", "--norc", "-i"], root,
                                    dict(os.environ, TERM="xterm-256color", PS1="PROMPT> "), 80, 24)
            def until(token):
                text = ""
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    try:
                        text += terminal.read()
                    except EOFError:
                        break
                    if token in text:
                        return text
                    time.sleep(.01)
                self.fail(f"PTY missing {token!r}: {text!r}")
            try:
                until("PROMPT>")
                terminal.write("stty -echo\n")
                until("PROMPT>")
                terminal.write("printf '你好'; pwd\n")
                self.assertIn(root, until("PROMPT>"))
                terminal.set_size(101, 37)
                terminal.write("stty size\n")
                self.assertIn("37 101", until("PROMPT>"))
                terminal.write("sleep 30\n")
                time.sleep(.1)
                terminal.write("\x03")
                until("PROMPT>")
                terminal.write("(trap '' HUP; exec sleep 30) & echo BG_PID:$!\n")
                background = int(re.search(r"BG_PID:(\d+)", until("PROMPT>")).group(1))
            finally:
                terminal.cancel_io()
            self.assertIsNotNone(terminal.process.poll())
            # Linux PID 1 can retain a zombie; a zombie is no longer executing.
            status = Path(f"/proc/{background}/stat")
            if status.exists():
                self.assertEqual(status.read_text().split()[2], "Z")
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(background, 0)


if __name__ == "__main__":
    unittest.main()
