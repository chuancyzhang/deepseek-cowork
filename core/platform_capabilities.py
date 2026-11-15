"""Desktop release scope. Windows retains its existing capability set."""
import platform
import sys

_WINDOWS_ONLY_SKILLS = {
    "browser-automation": "此平台首版仅支持应用内浏览器，暂不接管外部浏览器。",
    "wecom-unified": "此平台首版暂不提供企业微信办公套件；机器人消息渠道独立配置。",
}


def is_windows():
    return sys.platform == "win32"


def desktop_target(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "Linux" and machine in {"amd64", "x86_64"}:
        return "linux-x64"
    raise RuntimeError(f"当前发行版不支持 {system}/{machine}。目标平台为 Windows x64、Apple Silicon 和统信 V25 x86_64。")


def skill_unavailable_reason(name):
    return "" if is_windows() else _WINDOWS_ONLY_SKILLS.get(str(name or ""), "")


def require_windows_capability(label):
    if not is_windows():
        raise RuntimeError(f"此平台首版暂不提供{label}。")
