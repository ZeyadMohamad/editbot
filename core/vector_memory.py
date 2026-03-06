"""
Persistent local vector memory for EditBot using ChromaDB.

This module provides:
- ChromaDB persistent local storage (offline)
- Incremental source upserts using fingerprints
- Cosine similarity retrieval
- Session-aware retrieval boosts
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from core.logging import setup_logger

logger = setup_logger("vector_memory")

TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")
CLI_KNOWLEDGE_RE = re.compile(
    r"(?i)(\bcli\b|command\s*line|python\s+-m\s+app\.main|--interactive|-v/--video|--video|--prompt|\bargparse\b|\bcli\s+arguments\b)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> List[str]:
    """
    Chunk long text into overlapping windows.
    The chunking is character-based to avoid extra dependencies.
    """
    if not text:
        return []
    clean = text.strip()
    if len(clean) <= max_chars:
        return [clean]

    chunks: List[str] = []
    start = 0
    size = max(128, max_chars)
    overlap = max(0, min(overlap, size - 32))
    while start < len(clean):
        end = min(start + size, len(clean))
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return chunks


def filter_cli_chunks(chunks: List[str]) -> List[str]:
    """
    Remove chunks that are specifically about CLI usage/arguments.
    This keeps QA retrieval focused on editing capabilities and tool behavior.
    """
    filtered: List[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        if CLI_KNOWLEDGE_RE.search(chunk):
            continue
        filtered.append(chunk)
    return filtered


@dataclass
class SearchResult:
    entry_id: str
    score: float
    text: str
    metadata: Dict[str, Any]


class LocalVectorStore:
    """
    Persistent vector store backed by ChromaDB with semantic embeddings.
    Uses ChromaDB's default embedding function (all-MiniLM-L6-v2 via onnxruntime)
    for proper semantic similarity. Falls back to hash-based embeddings only if
    the default embedding function is unavailable.
    """

    def __init__(self, db_path: Path, dimension: int = 384, collection_name: str = "editbot_memory"):
        self.persist_dir = Path(db_path)
        # Backward compatibility: if a file path is passed, use sibling chroma dir.
        if self.persist_dir.suffix:
            self.persist_dir = self.persist_dir.parent / "chroma_db"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.dimension = int(dimension)
        self.collection_name = collection_name
        self._lock = threading.RLock()
        self._use_default_embeddings = True

        try:
            import chromadb  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "ChromaDB is not installed. Install with `pip install chromadb`."
            ) from exc

        self._chromadb = chromadb
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))

        # Try to use default embedding function (semantic); fall back to hash-based
        embedding_fn = None
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            embedding_fn = DefaultEmbeddingFunction()
            # Quick smoke test
            _test = embedding_fn(["test"])
            if _test and len(_test[0]) > 0:
                logger.info("Using ChromaDB default semantic embeddings (all-MiniLM-L6-v2)")
            else:
                raise ValueError("Empty embedding result")
        except Exception as emb_exc:
            logger.warning(f"Default embedding unavailable ({emb_exc}); falling back to hash-based embeddings")
            self._use_default_embeddings = False
            embedding_fn = None

        # Recreate collection with proper embedding for semantic search
        create_kwargs = {
            "name": self.collection_name,
            "metadata": {"hnsw:space": "cosine"},
        }
        if embedding_fn is not None:
            create_kwargs["embedding_function"] = embedding_fn
        self.collection = self.client.get_or_create_collection(**create_kwargs)

    def _tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        return [token.lower() for token in TOKEN_RE.findall(text)]

    def _embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimension
        tokens = self._tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], byteorder="big", signed=False) % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    def _sanitize_metadata_value(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)):
            return value
        if value is None:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    def _sanitize_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        safe: Dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            safe[str(key)] = self._sanitize_metadata_value(value)
        return safe

    def _existing_ids_for_source(self, source_key: str) -> List[str]:
        try:
            existing = self.collection.get(where={"source_key": source_key})
            return list(existing.get("ids") or [])
        except Exception:
            return []

    def upsert_source_chunks(
        self,
        source_key: str,
        chunks: List[str],
        metadata_base: Optional[Dict[str, Any]] = None,
        fingerprint: Optional[str] = None,
    ) -> int:
        """
        Upsert document chunks tied to a stable source key.
        If fingerprint is unchanged, no-op.
        """
        metadata_base = metadata_base or {}
        fingerprint = fingerprint or hashlib.sha1(
            "\n".join(chunks).encode("utf-8")
        ).hexdigest()

        with self._lock:
            cleaned_chunks = [chunk for chunk in chunks if chunk and chunk.strip()]
            expected_ids = [
                hashlib.sha1(f"{source_key}:{idx}:{fingerprint}".encode("utf-8")).hexdigest()
                for idx in range(len(cleaned_chunks))
            ]

            # Skip re-upsert when fingerprint and ids already match.
            existing_ids = self._existing_ids_for_source(source_key)
            if existing_ids and set(existing_ids) == set(expected_ids):
                return 0

            if existing_ids:
                self.collection.delete(ids=existing_ids)

            if not cleaned_chunks:
                return 0

            metadatas: List[Dict[str, Any]] = []
            embeddings: List[List[float]] = []
            for idx, chunk in enumerate(cleaned_chunks):
                metadata = dict(metadata_base)
                metadata.update(
                    {
                        "source_key": source_key,
                        "chunk_index": idx,
                        "chunk_total": len(cleaned_chunks),
                        "fingerprint": fingerprint,
                        "created_at": _utc_now(),
                    }
                )
                metadatas.append(self._sanitize_metadata(metadata))
                embeddings.append(self._embed(chunk))

            upsert_kwargs = {
                "ids": expected_ids,
                "documents": cleaned_chunks,
                "metadatas": metadatas,
            }
            if not self._use_default_embeddings:
                upsert_kwargs["embeddings"] = embeddings
            self.collection.upsert(**upsert_kwargs)
            return len(expected_ids)

    def add_entry(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        source_key: Optional[str] = None,
    ) -> str:
        metadata = metadata or {}
        source_key = source_key or f"event::{uuid4().hex}"
        entry_id = hashlib.sha1(
            f"{source_key}:{metadata.get('event_id', uuid4().hex)}".encode("utf-8")
        ).hexdigest()

        with self._lock:
            safe_meta = self._sanitize_metadata(dict(metadata, source_key=source_key, created_at=_utc_now()))
            upsert_kwargs = {
                "ids": [entry_id],
                "documents": [text],
                "metadatas": [safe_meta],
            }
            if not self._use_default_embeddings:
                upsert_kwargs["embeddings"] = [self._embed(text)]
            self.collection.upsert(**upsert_kwargs)
        return entry_id

    def index_file(
        self,
        file_path: Path,
        source_type: str = "system_file",
        max_chars: int = 1200,
        overlap: int = 200,
    ) -> int:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return 0

        text = _safe_read_text(path)
        if not text.strip():
            return 0

        fingerprint = hashlib.sha1(text.encode("utf-8")).hexdigest()
        source_key = f"file::{path.as_posix()}"
        chunks = filter_cli_chunks(chunk_text(text, max_chars=max_chars, overlap=overlap))
        if not chunks:
            return 0
        return self.upsert_source_chunks(
            source_key=source_key,
            chunks=chunks,
            metadata_base={
                "type": source_type,
                "path": path.as_posix(),
                "name": path.name,
            },
            fingerprint=fingerprint,
        )

    def search(
        self,
        query: str,
        top_k: int = 6,
        min_score: float = 0.08,
        session_id: Optional[str] = None,
        include_assistant_chat: bool = False,
    ) -> List[SearchResult]:
        if not query:
            return []

        query_lower = query.lower()
        capability_query = any(
            token in query_lower
            for token in [
                "what can you do",
                "capabilities",
                "features",
                "what do you do",
                "available tools",
                "tools available",
                "supported",
                "commands",
            ]
        )

        n_results = max(top_k * 4, top_k, 8)

        try:
            query_kwargs = {
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            if self._use_default_embeddings:
                query_kwargs["query_texts"] = [query]
            else:
                query_vector = self._embed(query)
                if not any(query_vector):
                    return []
                query_kwargs["query_embeddings"] = [query_vector]
            response = self.collection.query(**query_kwargs)
        except Exception as exc:
            logger.warning(f"Vector search failed: {exc}")
            return []

        ids = (response.get("ids") or [[]])[0]
        docs = (response.get("documents") or [[]])[0]
        metas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        results: List[SearchResult] = []
        for idx, entry_id in enumerate(ids):
            metadata = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
            meta_type = str(metadata.get("type", "")).strip().lower()
            meta_role = str(metadata.get("role", "")).strip().lower()
            if not include_assistant_chat and meta_type == "chat_turn" and meta_role == "assistant":
                continue
            distance = distances[idx] if idx < len(distances) else 1.0
            try:
                score = 1.0 - float(distance)
            except Exception:
                score = 0.0

            # Favor session-local events slightly while still keeping system knowledge.
            if session_id and metadata.get("session_id") == session_id:
                score *= 1.12

            # Prefer authoritative sources over conversational residue.
            if meta_type == "system_file":
                score *= 1.10
            elif meta_type == "operation":
                score *= 1.12
            elif meta_type == "chat_turn":
                # User chat can still help disambiguate, but should not dominate.
                score *= 0.88

            source_kind = str(metadata.get("source_kind", "")).strip().lower()
            path_value = str(metadata.get("path", "")).strip().lower()
            if capability_query:
                if meta_type == "chat_turn":
                    score *= 0.60
                if source_kind == "knowledge_pack" or "registry/tools.json" in path_value:
                    score *= 1.30

            if score < min_score:
                continue
            results.append(
                SearchResult(
                    entry_id=str(entry_id),
                    score=score,
                    text=str(docs[idx]) if idx < len(docs) else "",
                    metadata=metadata,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[: max(1, top_k)]
