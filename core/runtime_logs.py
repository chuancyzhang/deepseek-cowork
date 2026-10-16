"""File-based application diagnostics. Never manages conversation/recovery data.

Writers keep their existing events; readers use bounded file snapshots. Retention
only touches this module's explicit inventory, never a recursive data-directory glob.
"""

import atexit
import heapq
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core.task_details import redact_diagnostic


RETENTION_DAYS = 30
PAGE_SIZE = 1000
SOURCE_LABELS = {
    "startup": "应用启动", "ui_error": "界面异常", "ui_navigation": "界面与操作",
    "chat_runtime_debug": "对话运行", "chat_recovery": "对话恢复诊断",
    "daemon": "后台运行", "sub_agent_runtime": "子 Agent", "model_service": "模型服务",
    "im_gateway": "消息连接", "im_gateway_stderr": "消息连接原始输出",
    "browser_skill_runtime": "浏览器能力", "browser_service": "浏览器服务原始输出",
    "wecom_capability": "企业微信能力", "theme_debug": "主题",
    "native_crash": "原生崩溃", "app_update": "更新下载",
    "update": "更新安装", "update-launch": "安装器原始输出",
    "chat_log_export": "对话导出", "log_export": "日志导出",
    "conversation_skill_capture": "对话生成技能", "memory_update": "记忆更新",
    "favorites_runtime": "收藏任务", "attachments": "附件",
    "knowledge_library_validation": "资料库诊断", "ppt_agent_debug": "旧版办公任务",
    "python": "应用模块异常", "runtime_logs": "日志维护", "main": "应用",
}
RAW_SOURCES = {"native_crash", "im_gateway_stderr", "browser_service", "update-launch"}
_FILE_PATTERN = re.compile(r"^(?P<source>" + "|".join(re.escape(s) for s in SOURCE_LABELS) + r")-(?P<pid>\d+)\.log$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TEXT_TIME = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?)")
_JSON_TIME = re.compile(r'^\s*\{\s*"(?:timestamp|time)"\s*:\s*"([^"]+)"')
_EVENT_HEADER = re.compile(r"^\[[^\]]+\] \[source=[a-z_\-]+ pid=\d+\] ")
_WRITE_LOCK = threading.RLock()
_STATUS_LOCK = threading.RLock()
_maintenance_results = {}
_write_errors = {}
_services = {}


def data_root(data_dir=None):
    if data_dir is None:
        from core.env_utils import get_app_data_dir
        data_dir = get_app_data_dir()
    return Path(data_dir).absolute()


def local_now():
    return datetime.now().astimezone()


def source_id(source):
    source = str(source).removesuffix(".log")
    if source not in SOURCE_LABELS:
        raise ValueError(f"未登记的日志来源：{source}")
    return source


def log_path(source, data_dir=None, *, now=None, pid=None):
    """Resolve without creating files; paths must not depend on active UI state."""
    stamp = now or local_now()
    return data_root(data_dir) / "logs" / stamp.date().isoformat() / f"{source_id(source)}-{pid or os.getpid()}.log"


def _write_failure(root, exc):
    message = f"日志写入失败：{type(exc).__name__}: {exc}"
    with _STATUS_LOCK:
        _write_errors[str(root)] = redact_diagnostic(message)
    # Never recurse into logging, or let diagnostic I/O stop a conversation.
    try:
        print(redact_diagnostic(message), file=sys.stderr)
    except Exception:
        pass


def append_log(source, message, data_dir=None, *, now=None):
    root = data_root(data_dir)
    try:
        with _WRITE_LOCK:
            stamp = now or local_now()
            name = source_id(source)
            path = log_path(name, root, now=stamp)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"[{stamp.isoformat(timespec='milliseconds')}] [source={name} pid={os.getpid()}] {message}\n")
        with _STATUS_LOCK:
            _write_errors.pop(str(root), None)
        return str(path)
    except Exception as exc:
        _write_failure(root, exc)
        return ""


@dataclass(frozen=True)
class LogFile:
    path: Path
    source: str
    size: int
    mtime_ns: int
    identity: tuple
    day: object = None


@dataclass(frozen=True)
class LogRecord:
    timestamp: datetime
    text: str
    file: LogFile
    start: int
    end: int
    inferred_time: bool = False
    decode_errors: bool = False

    def sort_key(self):
        return self.timestamp.timestamp(), str(self.file.path), self.start

    def display_text(self):
        text = redact_diagnostic(self.text)
        if self.inferred_time:
            text = f"[时间未记录，使用文件修改时间 {self.timestamp.isoformat(timespec='seconds')}]\n" + text
        if self.decode_errors:
            text = "[包含无法解码的字节，以下以 � 显示；原文件未改动]\n" + text
        return text


def _linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _safe_path(path, root):
    """Reject symlink/junction traversal even for individually registered files."""
    try:
        relative = path.relative_to(root)
        current = root
        if _linked(current):
            return False
        for part in relative.parts:
            current = current / part
            if _linked(current):
                return False
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _legacy_browser_root(root):
    # An explicitly supplied test/other data directory must never collect the
    # current user's external browser diagnostics.
    if root != data_root():
        return None
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "DeepSeekCowork" / "browser-automation" / "runtime"


def collect_files(data_dir=None):
    root = data_root(data_dir)
    files, issues, seen = [], [], set()

    def add(path, source, base=root, day=None):
        if not _safe_path(path, base):
            issues.append(f"已跳过链接或目录外路径：{path}")
            return
        try:
            stat = path.stat()
            if not path.is_file() or path in seen:
                return
            seen.add(path)
            files.append(LogFile(path, source, stat.st_size, stat.st_mtime_ns,
                                 (stat.st_dev, stat.st_ino), day))
        except FileNotFoundError:
            return
        except OSError as exc:
            issues.append(f"无法读取 {path}：{exc}")

    def children(path):
        if not _safe_path(path, root):
            issues.append(f"已跳过链接目录：{path}")
            return []
        try:
            return list(path.iterdir())
        except FileNotFoundError:
            return []
        except OSError as exc:
            issues.append(f"无法列出 {path}：{exc}")
            return []

    for source in SOURCE_LABELS:
        add(root / f"{source}.log", source)
        add(root / "logs" / f"{source}.log", source)
    for directory in children(root / "logs"):
        if not _DATE_PATTERN.fullmatch(directory.name):
            continue
        try:
            day = datetime.strptime(directory.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        for path in children(directory):
            match = _FILE_PATTERN.fullmatch(path.name)
            if match:
                add(path, match["source"], day=day)
    add(root / "updates" / "app_update.log", "app_update")
    for transaction in children(root / "updates" / "transactions"):
        for source in ("update", "update-launch"):
            add(transaction / f"{source}.log", source)
    browser_root = _legacy_browser_root(root)
    if browser_root is not None and _safe_path(browser_root, browser_root):
        try:
            for path in browser_root.glob("service-start-*.log"):
                if re.fullmatch(r"service-start-[0-9a-f]{16}\.log", path.name):
                    add(path, "browser_service", base=browser_root)
        except OSError as exc:
            issues.append(f"无法读取浏览器服务日志：{exc}")
    return sorted(files, key=lambda item: (item.source, str(item.path))), issues


def _line_time(line):
    match = _TEXT_TIME.match(line) or _JSON_TIME.match(line)
    if match:
        try:
            return datetime.fromisoformat(match[1].replace(",", ".").replace("Z", "+00:00")).astimezone()
        except ValueError:
            pass
    return None


def iter_records(file, cancelled=lambda: False):
    """Group continuation lines with their event and stop at captured byte size."""
    fallback = datetime.fromtimestamp(file.mtime_ns / 1e9).astimezone()
    with file.path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        if (stat.st_dev, stat.st_ino) != file.identity or stat.st_size < file.size:
            raise OSError("日志已被替换或截短，请刷新后重试")
        start, timestamp, parts, decode_errors = 0, None, [], False
        while handle.tell() < file.size:
            if cancelled():
                raise InterruptedError("日志读取已取消")
            offset = handle.tell()
            raw = handle.readline(file.size - offset)
            if not raw:
                raise OSError("读取过程中日志被截短，请刷新后重试")
            encoding = "utf-8-sig" if offset == 0 else "utf-8"
            try:
                line = raw.decode(encoding)
                line_bad = False
            except UnicodeDecodeError:
                line = raw.decode(encoding, errors="replace")
                line_bad = True
            canonical = file.day is not None and file.source not in RAW_SOURCES
            stamp = _line_time(line) if not canonical or _EVENT_HEADER.match(line) else None
            # A crash dump may be emitted long after process_start, without its
            # own timestamp. Do not date it to that startup marker and hide it.
            raw_crash = file.source == "native_crash" and line.startswith(("Fatal Python error:", "Windows fatal exception:", "qt_fatal:"))
            if stamp is not None or raw_crash:
                if parts and any(part.strip() for part in parts):
                    yield LogRecord(timestamp or fallback, "".join(parts), file, start, offset, timestamp is None, decode_errors)
                    parts, start, decode_errors = [], offset, False
                timestamp = stamp
            parts.append(line)
            decode_errors = decode_errors or line_bad
        if parts:
            yield LogRecord(timestamp or fallback, "".join(parts), file, start, file.size, timestamp is None, decode_errors)


def date_range(days=1, now=None):
    if days not in (1, 7, 30):
        raise ValueError("日志时间范围必须为当天、最近 7 天或最近 30 天")
    end = (now or local_now()).date()
    return end - timedelta(days=days - 1), end


def selected_files(files, days=1, source="", now=None):
    start, end = date_range(days, now)
    return [file for file in files if (not source or file.source == source
                                     or (source == "im_gateway" and file.source == "im_gateway_stderr"))
            and (file.day is None or file.source in RAW_SOURCES or start <= file.day <= end)]


@dataclass
class QueryResult:
    records: list = field(default_factory=list)
    total: int = 0
    remaining: int = 0
    issues: list = field(default_factory=list)


def query_logs(data_dir=None, *, days=1, source="", keyword="", limit=PAGE_SIZE, now=None,
               before=None, cancelled=lambda: False):
    now = now or local_now()
    start, end = date_range(days, now)
    files, issues = collect_files(data_dir)
    result = QueryResult(issues=issues)
    heap = []
    keyword = keyword.casefold()
    for file in selected_files(files, days, source, now):
        try:
            reported_decode = False
            for record in iter_records(file, cancelled):
                if not start <= record.timestamp.date() <= end:
                    continue
                if record.decode_errors and not reported_decode:
                    result.issues.append(f"{file.path} 包含无法解码的字节，显示时使用替代字符；原文件未改动。")
                    reported_decode = True
                if keyword and keyword not in record.display_text().casefold():
                    continue
                result.total += 1
                key = record.sort_key()
                if before is not None and key >= before:
                    continue
                result.remaining += 1
                item = (*key, record)
                if len(heap) < limit:
                    heapq.heappush(heap, item)
                elif item[:3] > heap[0][:3]:
                    heapq.heapreplace(heap, item)
        except OSError as exc:
            if isinstance(exc, InterruptedError):
                raise
            result.issues.append(f"无法读取 {file.path}：{exc}")
    result.records = [item[3] for item in sorted(heap)]
    return result


def latest_source_path(source, data_dir=None):
    files, _ = collect_files(data_dir)
    matches = [file for file in files if file.source == source_id(source)]
    return str(max(matches, key=lambda file: file.mtime_ns).path) if matches else str(log_path(source, data_dir))


def latest_gateway_error(data_dir=None):
    markers = (" failed", " error=", " missing", " unavailable", "不可用", "未配置")
    records = query_logs(data_dir, days=30, source="im_gateway", limit=60).records
    for record in sorted(records, key=lambda item: item.timestamp, reverse=True):
        for line in reversed(record.display_text().splitlines()):
            if any(marker in line.lower() for marker in markers):
                return re.sub(r"^\[[^\]]+\]\s*(?:\[source=[^\]]+\]\s*)?", "", line)
    return ""


def validate_export_target(target, data_dir, files):
    target = Path(target).resolve()
    logs = (data_root(data_dir) / "logs").resolve()
    if target == logs or logs in target.parents:
        raise ValueError("不能将导出文件保存到运行日志目录，请选择其他保存位置。")
    for file in files:
        if target == file.path.resolve() or (target.exists() and os.path.samefile(target, file.path)):
            raise ValueError("不能覆盖正在使用的日志文件，请选择其他保存位置。")


@dataclass
class MaintenanceResult:
    removed: int = 0
    trimmed: int = 0
    issues: list = field(default_factory=list)


def maintenance_issues(data_dir=None):
    root = str(data_root(data_dir))
    with _STATUS_LOCK:
        result = _maintenance_results.get(root)
        return ([_write_errors[root]] if root in _write_errors else []) + (list(result.issues) if result else [])


def _legacy_writers_idle(root, owned_lock_paths):
    from core.process_utils import ProcessSingletonLock
    owned = {str(Path(path).absolute()) for path in owned_lock_paths}
    for pattern in ("ui-main-*.lock", "daemon-*.lock", "im-gateway-*.lock"):
        for path in root.glob(pattern):
            if path.name.startswith("daemon-launch-") or str(path) in owned:
                continue
            if not _safe_path(path, root):
                return False
            probe = ProcessSingletonLock(str(path))
            if not probe.acquire():
                return False
            probe.release()
    return True


def _same_snapshot(file):
    stat = file.path.stat()
    return (stat.st_dev, stat.st_ino) == file.identity and stat.st_size == file.size and stat.st_mtime_ns == file.mtime_ns


def _trim_legacy(file, cutoff, cancelled):
    """Copy original bytes, not decoded replacement characters; replace atomically."""
    temporary = None
    discarded = False
    kept = 0
    try:
        with tempfile.NamedTemporaryFile(dir=file.path.parent, prefix=".log-retention-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            with file.path.open("rb") as original:
                for record in iter_records(file, cancelled):
                    if record.timestamp.date() < cutoff:
                        discarded = True
                        continue
                    original.seek(record.start)
                    remaining = record.end - record.start
                    while remaining:
                        if cancelled():
                            raise InterruptedError("日志维护已取消")
                        chunk = original.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise OSError("整理过程中日志被截短")
                        output.write(chunk)
                        kept += len(chunk)
                        remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not discarded:
            return "unchanged"
        if cancelled():
            raise InterruptedError("日志维护已取消")
        if not _same_snapshot(file):
            raise OSError("日志仍在变化，原文件已保留，稍后重试")
        # Preserve fallback-time semantics for old timestamp-less fragments.
        os.utime(temporary, ns=(file.mtime_ns, file.mtime_ns))
        if not kept:
            file.path.unlink()
            return "removed"
        os.replace(temporary, file.path)
        return "trimmed"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cleanup_logs(data_dir=None, *, now=None, owned_lock_paths=(), cancelled=lambda: False):
    from core.process_utils import acquire_process_singleton
    root = data_root(data_dir)
    result = MaintenanceResult()
    cutoff, _ = date_range(RETENTION_DAYS, now)
    root.joinpath("logs").mkdir(parents=True, exist_ok=True)
    if not _safe_path(root / "logs" / "maintenance.lock", root):
        raise OSError("日志目录是链接，已停止自动清理")
    lock = acquire_process_singleton(str(root / "logs" / "maintenance.lock"))
    if lock is None:
        return result  # Another process owns today's maintenance; no duplicate work.
    try:
        files, result.issues = collect_files(root)
        idle = _legacy_writers_idle(root, owned_lock_paths)
        deferred = False
        for file in files:
            if cancelled():
                raise InterruptedError("日志维护已取消")
            try:
                if file.day is not None:
                    if file.day >= cutoff:
                        continue
                    if file.source not in RAW_SOURCES or datetime.fromtimestamp(file.mtime_ns / 1e9).astimezone().date() < cutoff:
                        if not _same_snapshot(file):
                            raise OSError("日志仍在变化，原文件已保留")
                        # On Windows an inherited/live crash descriptor prevents
                        # deletion. Report that failure; never force-close it.
                        file.path.unlink()
                        result.removed += 1
                        continue
                if not idle:
                    deferred = True
                    continue
                action = _trim_legacy(file, cutoff, cancelled)
                if action == "removed":
                    result.removed += 1
                elif action == "trimmed":
                    result.trimmed += 1
            except OSError as exc:
                if isinstance(exc, InterruptedError):
                    raise
                result.issues.append(f"未清理 {file.path}：{exc}；原文件保留，下次维护重试。")
        if deferred:
            result.issues.append("旧日志或原始输出暂未整理：其他应用后台进程仍在运行；进程退出后会在下次维护重试。")
        # Only remove empty, date-named directories; never recurse.
        for directory in root.joinpath("logs").iterdir():
            if _DATE_PATTERN.fullmatch(directory.name) and _safe_path(directory, root):
                try:
                    directory.rmdir()
                except OSError:
                    pass  # Nonempty directories contain retained or unknown files.
        with _STATUS_LOCK:
            _maintenance_results[str(root)] = result
        return result
    finally:
        lock.release()


class _ApplicationLogHandler(logging.Handler):
    def __init__(self, root):
        super().__init__()
        self.root = root

    def emit(self, record):
        if record.name.startswith(("core.", "ui.")) or record.name in {"main", "__main__"}:
            append_log("python", f"{record.levelname} {record.name} {self.format(record)}", self.root)


class LogMaintenance:
    """One cancellable daily file-maintenance thread per application process."""
    def __init__(self, root, owned_lock_paths=(), rotate_crash=None):
        self.root = root
        self.owned_lock_paths = owned_lock_paths
        self.rotate_crash = rotate_crash
        self.stop_event = threading.Event()
        self.last_day = None
        self.handler = None
        self.thread = threading.Thread(target=self._run, name="LogMaintenance", daemon=True)

    def tick(self, now=None):
        now = now or local_now()
        if now.date() == self.last_day:
            return
        if self.rotate_crash:
            self.rotate_crash()
        append_log("runtime_logs", "stage=maintenance_start", self.root)
        result = cleanup_logs(self.root, now=now, owned_lock_paths=self.owned_lock_paths,
                              cancelled=self.stop_event.is_set)
        self.last_day = now.date()
        append_log("runtime_logs", json.dumps({"stage": "maintenance_completed", "removed": result.removed,
                   "trimmed": result.trimmed, "issues": result.issues}, ensure_ascii=False), self.root)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except InterruptedError:
                break
            except Exception as exc:
                with _STATUS_LOCK:
                    _maintenance_results[str(self.root)] = MaintenanceResult(issues=[f"日志维护失败：{exc}"])
                append_log("runtime_logs", f"stage=maintenance_failed error={exc}", self.root)
                self.last_day = local_now().date()
            self.stop_event.wait(60)

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        if self.handler is not None:
            for name in ("core", "ui", "main", "__main__"):
                logging.getLogger(name).removeHandler(self.handler)
        with _STATUS_LOCK:
            if _services.get(str(self.root)) is self:
                _services.pop(str(self.root), None)


def start_log_maintenance(data_dir=None, *, owned_lock_paths=(), rotate_crash=None):
    root = data_root(data_dir)
    with _STATUS_LOCK:
        if str(root) in _services:
            return _services[str(root)]
        service = LogMaintenance(root, owned_lock_paths, rotate_crash)
        _services[str(root)] = service
        service.handler = _ApplicationLogHandler(root)
        for name in ("core", "ui", "main", "__main__"):
            logging.getLogger(name).addHandler(service.handler)
        # Preserve all existing logger levels and console handlers.
        atexit.register(service.stop)
        service.thread.start()
        return service
