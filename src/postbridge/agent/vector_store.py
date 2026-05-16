from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from postbridge.config import get_settings
from postbridge.models.domain import ContentEmbeddingOrm

_MAX_TEXT_HASH_LENGTH = 128


class VectorStore(Protocol):
    def upsert_embedding(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        entity_type: str,
        entity_id: str,
        model_name: str,
        vector: list[float],
        text_hash: str | None,
    ) -> None: ...

    def find_similar(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        vector: list[float],
        entity_type: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...

    def stats(self, *, tenant_id: str, channel_id: str | None = None) -> dict[str, Any]: ...

    def maintain(self, *, tenant_id: str, channel_id: str | None = None, optimize_native: bool = True) -> dict[str, Any]: ...


@dataclass(slots=True)
class PgVectorStore:
    session: Session

    def upsert_embedding(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        entity_type: str,
        entity_id: str,
        model_name: str,
        vector: list[float],
        text_hash: str | None,
    ) -> None:
        text_hash = _normalize_text_hash(text_hash)
        if self._supports_pgvector_native():
            self._upsert_pgvector_native(
                tenant_id=tenant_id,
                channel_id=channel_id,
                entity_type=entity_type,
                entity_id=entity_id,
                model_name=model_name,
                vector=vector,
                text_hash=text_hash,
            )
            self.session.flush()
            return
        row = self.session.scalar(
            select(ContentEmbeddingOrm).where(
                ContentEmbeddingOrm.tenant_id == tenant_id,
                ContentEmbeddingOrm.entity_type == entity_type,
                ContentEmbeddingOrm.entity_id == entity_id,
            )
        )
        payload = json.dumps(vector, ensure_ascii=True)
        if row is None:
            self.session.add(
                ContentEmbeddingOrm(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    model_name=model_name,
                    vector_json=payload,
                    text_hash=text_hash,
                )
            )
        else:
            row.model_name = model_name
            row.vector_json = payload
            row.text_hash = text_hash
        self.session.flush()

    def find_similar(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        vector: list[float],
        entity_type: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        self.session.flush()
        if self._supports_pgvector_native():
            return self._find_similar_pgvector_native(
                tenant_id=tenant_id,
                channel_id=channel_id,
                vector=vector,
                entity_type=entity_type,
                top_k=top_k,
            )
        stmt = select(ContentEmbeddingOrm).where(
            ContentEmbeddingOrm.tenant_id == tenant_id,
            ContentEmbeddingOrm.channel_id == channel_id,
        )
        if entity_type:
            stmt = stmt.where(ContentEmbeddingOrm.entity_type == entity_type)
        rows = list(self.session.scalars(stmt).all())
        scored: list[dict[str, Any]] = []
        for row in rows:
            try:
                other = json.loads(row.vector_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(other, list):
                continue
            score = cosine_similarity(vector, [float(x) for x in other if isinstance(x, (int, float))])
            scored.append(
                {
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "score": score,
                    "text_hash": row.text_hash,
                    "model_name": row.model_name,
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]

    def clear(self) -> None:
        self.session.execute(delete(ContentEmbeddingOrm))
        self.session.flush()

    def stats(self, *, tenant_id: str, channel_id: str | None = None) -> dict[str, Any]:
        base = select(ContentEmbeddingOrm).where(ContentEmbeddingOrm.tenant_id == tenant_id)
        if channel_id:
            base = base.where(ContentEmbeddingOrm.channel_id == channel_id)
        rows = list(self.session.scalars(base).all())
        stored_embeddings = len(rows)
        content_item_embeddings = sum(1 for row in rows if row.entity_type == "content_item")
        candidate_embeddings = sum(1 for row in rows if row.entity_type == "candidate")
        model_counts = Counter(row.model_name for row in rows if row.model_name)
        return {
            "backend": "pgvector",
            "native": self._supports_pgvector_native(),
            "stored_embeddings": int(stored_embeddings or 0),
            "content_item_embeddings": content_item_embeddings,
            "candidate_embeddings": candidate_embeddings,
            "model_counts": dict(model_counts),
        }

    def maintain(self, *, tenant_id: str, channel_id: str | None = None, optimize_native: bool = True) -> dict[str, Any]:
        optimized = False
        native_index_available = False
        if optimize_native and self._supports_pgvector_native():
            native_index_available = True
            self.session.execute(text("ANALYZE content_embeddings"))
            self.session.execute(text("REINDEX INDEX IF EXISTS ix_content_embeddings_vector_pg_ivfflat"))
            optimized = True
        stats = self.stats(tenant_id=tenant_id, channel_id=channel_id)
        return {
            "backend": "pgvector",
            "native": self._supports_pgvector_native(),
            "optimized_native_index": optimized,
            "native_index_available": native_index_available,
            **stats,
        }

    def _supports_pgvector_native(self) -> bool:
        if self.session.bind is None or self.session.bind.dialect.name != "postgresql":
            return False
        vector_type_available = bool(self.session.execute(text("SELECT to_regtype('vector') IS NOT NULL")).scalar())
        if not vector_type_available:
            return False
        vector_column_available = bool(
            self.session.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'content_embeddings'
                      AND column_name = 'vector_pg'
                    """
                )
            ).scalar()
        )
        return vector_column_available

    def _upsert_pgvector_native(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        entity_type: str,
        entity_id: str,
        model_name: str,
        vector: list[float],
        text_hash: str | None,
    ) -> None:
        payload = json.dumps(vector, ensure_ascii=True)
        vector_literal = _vector_literal(vector)
        existing = self.session.scalar(
            select(ContentEmbeddingOrm.id).where(
                ContentEmbeddingOrm.tenant_id == tenant_id,
                ContentEmbeddingOrm.entity_type == entity_type,
                ContentEmbeddingOrm.entity_id == entity_id,
            )
        )
        if existing is None:
            self.session.execute(
                text(
                    """
                    INSERT INTO content_embeddings (
                        id, tenant_id, channel_id, entity_type, entity_id, model_name,
                        vector_json, vector_pg, text_hash, created_at, updated_at
                    )
                    VALUES (
                        :id, :tenant_id, :channel_id, :entity_type, :entity_id, :model_name,
                        :vector_json, CAST(:vector_pg AS vector), :text_hash, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": tenant_id,
                    "channel_id": channel_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "model_name": model_name,
                    "vector_json": payload,
                    "vector_pg": vector_literal,
                    "text_hash": text_hash,
                },
            )
            return
        self.session.execute(
            text(
                """
                UPDATE content_embeddings
                SET model_name = :model_name,
                    vector_json = :vector_json,
                    vector_pg = CAST(:vector_pg AS vector),
                    text_hash = :text_hash,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND entity_type = :entity_type
                  AND entity_id = :entity_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "model_name": model_name,
                "vector_json": payload,
                "vector_pg": vector_literal,
                "text_hash": text_hash,
            },
        )

    def _find_similar_pgvector_native(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        vector: list[float],
        entity_type: str | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT entity_type, entity_id, model_name, text_hash,
                   1 - (vector_pg <=> CAST(:vector_pg AS vector)) AS score
            FROM content_embeddings
            WHERE tenant_id = :tenant_id
              AND channel_id = :channel_id
              AND vector_pg IS NOT NULL
        """
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "channel_id": channel_id,
            "vector_pg": _vector_literal(vector),
            "top_k": top_k,
        }
        if entity_type:
            sql += " AND entity_type = :entity_type"
            params["entity_type"] = entity_type
        sql += " ORDER BY vector_pg <=> CAST(:vector_pg AS vector) LIMIT :top_k"
        rows = self.session.execute(text(sql), params).all()
        return [
            {
                "entity_type": row[0],
                "entity_id": row[1],
                "model_name": row[2],
                "text_hash": row[3],
                "score": float(row[4]) if row[4] is not None else 0.0,
            }
            for row in rows
        ]


def get_vector_store(session: Session) -> VectorStore:
    settings = get_settings()
    if settings.agent_vector_backend != "pgvector":
        raise ValueError("only pgvector backend is supported")
    return PgVectorStore(session=session)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.10f}" for value in vector) + "]"


def _normalize_text_hash(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:_MAX_TEXT_HASH_LENGTH]
