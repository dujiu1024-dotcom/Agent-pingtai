"""本地 Ollama 生成器：按需启动服务、短时保活、失败时明确报错。

RAG 只负责“有依据的回答”；检索仍由 KnowledgeService 完成，本模块仅把
检索到的资料交给本地模型生成答案与引用编号，不做任何静默降级。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_EMBED_MODEL = "bge-m3:latest"
DEFAULT_EMBED_DIMENSIONS = 1024
SYSTEM_PROMPT = (
    "你是严谨的知识库问答助手。只能依据用户消息中的【资料】回答，不得编造。"
    "若资料不足以回答，answer 返回空字符串，citation_ids 返回空数组。"
    "必须只输出一个 JSON 对象，格式为："
    '{"answer": "回答正文", "citation_ids": ["引用编号"]}。'
    "citation_ids 只能从资料条目开头的 citation_id 中选择，至少选择一个。"
)


class OllamaUnavailable(RuntimeError):
    """本地 Ollama 未安装、未启动或模型缺失。"""


class _OllamaClient:
    """共享本地 Ollama 服务探测、按需启动与模型校验。"""

    def __init__(
        self, *, base_url: str, model: str,
        log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("Ollama base_url is required")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Ollama model is required")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.log_path = Path(log_path) if log_path else (
            Path.home() / ".fde-local-data" / "ollama.log"
        )
        self._lock = asyncio.Lock()
        self._ready = False
        self._ready_model: str | None = None

    async def _ensure_ready(self) -> None:
        if self._ready and self._ready_model == self.model:
            return
        async with self._lock:
            if self._ready and self._ready_model == self.model:
                return
            if not await self._probe():
                await self._launch_server()
            await self._verify_model()
            self._ready = True
            self._ready_model = self.model

    async def _probe(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _verify_model(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"无法读取本地模型列表：{exc}") from exc
        models = data.get("models") if isinstance(data, dict) else None
        names = {
            str(item.get("name")) for item in (models or [])
            if isinstance(item, Mapping) and item.get("name")
        }
        if self.model in names:
            return
        stem = self.model.split(":", 1)[0]
        if any(name.split(":", 1)[0] == stem for name in names):
            return
        raise OllamaUnavailable(
            f"本地 Ollama 中找不到模型 {self.model}，请先执行 ollama pull {self.model}"
        )

    async def _launch_server(self) -> None:
        binary = (
            shutil.which("ollama")
            or ("/usr/local/bin/ollama" if Path("/usr/local/bin/ollama").is_file() else None)
            or ("/opt/homebrew/bin/ollama" if Path("/opt/homebrew/bin/ollama").is_file() else None)
        )
        if not binary:
            raise OllamaUnavailable("找不到 ollama 可执行文件，无法启动本地模型服务")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self.log_path, "ab", buffering=0)
        try:
            subprocess.Popen(
                [binary, "serve"],
                stdout=log_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        finally:
            log_file.close()
        for _ in range(60):
            await asyncio.sleep(0.5)
            if await self._probe():
                return
        raise OllamaUnavailable(
            f"Ollama 服务启动超时，请查看日志 {self.log_path}"
        )


class OllamaChatGenerator(_OllamaClient):
    """实现 knowledge.rag.RAGGenerator 协议。"""

    def __init__(
        self, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
        timeout_seconds: float = 180.0, keep_alive: str = "30s",
        max_context_chars: int = 12000, log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        super().__init__(base_url=base_url, model=model, log_path=log_path)
        self.timeout_seconds = float(timeout_seconds)
        self.keep_alive = keep_alive
        self.max_context_chars = max_context_chars

    async def generate(
        self, *, query: str, context: Sequence[Mapping[str, object]],
    ) -> Mapping[str, Any]:
        await self._ensure_ready()
        prompt = self._build_user_prompt(query, context)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                if response.status_code == 404:
                    raise OllamaUnavailable(
                        f"本地 Ollama 中找不到模型 {self.model}，请先执行 ollama pull {self.model}"
                    )
                response.raise_for_status()
                data = response.json()
        except OllamaUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"本地模型调用失败：{exc}") from exc
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaUnavailable("本地模型返回了空响应")
        answer, citation_ids = self._parse_response(content)
        allowed = {
            str(item.get("citation_id")) for item in context
            if isinstance(item, Mapping) and item.get("citation_id") is not None
        }
        return {
            "answer": answer,
            "citation_ids": [value for value in citation_ids if value in allowed],
        }

    def _build_user_prompt(
        self, query: str, context: Sequence[Mapping[str, object]],
    ) -> str:
        lines = ["【资料】"]
        used = 0
        for item in context:
            citation_id = str(item.get("citation_id") or "")
            title = str(item.get("title") or "")
            text = str(item.get("text") or "")
            remaining = self.max_context_chars - used
            if remaining <= 0:
                break
            snippet = text[: min(len(text), remaining, 2000)]
            used += len(snippet)
            lines.append(f"[{citation_id}] {title}\n{snippet}\n")
        lines.append(f"【问题】{query}")
        lines.append("请按约定 JSON 格式回答，citation_ids 只能使用上面的 citation_id。")
        return "\n".join(lines)

    @staticmethod
    def _parse_response(content: str) -> tuple[str, list[str]]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OllamaUnavailable("本地模型未返回可解析的 JSON 结果") from exc
        if not isinstance(payload, Mapping):
            raise OllamaUnavailable("本地模型返回的 JSON 不是对象")
        answer = payload.get("answer")
        citation_ids = payload.get("citation_ids")
        if not isinstance(answer, str):
            raise OllamaUnavailable("本地模型返回缺少 answer 字段")
        if not isinstance(citation_ids, list):
            raise OllamaUnavailable("本地模型返回缺少 citation_ids 字段")
        normalized = [value.strip() for value in citation_ids if isinstance(value, str)]
        return answer.strip(), [value for value in normalized if value]


class OllamaEmbedder(_OllamaClient):
    """实现 knowledge.service.Embedder 协议：调用本地 bge-m3 生成向量。

    storage_key 只取模型族名（去掉 :tag），避免 tag 漂移造成同一模型
    的向量被写入不同命名空间而检索不到。
    """

    def __init__(
        self, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_EMBED_MODEL,
        timeout_seconds: float = 120.0, keep_alive: str = "30s",
        dimensions: int = DEFAULT_EMBED_DIMENSIONS, batch_size: int = 16,
        log_path: str | os.PathLike[str] | None = None,
    ) -> None:
        super().__init__(base_url=base_url, model=model, log_path=log_path)
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("embedding dimensions must be a positive integer")
        self.model_id = self.model
        self.storage_key = self.model.split(":", 1)[0]
        self.model_version = "1"
        self.dimensions = dimensions
        self.timeout_seconds = float(timeout_seconds)
        self.keep_alive = keep_alive
        self.batch_size = max(1, int(batch_size))

    async def embed_query(self, text: str) -> Sequence[float]:
        vectors = await self.embed_documents([text])
        return vectors[0]

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        pending = [str(text) for text in texts]
        if not pending:
            return []
        await self._ensure_ready()
        vectors: list[list[float]] = []
        for start in range(0, len(pending), self.batch_size):
            vectors.extend(await self._embed_batch(pending[start:start + self.batch_size]))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
            "truncate": True,
            "keep_alive": self.keep_alive,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}/api/embed", json=payload)
                if response.status_code == 404:
                    raise OllamaUnavailable(
                        f"本地 Ollama 中找不到向量模型 {self.model}，请先执行 ollama pull {self.model}"
                    )
                response.raise_for_status()
                data = response.json()
        except OllamaUnavailable:
            raise
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"本地向量模型调用失败：{exc}") from exc
        vectors = data.get("embeddings") if isinstance(data, Mapping) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise OllamaUnavailable("本地向量模型返回的向量数量与输入不一致")
        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                actual = len(vector) if isinstance(vector, list) else "未知"
                raise OllamaUnavailable(
                    f"向量维度异常：期望 {self.dimensions}，实际 {actual}；"
                    "请确认向量模型与 knowledge_embeddings 列维度一致"
                )
            normalized.append([float(value) for value in vector])
        return normalized


def ollama_config_from_env(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    return {
        "base_url": env.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        "model": env.get("OLLAMA_CHAT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "keep_alive": env.get("OLLAMA_KEEP_ALIVE", "30s").strip() or "30s",
        "log_path": env.get("OLLAMA_LOG_FILE") or None,
    }


def ollama_embedding_config_from_env(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    raw_dimensions = env.get("OLLAMA_EMBED_DIMENSIONS", str(DEFAULT_EMBED_DIMENSIONS)).strip()
    try:
        dimensions = int(raw_dimensions)
    except ValueError as exc:
        raise ValueError("OLLAMA_EMBED_DIMENSIONS must be an integer") from exc
    return {
        "base_url": env.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        "model": env.get("OLLAMA_EMBED_MODEL", DEFAULT_EMBED_MODEL).strip() or DEFAULT_EMBED_MODEL,
        "keep_alive": env.get("OLLAMA_KEEP_ALIVE", "30s").strip() or "30s",
        "dimensions": dimensions,
        "log_path": env.get("OLLAMA_LOG_FILE") or None,
    }

