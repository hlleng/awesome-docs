#!/usr/bin/env python3
"""Minimal MCP-backed API for the Sphinx documentation assistant."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("docs-assistant")

try:
    from .local_config import LLM_API_KEY as LOCAL_LLM_API_KEY
    from .local_config import LLM_MODEL as LOCAL_LLM_MODEL
    from .local_config import LLM_URL as LOCAL_LLM_URL
except ImportError:
    # The local file is intentionally optional and ignored by Git.
    LOCAL_LLM_API_KEY = ""
    LOCAL_LLM_MODEL = ""
    LOCAL_LLM_URL = ""

try:
    from .local_config import MCP_COLLECTIONS as LOCAL_MCP_COLLECTIONS
    from .local_config import MCP_URL as LOCAL_MCP_URL
except ImportError:
    # MCP settings are also optional and may be supplied through the environment.
    LOCAL_MCP_COLLECTIONS = ""
    LOCAL_MCP_URL = ""


class UpstreamError(RuntimeError):
    """An MCP or LLM request failed."""


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    value = os.environ.get(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    mcp_url: str
    mcp_collections: tuple[str, ...]
    mcp_auth_header: str
    mcp_auth_value: str
    mcp_timeout_seconds: int
    llm_url: str
    llm_model: str
    llm_api_key: str
    llm_timeout_seconds: int
    allowed_origins: tuple[str, ...]
    top_k: int
    reranker_top_k: int
    score_threshold: float
    max_context_chars: int
    max_body_bytes: int

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_url and self.llm_model)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            host=os.environ.get("DOCS_ASSISTANT_HOST", "127.0.0.1"),
            port=_env_int("DOCS_ASSISTANT_PORT", 8300),
            mcp_url=os.environ.get("DOCS_ASSISTANT_MCP_URL", LOCAL_MCP_URL).strip(),
            mcp_collections=_csv_env(
                "DOCS_ASSISTANT_MCP_COLLECTIONS",
                LOCAL_MCP_COLLECTIONS or "md_knowledge_2560,pdf_ocr_knowledge_2560",
            ),
            mcp_auth_header=os.environ.get(
                "DOCS_ASSISTANT_MCP_AUTH_HEADER", "Authorization"
            ).strip(),
            mcp_auth_value=os.environ.get(
                "DOCS_ASSISTANT_MCP_AUTH_VALUE", ""
            ).strip(),
            mcp_timeout_seconds=_env_int("DOCS_ASSISTANT_MCP_TIMEOUT_SECONDS", 30),
            llm_url=os.environ.get("DOCS_ASSISTANT_LLM_URL", LOCAL_LLM_URL).strip(),
            llm_model=os.environ.get("DOCS_ASSISTANT_LLM_MODEL", LOCAL_LLM_MODEL).strip(),
            llm_api_key=os.environ.get(
                "DOCS_ASSISTANT_LLM_API_KEY", LOCAL_LLM_API_KEY
            ).strip(),
            llm_timeout_seconds=_env_int("DOCS_ASSISTANT_LLM_TIMEOUT_SECONDS", 60),
            allowed_origins=_csv_env(
                "DOCS_ASSISTANT_ALLOWED_ORIGINS",
                ",".join(
                    (
                        "http://127.0.0.1:8200",
                        "http://localhost:8200",
                        "http://0.0.0.0:8200",
                    )
                ),
            ),
            top_k=_env_int("DOCS_ASSISTANT_TOP_K", 12),
            reranker_top_k=_env_int("DOCS_ASSISTANT_RERANKER_TOP_K", 5),
            score_threshold=_env_float("DOCS_ASSISTANT_SCORE_THRESHOLD", 0.05),
            max_context_chars=_env_int("DOCS_ASSISTANT_MAX_CONTEXT_CHARS", 18_000),
            max_body_bytes=_env_int("DOCS_ASSISTANT_MAX_BODY_BYTES", 64 * 1024),
        )


def _decode_http_payload(body: str, content_type: str) -> Any:
    if "text/event-stream" in content_type:
        events = []
        for line in body.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
        if not events:
            raise UpstreamError("upstream returned an empty event stream")
        return events[-1]
    return json.loads(body)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: int,
) -> Any:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return _decode_http_payload(body, response.headers.get("Content-Type", ""))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise UpstreamError(f"upstream returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise UpstreamError(f"upstream request failed: {error}") from error


def _mcp_tool_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("MCP returned an invalid response")
    if payload.get("error"):
        raise UpstreamError(f"MCP error: {payload['error']}")

    result = payload.get("result", {})
    structured = result.get("structuredContent", {})
    if isinstance(structured.get("result"), dict):
        return structured["result"]

    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                parsed = json.loads(item.get("text", ""))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise UpstreamError("MCP response did not contain structured search results")


def _clean_excerpt(value: str, limit: int = 2_800) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value[:limit]


def _source_title(result: dict[str, Any]) -> str:
    document = str(result.get("document") or "文档来源")
    page = result.get("page") or result.get("page_start")
    return f"{document} · 第 {page} 页" if page else document


def extract_sources(
    results: list[dict[str, Any]], score_threshold: float
) -> list[dict[str, Any]]:
    sources = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        score = float(result.get("score") or 0)
        url = str(result.get("citation_url") or result.get("source_url") or "")
        document = str(result.get("document") or "")
        page = str(result.get("page") or result.get("page_start") or "")
        key = (document, page)
        if key in seen:
            continue
        if score < score_threshold and sources:
            continue
        seen.add(key)
        sources.append(
            {
                "title": _source_title(result),
                "document": document,
                "collection": result.get("collection"),
                "page": result.get("page") or result.get("page_start"),
                "score": score,
                "url": url,
                "excerpt": _clean_excerpt(str(result.get("content") or "")),
            }
        )
    return sources


def build_context(sources: list[dict[str, Any]], max_chars: int) -> str:
    blocks = []
    used = 0
    for index, source in enumerate(sources, start=1):
        block = (
            f"[{index}] {source['title']}\n"
            f"URL: {source['url'] or '未提供'}\n"
            f"内容:\n{source['excerpt']}"
        )
        remaining = max_chars - used
        if remaining <= 0:
            break
        blocks.append(block[:remaining])
        used += len(block)
    return "\n\n".join(blocks)


def retrieval_preview(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "当前知识库没有检索到足够相关的内容。"
    excerpts = []
    for index, source in enumerate(sources[:2], start=1):
        excerpt = source["excerpt"][:650].strip()
        excerpts.append(f"[{index}] {excerpt}")
    return "检索预览模式已找到以下相关内容：\n\n" + "\n\n".join(excerpts)


class AssistantService:
    def __init__(self, config: Config):
        self.config = config

    def search(self, query: str) -> list[dict[str, Any]]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": "AXERA-Docs-Assistant/0.1",
        }
        if self.config.mcp_auth_value:
            headers[self.config.mcp_auth_header] = self.config.mcp_auth_value
        response = _post_json(
            self.config.mcp_url,
            {
                "jsonrpc": "2.0",
                "id": uuid.uuid4().hex,
                "method": "tools/call",
                "params": {
                    "name": "search_documents",
                    "arguments": {
                        "query": query,
                        "top_k": self.config.top_k,
                        "reranker_top_k": self.config.reranker_top_k,
                        "collections": list(self.config.mcp_collections),
                    },
                },
            },
            headers=headers,
            timeout=self.config.mcp_timeout_seconds,
        )
        result = _mcp_tool_result(response)
        return extract_sources(result.get("results", []), self.config.score_threshold)

    def generate(
        self,
        question: str,
        sources: list[dict[str, Any]],
        history: list[dict[str, str]],
        page: dict[str, str],
    ) -> str:
        if not self.config.llm_configured:
            return retrieval_preview(sources)

        headers = {"Content-Type": "application/json"}
        if self.config.llm_api_key:
            headers["Authorization"] = f"Bearer {self.config.llm_api_key}"
        context = build_context(sources, self.config.max_context_chars)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "你是 AXERA 技术文档助手。只能依据提供的参考资料回答。"
                    "结论后使用 [1]、[2] 标注来源；资料不足时明确说明，不得猜测。"
                    "使用简洁纯文本，不输出 Markdown 表格。"
                ),
            }
        ]
        for item in history[-6:]:
            if item.get("role") in {"user", "assistant"} and item.get("content"):
                messages.append(
                    {"role": item["role"], "content": item["content"][:2_000]}
                )
        page_context = "\n".join(
            part
            for part in (
                f"当前页面: {page.get('title', '')}" if page.get("title") else "",
                f"页面地址: {page.get('url', '')}" if page.get("url") else "",
            )
            if part
        )
        messages.append(
            {
                "role": "user",
                "content": f"{page_context}\n\n问题：{question}\n\n参考资料：\n{context}",
            }
        )
        response = _post_json(
            self.config.llm_url,
            {
                "model": self.config.llm_model,
                "messages": messages,
                "temperature": 0.1,
                "stream": False,
            },
            headers=headers,
            timeout=self.config.llm_timeout_seconds,
        )
        try:
            return str(response["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as error:
            raise UpstreamError("LLM returned an invalid response") from error

    def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("message") or "").strip()
        if not question:
            raise ValueError("message is required")
        if len(question) > 4_000:
            raise ValueError("message is too long")
        history = payload.get("history") if isinstance(payload.get("history"), list) else []
        page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
        sources = self.search(question)
        answer = self.generate(question, sources, history, page)
        return {
            "answer": answer,
            "sources": sources,
            "mode": "llm" if self.config.llm_configured else "retrieval",
        }


class AssistantHandler(BaseHTTPRequestHandler):
    server_version = "AXERADocsAssistant/0.1"
    config: Config
    service: AssistantService

    def _origin(self) -> str:
        return self.headers.get("Origin", "")

    def _origin_allowed(self) -> bool:
        origin = self._origin()
        return not origin or "*" in self.config.allowed_origins or origin in self.config.allowed_origins

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Origin")
        if self._origin() and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", self._origin())
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "mcp_configured": bool(self.config.mcp_url),
                "llm_configured": self.config.llm_configured,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/docs-assistant":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._origin_allowed():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "origin is not allowed"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > self.config.max_body_bytes:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            response = self.service.ask(payload)
            self._send_json(HTTPStatus.OK, response)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except UpstreamError as error:
            LOGGER.warning("upstream request failed: %s", error)
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
        except Exception:
            LOGGER.exception("unexpected request failure")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)


def run(config: Config | None = None) -> None:
    config = config or Config.from_env()
    if not config.mcp_url:
        raise SystemExit("DOCS_ASSISTANT_MCP_URL is required")
    AssistantHandler.config = config
    AssistantHandler.service = AssistantService(config)
    server = ThreadingHTTPServer((config.host, config.port), AssistantHandler)
    mode = "LLM" if config.llm_configured else "retrieval preview"
    LOGGER.info("listening on http://%s:%s (%s)", config.host, config.port, mode)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
