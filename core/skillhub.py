"""Public SkillHub catalogue. Installed skills remain owned by SkillManager."""
import hashlib
import json
import logging
import os
import re
import time
import tempfile
from urllib.parse import quote, urljoin

import requests

from core.app_version import APP_VERSION

BASE_URL = "https://api.skillhub.cn"
ORIGIN_FILE = ".skillhub.json"
MAX_BYTES = 50 * 1024 * 1024
log = logging.getLogger(__name__)


class SkillHubCache:
    """Bounded on-disk cache for public browsing data only."""

    def __init__(self, directory):
        self.directory = directory

    def _path(self, key):
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()
        return os.path.join(self.directory, digest + ".json")

    def get(self, key):
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                entry = json.load(handle)
            if time.time() - float(entry["saved_at"]) < 60 * 86400:
                return entry["data"]
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"SkillHub 缓存读取失败，请点击重试重新获取：{exc}") from exc

    def put(self, key, data):
        temporary = None
        try:
            os.makedirs(self.directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                json.dump({"saved_at": time.time(), "data": data}, handle, ensure_ascii=False)
            os.replace(temporary, self._path(key))
            files = sorted((os.path.join(self.directory, name) for name in os.listdir(self.directory)
                            if name.endswith(".json")), key=os.path.getmtime, reverse=True)
            for path in files[32:]:
                os.remove(path)
        except OSError as exc:
            raise RuntimeError(f"SkillHub 缓存保存失败：{exc}") from exc
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)


def identifier(value):
    value = str(value or "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]{0,127}", value) or ".." in value:
        raise ValueError("无效的 SkillHub 标识或版本")
    return quote(value, safe="")


def file_hashes(root, *, ignore_runtime=True):
    if os.path.islink(root) or getattr(os.path, "isjunction", lambda _: False)(root):
        raise ValueError("技能目录不得是链接")
    result = {}
    for directory, dirs, files in os.walk(root):
        if any(os.path.islink(os.path.join(directory, d)) or getattr(os.path, "isjunction", lambda _: False)(os.path.join(directory, d)) for d in dirs):
            raise ValueError("技能包含目录链接，无法验证本地修改")
        if ignore_runtime:
            dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root).replace("\\", "/")
            if relative == ORIGIN_FILE:
                continue
            if os.path.islink(path):
                raise ValueError("技能包含链接，无法验证本地修改")
            with open(path, "rb") as handle:
                result[relative] = hashlib.sha256(handle.read()).hexdigest()
    return result


def read_origin(root):
    path = os.path.join(root, ORIGIN_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("source") != "skillhub":
        raise ValueError("SkillHub 来源记录损坏")
    return value


class SkillHubInstallError(ValueError):
    def __init__(self, diagnostic):
        self.diagnostic = str(diagnostic)
        detail = self.diagnostic.removeprefix("Import failed: ")
        summary = detail
        for terms, label in [
            (("ZIP file is invalid", "Bad CRC", "BadZipFile", "Bad magic", "Truncated"), "压缩包损坏，请重新下载"),
            (("unsafe paths", "duplicate paths or links"), "压缩包包含不安全路径、重名文件或链接"),
            (("exceeds 2000",), "压缩包超过文件数或解压大小限制"),
            (("SKILL.md", "frontmatter", "Invalid skill name", "no importable"), "技能格式不支持，请查看诊断信息"),
            (("Permission denied", "Access is denied"), "无法写入能力目录，请检查目录权限"),
            (("No space left", "disk full"), "磁盘空间不足，请清理后重试"),
        ]:
            if any(term in detail for term in terms):
                summary = label
                break
        super().__init__(summary)


class SkillHubClient:
    def icon(self, url):
        from core.remote_skill_installer import validate_public_https_url
        for _ in range(4):
            validate_public_https_url(url)
            with requests.get(url, stream=True, allow_redirects=False, timeout=(5, 10)) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code != 200:
                    raise ValueError("图标下载失败")
                content = bytearray()
                for chunk in response.iter_content(16384):
                    content.extend(chunk)
                    if len(content) > 512 * 1024:
                        raise ValueError("图标超过 512 KB")
                return bytes(content)
        raise ValueError("图标重定向过多")

    def _get(self, path, params=None):
        log.info("skillhub request_start path=%s", path)
        for attempt in range(3):
            try:
                response = requests.get(
                    BASE_URL + path, params=params, timeout=(5, 20),
                    headers={"User-Agent": f"Cowork/{APP_VERSION}", "Accept": "application/json"},
                )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("SkillHub 返回格式错误")
                if "code" in body:
                    if body["code"] != 0 or not isinstance(body.get("data"), dict):
                        raise ValueError(str(body.get("message") or "SkillHub 请求失败"))
                    body = body["data"]
                log.info("skillhub request_done path=%s", path)
                return body
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if attempt == 2 or (status is not None and status != 429 and status < 500):
                    log.error("skillhub request_error path=%s status=%s", path, status)
                    raise RuntimeError(f"SkillHub 请求失败（{status or '网络超时或连接异常'}），请重试") from exc
                time.sleep(0.5 * 2 ** attempt)

    def search(self, keyword="", category="", sort="score", page=1):
        data = self._get("/api/skills", {
            "keyword": keyword, "category": category, "sortBy": sort,
            "order": "desc", "page": page, "pageSize": 20,
        })
        if not isinstance(data.get("skills"), list) or not isinstance(data.get("total"), int):
            raise ValueError("SkillHub 列表格式错误")
        for item in data["skills"]:
            identifier(item["slug"])
            if not isinstance(item.get("name"), str):
                raise ValueError("SkillHub 技能名称格式错误")
        return data

    def categories(self):
        items = self._get("/api/v1/categories")["items"]
        if not isinstance(items, list) or any(not isinstance(item.get("name"), str) or not isinstance(item.get("key"), str) for item in items):
            raise ValueError("SkillHub 分类格式错误")
        return items

    def detail(self, slug):
        data = self._get(f"/api/v1/skills/{identifier(slug)}")
        if not isinstance(data.get("skill"), dict) or not isinstance(data["skill"].get("displayName"), str):
            raise ValueError("SkillHub 详情格式错误")
        return data

    def versions(self, slug):
        data = self._get(f"/api/v1/skills/{identifier(slug)}/versions")
        if not isinstance(data.get("versions"), list):
            raise ValueError("SkillHub 版本列表格式错误")
        for item in data["versions"]:
            identifier(item["version"])
        return data

    def evaluation(self, slug):
        return self._get(f"/api/v1/skills/{identifier(slug)}/evaluation")

    def files(self, slug, version):
        return self._get(f"/api/v1/skills/{identifier(slug)}/files", {"version": version})

    def download(self, slug, version, destination):
        identifier(version)
        url = f"{BASE_URL}/api/v1/download?slug={identifier(slug)}&version={identifier(version)}"
        # Signed object-store URLs are transient and must never be logged or saved.
        from core.remote_skill_installer import validate_public_https_url
        for _ in range(6):
            validate_public_https_url(url)
            with requests.get(url, stream=True, allow_redirects=False, timeout=(5, 20),
                              headers={"User-Agent": f"Cowork/{APP_VERSION}"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f"SkillHub 下载失败（HTTP {response.status_code}）")
                size = 0
                with open(destination, "wb") as handle:
                    for chunk in response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("技能包超过 50 MB")
                        handle.write(chunk)
                return
        raise ValueError("SkillHub 下载重定向次数过多")

    def install(self, manager, slug, version, *, update_skill=None, on_commit=None):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="cowork-skillhub-") as tmp:
            archive = os.path.join(tmp, "skill.zip")
            try:
                self.download(slug, version, archive)
            except requests.RequestException as exc:
                raise RuntimeError("SkillHub 下载连接失败，请重试") from exc
            log.info("skillhub install_run slug=%s version=%s", slug, version)
            ok, message = manager.import_skill(
                archive, enabled=False if not update_skill else None,
                prepare_dependencies=False, update_skill=update_skill,
                origin={"source": "skillhub", "slug": slug, "version": version},
                on_commit=on_commit,
            )
            if not ok:
                raise SkillHubInstallError(message)
            return {"names": manager.last_imported_skill_names, "message": message}
