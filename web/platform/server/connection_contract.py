"""Pure public package contract, shared without desktop/server dependencies."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

CONNECTION_FILE = "enterprise_connection.json"
SUPPORTED_CONNECTION_SCHEMAS = (1,)
SUPPORTED_PLATFORM_API_VERSIONS = (1,)
MAX_CONNECTION_BYTES = 64 * 1024
FIELDS = {"schema_version", "config_version", "platform_id", "platform_name", "platform_url", "distribution_channel"}


class EnterpriseConfigurationError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnterpriseConfigurationError("企业连接文件包含重复字段：" + key)
        result[key] = value
    return result


def validate_connection(value, *, development=False):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise EnterpriseConfigurationError("企业连接文件须仅包含约定的六个字段。")
    value = dict(value)
    if type(value["schema_version"]) is not int or value["schema_version"] not in SUPPORTED_CONNECTION_SCHEMAS:
        raise EnterpriseConfigurationError("不支持此企业连接文件版本，请使用兼容客户端。")
    if type(value["config_version"]) is not int or value["config_version"] < 1:
        raise EnterpriseConfigurationError("企业配置版本必须是正整数。")
    if not isinstance(value["platform_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value["platform_id"]):
        raise EnterpriseConfigurationError("企业平台标识无效。")
    name = value["platform_name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise EnterpriseConfigurationError("企业名称必须为 1–80 个非控制字符。")
    value["platform_name"] = name.strip()
    url = value["platform_url"]
    if not isinstance(url, str):
        raise EnterpriseConfigurationError("企业平台地址必须是 HTTPS 地址。")
    try:
        p = urlsplit(url)
        _ = p.port
    except ValueError:
        raise EnterpriseConfigurationError("企业平台地址无效。") from None
    local_http = development and p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not p.hostname or (p.scheme != "https" and not local_http) or p.username or p.password
            or p.query or p.fragment or "\\" in url or any(c.isspace() for c in url)
            or any(part in {".", ".."} for part in p.path.split("/"))):
        raise EnterpriseConfigurationError("企业平台地址须使用 HTTPS，且不含凭据、查询参数或片段。")
    value["platform_url"] = url.rstrip("/")
    if value["distribution_channel"] != "enterprise":
        raise EnterpriseConfigurationError("企业分发渠道无效。")
    return value


def parse_connection(data, *, development=False):
    if len(data) > MAX_CONNECTION_BYTES:
        raise EnterpriseConfigurationError("企业连接文件超过 64 KiB。")
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EnterpriseConfigurationError("企业连接文件不是有效的 UTF-8 JSON。") from None
    return validate_connection(payload, development=development)



def check_update_connection(current, candidate):
    """An update may refresh an embedding but never switch the bound enterprise."""
    if not current:
        return
    if not candidate:
        raise EnterpriseConfigurationError("企业客户端不能安装普通渠道包，请下载同一企业的分发包。")
    if any(current[k] != candidate[k] for k in ("platform_id", "platform_url")):
        raise EnterpriseConfigurationError("更新包属于其他企业或地址，请从连接设置明确切换平台。")
    if candidate["config_version"] < current["config_version"]:
        raise EnterpriseConfigurationError("更新包包含较旧的企业配置，请选择当前企业版本。")
    if candidate["config_version"] == current["config_version"] and candidate != current:
        raise EnterpriseConfigurationError("同版本企业配置内容冲突，请联系管理员重新发布。")
