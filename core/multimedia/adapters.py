"""Provider contracts and explicit submission outcomes."""
from __future__ import annotations

import base64
from contextlib import ExitStack
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import quote, urlsplit, urlunsplit

import requests

MAX_INPUT = 25 * 1024 * 1024
MAX_OUTPUT = 512 * 1024 * 1024


class MediaError(RuntimeError):
    def __init__(self, message, *, uncertain=False, retryable=False, http_status=None, code="", request_id=""):
        super().__init__(message)
        self.uncertain = uncertain
        self.retryable = retryable
        self.http_status = http_status
        self.code = code
        self.request_id = request_id


def http_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("请使用有效的 HTTP 或 HTTPS 素材链接。")
    return str(value)


def api_root(profile):
    base = http_url(profile["base_url"]).rstrip("/")
    parsed = urlsplit(base)
    if parsed.query or parsed.fragment:
        raise ValueError("服务地址不能包含查询参数或片段。")
    if profile["adapter"] == "ark":
        return base if parsed.path.rstrip("/").endswith("/api/v3") else base + "/api/v3"
    return base if parsed.path.rstrip("/").endswith("/v1") else base + "/v1"


def local_input(value, workspace):
    root = Path(workspace).resolve()
    path = (root / str(value)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("素材必须是当前对话工作目录中的文件，或受支持的素材链接。")
    if path.stat().st_size > MAX_INPUT:
        raise ValueError("本地素材超过 25 MiB，请先裁剪或压缩。")
    return path


def image_input(value, workspace):
    if str(value).startswith(("https://", "http://")):
        return http_url(value)
    path = local_input(value, workspace)
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ValueError("图片素材请使用 PNG、JPEG、WebP 或 GIF。")
    return "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def safe_message(value, secret=""):
    text = str(value or "服务没有提供错误原因。")
    if secret:
        text = text.replace(secret, "[已隐藏凭据]")
    text = re.sub(r"https?://\S+", "[服务链接]", text)
    return text[:700]


class Adapter:
    def __init__(self, profile):
        self.profile = dict(profile)
        self.root = api_root(profile)
        from . import model_usage
        self.known = model_usage({**profile, "model_name": profile.get("model")}, {"binding": profile})
        if self.known and profile["adapter"] != "agnes":
            raise ValueError("此 Agnes 模型需要 Agnes 接入预设，请在多媒体页修改后保存。")
        if not profile.get("api_key"):
            raise ValueError("此服务尚未填写 API Key，请到模型与服务中补充。")
        self.headers = {"Authorization": "Bearer " + profile["api_key"]}

    def request(self, method, path, *, absolute=False, timeout=(15, 180), **kwargs):
        url = path if absolute else self.root + path
        try:
            response = requests.request(method, url, headers=self.headers, timeout=timeout,
                                        allow_redirects=method == "GET", **kwargs)
        except requests.RequestException:
            raise MediaError("服务连接中断或超时。", uncertain=method == "POST", retryable=method != "POST") from None
        if not 200 <= response.status_code < 300:
            status = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            payload = payload if isinstance(payload, dict) else {}
            error = payload.get("error")
            fields = error if isinstance(error, dict) else payload
            code = str(fields.get("code") or "")
            detail = fields.get("message") or (error if isinstance(error, str) else code)
            rejected = (method == "POST" and path == "/videos" and self.profile["adapter"] == "agnes"
                        and status == 503 and code == "video_queue_full"
                        and not any(payload.get(key) or fields.get(key) for key in ("id", "task_id", "video_id")))
            request_id = str(response.headers.get("X-Request-ID") or fields.get("request_id") or payload.get("request_id") or "")
            secret = self.profile["api_key"]
            response.close()
            raise MediaError(f"服务返回 HTTP {status}：" + safe_message(detail, secret),
                             uncertain=method == "POST" and (status == 408 or status >= 500) and not rejected,
                             retryable=rejected or (method != "POST" and (status in {408, 429} or status >= 500)),
                             http_status=status, code=code if re.fullmatch(r"[\w.-]{1,80}", code) and secret not in code else "",
                             request_id=request_id if re.fullmatch(r"[\w.-]{1,120}", request_id) and secret not in request_id else "")
        return response

    def json_request(self, method, path, **kwargs):
        with self.request(method, path, **kwargs) as response:
            try:
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError()
                return result
            except ValueError:
                raise MediaError("服务返回了无法解析的结果。", uncertain=method == "POST") from None

    def prepare(self, kind, prompt, inputs, options, workspace):
        """Validate and resolve inputs before the durable submitting boundary."""
        if self.known and kind not in self.known["capabilities"]:
            raise ValueError("此模型只支持" + self.known["purpose_label"] + "，请修改多媒体配置。")
        if not isinstance(options, dict) or not isinstance(inputs, list) or len(inputs) > 8:
            raise ValueError("素材与生成参数格式无效，单次最多 8 个素材。")
        if not all(isinstance(value, str) and value for value in inputs):
            raise ValueError("素材应使用文件路径或链接。")
        allowed = {
            "image_generate": {"size"}, "video_generate": {"seconds", "size", "aspect_ratio"},
            "audio_generate": {"voice", "speed"}, "audio_understand": {"language"},
            "image_understand": set(), "video_understand": set(),
        }
        if kind not in allowed or set(options) - allowed[kind]:
            raise ValueError("包含此能力不支持的参数。")
        if not prompt.strip() and kind != "audio_understand":
            raise ValueError("请提供生成要求或需要回答的问题。")
        if len(prompt) > 32000:
            raise ValueError("要求过长，请缩短到 32000 个字符以内。")
        if kind.endswith("understand") and not inputs:
            raise ValueError("请提供需要解析的素材。")
        if kind in {"audio_understand", "video_understand"} and len(inputs) != 1:
            raise ValueError("每次请解析一个音频或视频。")
        if kind == "audio_generate" and inputs:
            raise ValueError("语音生成功能将输入文字转为语音，不接受参考素材。")
        profile, body = self.profile, {"model": self.profile["model"]}
        adapter = profile["adapter"]
        if kind == "image_generate":
            size = str(options.get("size") or "1024x1024")
            if not re.fullmatch(r"\d{2,5}x\d{2,5}|auto", size):
                raise ValueError("图片尺寸使用 WIDTHxHEIGHT 或 auto。")
            body.update(prompt=prompt, size=size)
            if adapter == "agnes":
                body["extra_body"] = {"response_format": "url"}
                if inputs:
                    body["extra_body"]["image"] = [image_input(value, workspace) for value in inputs]
            elif inputs:
                return {"kind": kind, "body": body, "files": [str(local_input(value, workspace)) for value in inputs]}
        elif kind == "video_generate":
            duration = options.get("seconds", 5)
            if type(duration) is not int and not (isinstance(duration, str) and duration.strip().isdecimal()):
                raise ValueError("视频时长须为整数秒。")
            seconds = int(duration)
            if not 1 <= seconds <= 30:
                raise ValueError("视频时长须为 1–30 秒，实际范围以模型为准。")
            ratio = str(options.get("aspect_ratio") or "16:9")
            if ratio not in {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
                raise ValueError("不支持此视频画幅。")
            if adapter == "agnes":
                if not 4 <= seconds <= 12:
                    raise ValueError("Agnes 视频时长须为 4–12 秒。")
                size = "720P" if options.get("size") is None else str(options["size"]).strip().upper()
                if profile["model"] == "agnes-video-2.5-flash":
                    if size != "720P":
                        raise ValueError("Agnes Video 2.5 Flash 仅支持 720P 分辨率。")
                    if len(inputs) > 5:
                        raise ValueError("Agnes Video 2.5 Flash 最多支持 5 张参考图。")
                if size not in {"720P", "1080P", "1K", "2K"}:
                    raise ValueError("Agnes 分辨率使用 720P、1080P、1K 或 2K。")
                body.update(prompt=prompt, seconds=str(seconds), size=size, aspect_ratio=ratio, mode="reference" if inputs else "text")
                if inputs:
                    if any(not value.startswith(("http://", "https://")) for value in inputs):
                        raise ValueError("Agnes 视频参考图需要 HTTP 或 HTTPS 公网链接，当前未提供本地上传适配。")
                    body["images"] = [http_url(value) for value in inputs]
            elif adapter == "ark":
                body.update(content=[{"type": "text", "text": prompt}], duration=seconds, ratio=ratio)
                if options.get("size"):
                    body["resolution"] = str(options["size"]).lower()
                body["content"].extend({"type": "image_url", "image_url": {"url": image_input(value, workspace)}} for value in inputs)
            else:
                if inputs or options.get("aspect_ratio"):
                    raise ValueError("通用 Videos 预设首期只支持文字生成；参考图片请使用 Agnes 或火山方舟预设。")
                body.update(prompt=prompt, seconds=str(seconds), size=options.get("size") or "1280x720")
        elif kind == "audio_generate":
            speed = float(options.get("speed", 1))
            if not .25 <= speed <= 4:
                raise ValueError("语速须为 0.25–4。")
            body.update(input=prompt, voice=str(options.get("voice") or "alloy"), response_format="mp3", speed=speed)
        elif kind == "audio_understand":
            if options.get("language"):
                body["language"] = str(options["language"])
            body["response_format"] = "json"
            return {"kind": kind, "body": body, "files": [str(local_input(inputs[0], workspace))]}
        else:
            content = [{"type": "text", "text": prompt}]
            for value in inputs:
                content.append({"type": "image_url", "image_url": {"url": image_input(value, workspace)}} if kind == "image_understand"
                               else {"type": "video_url", "video_url": {"url": http_url(value)}})
            body.update(messages=[{"role": "user", "content": content}], stream=False)
        return {"kind": kind, "body": body}

    def submit(self, prepared, destination):
        kind, body = prepared["kind"], prepared["body"]
        if kind == "audio_generate":
            with self.request("POST", "/audio/speech", json=body, stream=True) as response:
                target = destination / "speech.mp3"
                try:
                    if response.headers.get("Content-Type", "").lower().startswith(("text/", "application/json")):
                        raise MediaError("语音接口返回了网页或错误信息。")
                    self.write_stream(response, target)
                except MediaError as exc:
                    exc.uncertain = True
                    raise
            self.write_bytes(destination / "receipt.json", json.dumps({"files": [str(target)]}).encode())
            return {"files": [str(target)]}
        if kind == "audio_understand":
            with open(prepared["files"][0], "rb") as file:
                result = self.json_request("POST", "/audio/transcriptions", data=body, files={"file": (Path(file.name).name, file)})
            text = result.get("text")
            if not isinstance(text, str):
                raise MediaError("转写结果缺少文字。", uncertain=True)
            return {"text": text}
        if kind.endswith("understand"):
            result = self.json_request("POST", "/chat/completions", json=body)
            choices = result.get("choices") or []
            text = (choices[0].get("message") or {}).get("content") if choices else None
            if not isinstance(text, str) or not text:
                raise MediaError("理解接口没有返回文字内容。", uncertain=True)
            return {"text": text}
        if kind == "image_generate":
            if prepared.get("files"):
                with ExitStack() as stack:
                    files = [("image[]", (Path(path).name, stack.enter_context(open(path, "rb")), mimetypes.guess_type(path)[0] or "application/octet-stream")) for path in prepared["files"]]
                    result = self.json_request("POST", "/images/edits", data=body, files=files)
            else:
                result = self.json_request("POST", "/images/generations", json=body)
            data = result.get("data")
            if not isinstance(data, list) or not data or len(data) > 10:
                raise MediaError("图片接口没有返回有效的成果。", uncertain=True)
            sources = []
            for i, item in enumerate(data):
                if item.get("b64_json"):
                    # Persist response bytes immediately, independently of the task's final state.
                    target = destination / f"image-{i + 1}.png"
                    try:
                        raw = base64.b64decode(item["b64_json"], validate=True)
                    except (ValueError, TypeError):
                        raise MediaError("图片 Base64 数据无效。", uncertain=True) from None
                    if not raw or len(raw) > MAX_OUTPUT:
                        raise MediaError("图片成果大小无效。", uncertain=True)
                    self.write_bytes(target, raw)
                    target = self.check_image(target)
                    sources.append({"path": str(target)})
                    self.write_bytes(destination / "receipt.json", json.dumps({"sources": sources}).encode())
                elif item.get("url"):
                    sources.append({"url": http_url(item["url"]), "name": f"image-{i + 1}.png"})
                else:
                    raise MediaError("图片结果缺少下载地址或数据。", uncertain=True)
            return {"sources": sources}
        path = "/contents/generations/tasks" if self.profile["adapter"] == "ark" else "/videos"
        result = (self.json_request("POST", path, files={key: (None, str(value)) for key, value in body.items()})
                  if self.profile["adapter"] == "openai" else self.json_request("POST", path, json=body))
        key = "video_id" if self.profile["adapter"] == "agnes" else "id"
        remote_id = result.get(key)
        if not isinstance(remote_id, str) or not remote_id:
            raise MediaError(f"创建响应缺少 {key}。", uncertain=True)
        return {"remote_id": remote_id, "provider_task_id": str(result.get("id") or remote_id)}

    def poll(self, remote_id, *, timeout=(15, 180)):
        identity = quote(remote_id, safe="")
        adapter = self.profile["adapter"]
        if adapter == "agnes":
            parsed = urlsplit(self.root)
            url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.removesuffix("/v1") + "/agnesapi", "", ""))
            result = self.json_request("GET", url, absolute=True, timeout=timeout,
                                       params={"video_id": remote_id, "model_name": self.profile["model"]})
        else:
            result = self.json_request("GET", ("/contents/generations/tasks/" if adapter == "ark" else "/videos/") + identity,
                                       timeout=timeout)
        status = result.get("status")
        if status in {"failed", "cancelled", "canceled", "expired"}:
            error = result.get("error") or {}
            reason = error.get("message") if isinstance(error, dict) else str(error)
            return {"status": "failed", "error": safe_message(reason or status, self.profile["api_key"])}
        if status in {"completed", "succeeded"}:
            url = (result.get("content") or {}).get("video_url") if adapter == "ark" else result.get("url")
            if url:
                return {"status": "generated", "sources": [{"url": http_url(url), "name": "video.mp4"}]}
            if adapter == "openai":
                return {"status": "generated", "sources": [{"api_path": "/videos/" + identity + "/content", "name": "video.mp4"}]}
            raise MediaError("视频已生成，但服务没有返回下载地址。", retryable=True)
        if status not in {"queued", "pending", "running", "in_progress", "processing"}:
            raise MediaError("服务返回了未知的视频状态，请核对接入预设。")
        queued = status in {"queued", "pending"}
        progress = result.get("progress")
        return {"status": "running", "phase": "queued" if queued else "generating",
                "progress": progress if not queued and type(progress) in {float, int} and 0 <= progress <= 100 else None}

    @staticmethod
    def check_image(path):
        from PIL import Image
        try:
            with Image.open(path) as image:
                extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}.get(image.format)
                image.verify()
            if not extension:
                raise ValueError()
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            raise MediaError("服务返回的图片文件无效，已保留原始文件以便核查。") from None
        correct = path.with_suffix(extension)
        if correct != path:
            path.replace(correct)
        return correct

    @staticmethod
    def write_bytes(target, raw):
        temporary = target.with_suffix(target.suffix + ".part")
        with open(temporary, "wb") as file:
            file.write(raw)
            file.flush()
            import os
            os.fsync(file.fileno())
        temporary.replace(target)

    @staticmethod
    def write_stream(response, target):
        temporary, total = target.with_suffix(target.suffix + ".part"), 0
        try:
            with open(temporary, "wb") as file:
                for chunk in response.iter_content(1024 * 256):
                    total += len(chunk)
                    if total > MAX_OUTPUT:
                        raise MediaError("成果超过 512 MiB，请到服务后台下载。")
                    file.write(chunk)
                if not total:
                    raise MediaError("服务返回了空文件。")
                file.flush()
                import os
                os.fsync(file.fileno())
            temporary.replace(target)
        except requests.RequestException:
            raise MediaError("成果下载中断。", retryable=True) from None

    def download(self, source, destination):
        if source.get("path"):
            path = Path(source["path"]).resolve()
            if not path.is_relative_to(destination.resolve()) or not path.is_file():
                raise MediaError("已保存的成果文件不存在。")
            return str(path)
        name = str(source["name"])
        if Path(name).name != name or Path(name).suffix not in {".png", ".mp4"}:
            raise MediaError("成果文件名无效。")
        target = destination / name
        if not target.resolve().is_relative_to(destination.resolve()):
            raise MediaError("成果路径超出任务目录。")
        if source.get("api_path"):
            response = self.request("GET", source["api_path"], stream=True)
        else:
            # Signed CDN downloads never inherit the API credential.
            try:
                response = requests.get(http_url(source["url"]), timeout=(15, 90), stream=True)
                if not 200 <= response.status_code < 300:
                    status = response.status_code
                    response.close()
                    raise MediaError(f"成果下载返回 HTTP {status}。", retryable=status in {408, 429} or status >= 500,
                                     http_status=status)
            except requests.RequestException:
                raise MediaError("成果下载连接失败。", retryable=True) from None
        with response:
            if response.headers.get("Content-Type", "").lower().startswith(("text/", "application/json")):
                raise MediaError("下载地址返回了网页或错误信息，未将其作为媒体成果交付。")
            self.write_stream(response, target)
        if target.suffix == ".png":
            target = self.check_image(target)
        return str(target)
