"""Static Windows release contract shared by publishing and the desktop updater."""
import re


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("更新清单必须是 JSON 对象。")
    version = data.get("version")
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("target_os") != "windows" or data.get("arch") != "x64"
            or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version)
            or data.get("filename") != f"deepseek-cowork-v{version}.zip"
            or type(data.get("size")) is not int or data["size"] <= 0
            or not isinstance(data.get("sha256"), str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", data["sha256"])
            or not isinstance(data.get("notes", ""), str)):
        raise ValueError("更新清单的版本、平台或安装包信息无效。")
    return {**data, "sha256": data["sha256"].lower(), "notes": data.get("notes", "")}
