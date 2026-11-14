"""Platform-specific static release contracts; the Windows v1 format is unchanged."""
import re


def release_filename(version, target="windows-x64", *, layout="qt"):
    if layout == "electron":
        if target != "windows-x64":
            raise ValueError("此平台尚未发布新版工作台安装包。")
        return f"cowork-desktop-v{version}-{target}.zip"
    suffixes = {"windows-x64": ".zip", "macos-arm64": "-macos-arm64.dmg", "linux-x64": "-linux-x64.deb"}
    if target not in suffixes:
        raise ValueError("更新平台不受支持。")
    return f"deepseek-cowork-v{version}" + suffixes[target]


def validate_manifest(data, target="windows-x64", *, layout="qt"):
    if not isinstance(data, dict):
        raise ValueError("更新清单必须是 JSON 对象。")
    version = data.get("version")
    target_os, arch = target.split("-", 1)
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("target_os") != target_os or data.get("arch") != arch
            or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version)
            or (layout == "electron" and data.get("app_layout") != "electron")
            or (layout == "qt" and data.get("app_layout", "qt") != "qt")
            or data.get("filename") != release_filename(version, target, layout=layout)
            or type(data.get("size")) is not int or data["size"] <= 0
            or not isinstance(data.get("sha256"), str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", data["sha256"])
            or not isinstance(data.get("notes", ""), str)):
        raise ValueError("更新清单的版本、平台或安装包信息无效。")
    return {**data, "sha256": data["sha256"].lower(), "notes": data.get("notes", "")}
