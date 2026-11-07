"""Pinned native runtime artifacts, shared by component installation and builds."""
from dataclasses import dataclass
from .platform_capabilities import desktop_target


@dataclass(frozen=True)
class RuntimeRelease:
    filename: str
    url: str
    sha256: str
    executable: str


_PYTHON_RELEASE = "20261026"
_PYTHON_VERSION = "3.12.16"
_PYTHON_TARGETS = {
    "macos-arm64": ("aarch64-apple-darwin", "353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e"),
    "linux-x64": ("x86_64-unknown-linux-gnu", "ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20"),
}
_NODE_TARGETS = {
    "macos-arm64": ("darwin-arm64.tar.gz", "25495ff85bd89e2d8a24d88566d7e2f827c6b0d3d872b2cebf75371f93fcb1fe", "bin/node"),
    "linux-x64": ("linux-x64.tar.xz", "84d38715d449447117d05c3e71acd78daa49d5b1bfa8aacf610303920c3322be", "bin/node"),
}
_RG_TARGETS = {
    "macos-arm64": ("aarch64-apple-darwin", "378e973289176ca0c6054054ee7f631a065874a352bf43f0fa60ef079b6ba715"),
    "linux-x64": ("x86_64-unknown-linux-musl", "1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599"),
}


def native_runtime_release(name, target=None):
    target = target or desktop_target()
    if name == "python" and target in _PYTHON_TARGETS:
        triple, digest = _PYTHON_TARGETS[target]
        filename = f"cpython-{_PYTHON_VERSION}+{_PYTHON_RELEASE}-{triple}-install_only_stripped.tar.gz"
        url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{_PYTHON_RELEASE}/{filename}"
        return RuntimeRelease(filename, url, digest, "bin/python3")
    if name == "node" and target in _NODE_TARGETS:
        suffix, digest, executable = _NODE_TARGETS[target]
        filename = f"node-v24.14.1-{suffix}"
        return RuntimeRelease(filename, f"https://nodejs.org/dist/v24.14.1/{filename}", digest, executable)
    if name == "ripgrep" and target in _RG_TARGETS:
        triple, digest = _RG_TARGETS[target]
        filename = f"ripgrep-15.1.0-{triple}.tar.gz"
        return RuntimeRelease(filename, f"https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/{filename}", digest, "rg")
    raise ValueError(f"没有 {target} 的 {name} 运行时定义。")
