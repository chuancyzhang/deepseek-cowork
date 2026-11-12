"""Open the original desktop data in place, with exclusive service ownership."""
from pathlib import Path
import json
import logging
from core.platform_runtime import platform_runtime

log = logging.getLogger(__name__)


class DataLease:
    """An OS-held lock; process death releases ownership without stale PID rules."""
    def __init__(self, directory):
        self.path = Path(directory) / ".desktop-service.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            if self.file.seek(0, 2) == 0:
                self.file.write(b"\0")
                self.file.flush()
            self.file.seek(0)
            platform_runtime.lock(self.file)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("此数据目录已由另一个后台服务使用，请回到已有窗口。") from exc

    def close(self):
        if not self.file.closed:
            self.file.close()


def existing_database(data_dir):
    """Validate the configured history before any normalizer can create data."""
    root = Path(data_dir).resolve()
    platform_runtime.require_legacy_closed(root)
    config_path = root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("现有配置文件格式无效，原数据已保留。")
    history = Path(config.get("chat_history_dir") or root / "chat_history").expanduser()
    if not history.is_absolute():
        raise ValueError("现有历史目录不是绝对路径，请先修正目录设置。")
    if config.get("chat_history_dir") and not history.is_dir():
        raise RuntimeError("原历史目录暂不可访问，请恢复目录连接；不会创建空白历史替代原数据。")
    database = history / "chat_history.sqlite"
    marker = root / ".desktop-upgrade.json"
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("database") == str(database) and not database.is_file():
            raise RuntimeError("已使用的历史数据库不存在，请恢复历史目录后重试。")
    log.info("desktop_data_open mode=in_place existing_database=%s", database.is_file())
    return database
