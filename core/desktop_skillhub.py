"""SkillHub browsing and install jobs over the existing verified installer."""
from pathlib import Path
import threading

from core.skillhub import SkillHubClient, SkillHubCache, read_origin, preferred_version
from core.desktop_jobs import JobCancelled


class DesktopSkillHub:
    methods = frozenset({"skillhub.search", "skillhub.detail", "skillhub.install", "skillhub.refresh"})

    def __init__(self, service):
        self.s = service
        self.client = SkillHubClient()
        self.cache = SkillHubCache(str(Path(service.config.data_dir) / "cache" / "skillhub"))
        self.install_lock = threading.Lock()

    def installed(self):
        result = {}
        for skill in self.s.state.skill_catalog.snapshot().display_skills():
            if not skill.get("path"):
                continue
            origin = read_origin(skill["path"])
            if origin:
                result[origin["slug"]] = {"name": skill["name"], "version": origin.get("version"), "enabled": skill.get("enabled")}
        return result

    def dispatch(self, method, p):
        if method == "skillhub.refresh":
            self.cache.clear()
            return {"cleared": True}
        if method == "skillhub.install":
            slug, version = str(p["slug"]), str(p["version"])
            def install(cancelled, progress):
                if not self.install_lock.acquire(blocking=False):
                    raise ValueError("另一个技能正在安装，请完成后再试。")
                try:
                    if self.s.store.active_runs():
                        raise ValueError("请等待运行中的任务结束，再安装或更新技能。")
                    current = self.installed().get(slug)
                    if not p.get("explicit_version") and preferred_version(self.client.detail(slug)) != version:
                        raise ValueError("发布版本已变化，请刷新并核对版本后安装。尚未修改本地技能。")
                    if cancelled.is_set():
                        raise JobCancelled("已取消安装。")
                    progress("正在下载并校验技能包…")
                    manager = self.s.state.skill_catalog.snapshot().manager
                    result = self.client.install(manager, slug, version, update_skill=current["name"] if current else None)
                    self.s.management.skill_change("updated" if current else "created", result["names"])
                    return {**result, "installed": self.installed(), "message": "更新完成。" if current else "已安装，可在“我的能力”中开启。"}
                finally:
                    self.install_lock.release()
            return self.s.jobs.submit("安装技能 " + slug, install)
        def browse(cancelled, progress):
            slug = p.get("slug") if method == "skillhub.detail" else None
            key = ["detail", slug] if slug else ["search", str(p.get("keyword") or ""), str(p.get("category") or ""), str(p.get("sort") or "score"), max(1, int(p.get("page") or 1))]
            data = self.cache.get(key)
            cached = data is not None
            if not cached:
                progress("正在读取 SkillHub…")
                if slug:
                    data = self.client.detail(slug)
                    data["versions"] = self.client.versions(slug)["versions"]
                    try:
                        data["evaluation"] = self.client.evaluation(slug)
                    except Exception as exc:
                        data["evaluation_error"] = str(exc)
                else:
                    data = {"list": self.client.search(*key[1:]), "categories": self.client.categories()}
                if cancelled.is_set():
                    raise JobCancelled("已取消读取。")
                self.cache.put(key, data)
            return {**data, "cached": cached, "installed": self.installed()}
        return self.s.jobs.submit("读取 SkillHub", browse)
