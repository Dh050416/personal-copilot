"""search tool adapters; business persistence lives in services."""

import re
import xml.etree.ElementTree as ET
from html import unescape

import httpx
from langchain_core.tools import tool

from app.errors import ToolError
from app.schemas import (
    WebSearchArgs,
    WebSearchItem,
    WebSearchResult,
)


def build_search_tools(db, embedding_client=None):
    @tool("web_search", args_schema=WebSearchArgs, response_format="content_and_artifact")
    async def web_search(**kwargs):
        """搜索互联网获取实时信息（新闻、攻略、天气、价格、评测、路线等本地账单里没有的外部资料）。

        适合旅游规划、最新消息、产品对比、路线攻略等需要外部信息的查询；
        账单/收支类查询不要用本工具，用 query_bill。结果来自公开网页，注意时效与来源可靠性。
        """
        args = WebSearchArgs.model_validate(kwargs)
        try:
            async with httpx.AsyncClient(
                timeout=8,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                    )
                },
            ) as client:
                resp = await client.get(
                    "https://cn.bing.com/search",
                    params={"q": args.query, "format": "rss", "count": args.max_results},
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(
                "SEARCH_UNAVAILABLE",
                f"搜索服务暂时不可用（{exc.__class__.__name__}），请稍后重试。",
                stage="execution",
                retryable=True,
            )
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            raise ToolError(
                "SEARCH_UNAVAILABLE",
                "搜索服务返回内容无法解析，请稍后重试。",
                stage="execution",
                retryable=True,
            )
        items = []
        for it in root.findall(".//item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "")
            desc = unescape(desc).strip()
            if title:
                items.append(WebSearchItem(title=title, url=link, snippet=desc))
            if len(items) >= args.max_results:
                break
        if not items:
            raise ToolError(
                "SEARCH_EMPTY",
                f"没有找到与「{args.query}」相关的搜索结果，可换关键词重试。",
                stage="execution",
                retryable=True,
            )
        result = WebSearchResult(query=args.query, count=len(items), provider="bing", items=items)
        summary = f"搜索「{args.query}」共 {len(items)} 条结果（来源：必应）："
        for it in items:
            summary += f"\n- {it.title}｜{it.url}"
            if it.snippet:
                summary += f"｜{it.snippet[:120]}"
        return summary, result

    return [web_search]
