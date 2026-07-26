#!/usr/bin/env python3
# mx_search - 妙想资讯搜索 skill
# 基于东方财富妙想搜索API提供金融资讯搜索能力
# Cowork 适配：默认输出到当前会话工作区的 mx_data/output/

import os
import sys
import json
import re
import argparse
import time
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runtime_support import configured_api_url, diagnostic, require_env, resolve_output_dir

def safe_filename(text: str, max_len: int = 80) -> str:
    """Convert query string to safe filenameh"""
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", text).strip().replace(" ", "_")
    return (cleaned[:max_len] or "query").strip("._")

class MXSearch:
    """妙想资讯搜索客户端"""
    
    ENDPOINT = "/api/claw/news-search"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        初始化客户端
        :param api_key: MX API Key，如果不提供则从环境变量 MX_APIKEY 读取
        """
        self.api_key = api_key or require_env("MX_APIKEY")
        self.base_url = configured_api_url()
    
    def search(self, query: str) -> Dict[str, Any]:
        """
        搜索金融资讯
        :param query: 搜索问句
        :return: API 响应结果
        """
        headers = {
            "Content-Type": "application/json",
            "apikey": self.api_key
        }
        data = {
            "query": query
        }
        
        started_at = time.monotonic()
        try:
            response = requests.post(self.base_url + self.ENDPOINT, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            result = response.json()
            diagnostic(
                "mx_search.search",
                self.ENDPOINT,
                "finish",
                started_at=started_at,
                http_status=response.status_code,
                error_code=result.get("code", "") if isinstance(result, dict) else "",
            )
            return result
        except Exception as exc:
            diagnostic(
                "mx_search.search",
                self.ENDPOINT,
                "error",
                started_at=started_at,
                http_status=getattr(getattr(exc, "response", None), "status_code", None),
                error_code=type(exc).__name__,
            )
            raise
    
    @staticmethod
    def extract_content(result: Dict[str, Any]) -> str:
        """
        提取纯文本内容
        :param result: API 响应结果
        :return: 提取后的纯文本
        """
        def _extract(raw: Any) -> str:
            if not isinstance(raw, dict):
                if isinstance(raw, str):
                    return raw.strip()
                return ""
            
            # Common envelope format
            for wrapper_key in ("data", "result"):
                wrapped = raw.get(wrapper_key)
                if isinstance(wrapped, dict):
                    nested = _extract(wrapped)
                    if nested:
                        return nested
            
            for key in ("llmSearchResponse", "searchResponse", "content", "answer", "summary"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, (list, dict)):
                    return json.dumps(value, ensure_ascii=False, indent=2)
            
            return json.dumps(raw, ensure_ascii=False, indent=2)
        
        return _extract(result)
    
    @staticmethod
    def format_pretty(result: Dict[str, Any]) -> str:
        """
        格式化结果用于终端显示
        :param result: API 响应结果
        :return: 格式化文本
        """
        output = []
        
        status = result.get("status")
        message = result.get("message", "")
        if status != 0:
            output.append(f"错误: 状态码 {status} - {message}")
            return "\n".join(output)
        
        data = result.get("data", {})
        inner_data = data.get("data", {})
        search_response = inner_data.get("llmSearchResponse", {})
        items = search_response.get("data", [])
        
        if not items:
            return "未找到相关资讯"
        
        output.append(f"搜索结果: 共找到 {len(items)} 条相关资讯:\n")
        
        for i, item in enumerate(items, 1):
            title = item.get("title", "无标题")
            content = item.get("content", "无内容")
            date = item.get("date", "")
            ins_name = item.get("insName", "")
            info_type = item.get("informationType", "")
            rating = item.get("rating", "")
            entity_name = item.get("entityFullName", "")
            
            type_map = {
                "REPORT": "研报",
                "NEWS": "新闻",
                "ANNOUNCEMENT": "公告"
            }
            type_cn = type_map.get(info_type, info_type)
            
            output.append(f"--- {i}. {title} ---")
            meta = []
            if entity_name:
                meta.append(f"证券: {entity_name}")
            if ins_name:
                meta.append(f"机构: {ins_name}")
            if date:
                meta.append(f"日期: {date.split()[0]}")
            if type_cn:
                meta.append(f"类型: {type_cn}")
            if rating:
                meta.append(f"评级: {rating}")
            
            if meta:
                output.append(" | ".join(meta))
            
            if content:
                output.append("")
                output.append(content)
            output.append("")
        
        return "\n".join(output)

def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="东方财富妙想资讯搜索")
    parser.add_argument("query", help="自然语言搜索问句")
    parser.add_argument("--output-dir", dest="output_dir", help="输出目录；默认使用当前 Cowork 工作区")
    args = parser.parse_args()
    query = args.query
    output_dir = resolve_output_dir(args.output_dir)
    
    try:
        mx = MXSearch()
        result = mx.search(query)
        
        # 终端显示格式化结果
        print(mx.format_pretty(result))
        
        # 提取纯文本保存为 .txt 文件
        content = mx.extract_content(result)
        if content.strip():
            filename = output_dir / f"mx_search_{safe_filename(query)}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"\n✅ 纯文本结果已保存到: {filename}")
        
        # 同时保存原始 JSON 结果
        json_filename = output_dir / f"mx_search_{safe_filename(query)}.json"
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"📄 原始结果已保存到: {json_filename}")
            
    except Exception as e:
        print(f"错误: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
