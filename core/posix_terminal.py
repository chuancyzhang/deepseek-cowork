"""POSIX controlling PTY, launched without forking the multithreaded Qt host."""
import codecs
import errno
import os
import select
import signal
import struct
import subprocess
import threading
import time


class PosixPTY:
    def __init__(self, executable, args, cwd, env, cols, rows):
        import fcntl
        import pty
        import termios
        from .sandbox_runtime import get_runtime_executable
        from .process_utils import popen_external_program
        python = get_runtime_executable("python")
        if not python or not os.path.isfile(python):
            raise RuntimeError("终端需要应用 Python 运行时，请先完成运行时安装。")
        self._lock = threading.RLock()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._eof = False
        self._closed = False
        self.process = None
        self.master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            # Popen creates a new session before exec. The single-threaded helper
            # attaches its controlling terminal and then replaces itself with shell.
            helper = ("import fcntl,os,sys,termios;"
                      "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
                      "os.execvpe(sys.argv[1],sys.argv[1:],os.environ)")
            self.process = popen_external_program(
                [python, "-I", "-c", helper, executable, *args],
                stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                start_new_session=True, close_fds=True, preserve_path=True,
            )
            self.pid = self.process.pid
            os.set_blocking(self.master, False)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)

    def read(self, blocking=False):
        with self._lock:
            if self._closed or self._eof:
                raise EOFError
            if not select.select([self.master], [], [], 0)[0]:
                return ""
            try:
                raw = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EAGAIN:
                    return ""
                if exc.errno != errno.EIO:
                    raise
                raw = b""
            if not raw:
                self._eof = True
                return self._decoder.decode(b"", final=True)
            return self._decoder.decode(raw)

    def write(self, text):
        data = memoryview(text.encode("utf-8"))
        while data:
            with self._lock:
                if self._closed:
                    raise RuntimeError("终端已关闭。")
                try:
                    sent = os.write(self.master, data)
                except BlockingIOError:
                    sent = 0
            if not sent:
                # Bounded: a shell that does not consume input must not freeze UI.
                if not select.select([], [self.master], [], .1)[1]:
                    raise RuntimeError("终端暂时无法接收更多输入，请稍后重试。")
            data = data[sent:]

    def set_size(self, cols, rows):
        import fcntl
        import termios
        with self._lock:
            if not self._closed:
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def iseof(self):
        return self._eof

    def isalive(self):
        # Drain PTY output through EOF, even after a short-lived command exits.
        return not self._eof

    def get_exitstatus(self):
        try:
            return self.process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            return None

    def cancel_io(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                groups = self._owned_process_groups()
            finally:
                # Always release the descriptor, including when inspection fails.
                # Closing the controlling terminal sends SIGHUP to the foreground job.
                os.close(self.master)
        for group in groups:
            try:
                os.killpg(group, signal.SIGHUP)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            pass
        # Interactive shells put foreground/background jobs in separate groups.
        # Recheck the session: never signal another terminal's group, or rely on
        # the shell forwarding HUP (jobs may ignore it).
        for group in self._owned_process_groups():
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait(timeout=1)
        deadline = time.monotonic() + 1
        while self._owned_process_groups():
            if time.monotonic() >= deadline:
                raise RuntimeError("终端进程尚未确认退出，请检查终端诊断记录。")
            time.sleep(.02)

    def _owned_process_groups(self):
        from .process_utils import popen_external_program
        ps = popen_external_program(["/bin/ps", "-axo", "pid=,stat="], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            output, _ = ps.communicate(timeout=2)
        except BaseException:
            ps.kill()
            ps.wait()
            raise
        if ps.returncode:
            raise RuntimeError("无法确认终端所属进程，终端清理失败。")
        groups = set()
        for line in output.splitlines():
            raw, state = line.split(maxsplit=1)
            if state.startswith(b"Z"):
                continue  # Already exited, awaiting its parent's reap.
            try:
                pid = int(raw)
                if os.getsid(pid) == self.pid:
                    groups.add(os.getpgid(pid))
            except (ProcessLookupError, PermissionError):
                continue
        return groups

