"""
向量存储与检索服务 (RAG)。

基于 Qdrant 实现：
- 分析文本 embedding → 存入 Qdrant collection
- 向量相似度检索 + metadata 过滤
- 支持 hybrid search（向量 + 关键词）
"""

import logging
import uuid
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    chunk_id: str
    text: str
    score: float
    metadata: dict


COLLECTION_NAME = "analysis_chunks"
VECTOR_SIZE = 1536  # text-embedding-3-small


class RAGService:
    """
    向量检索服务。

    使用方式：
        rag = RAGService()
        await rag.index(text="分析文本...", metadata={...})
        results = await rag.search("查询文本", filters={"grade": "二年级"})
    """

    def __init__(self):
        self._client = None
        self._embedding_client = None
        from app.core.config import get_settings
        self._settings = get_settings()
        self._qdrant_available: bool | None = None  # tri-state: None=unchecked, True/False

    def _is_qdrant_available(self) -> bool:
        """Check if Qdrant is reachable. Cache result to avoid repeated timeouts."""
        if self._qdrant_available is not None:
            return self._qdrant_available
        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(
                host=self._settings.QDRANT_HOST,
                port=self._settings.QDRANT_PORT,
                timeout=3,
            )
            client.get_collections()  # Quick health check
            self._qdrant_available = True
        except Exception:
            logger.warning("Qdrant is not available — vector search will return empty results")
            self._qdrant_available = False
        return self._qdrant_available

    @property
    def client(self):
        if self._client is None:
            if not self._is_qdrant_available():
                raise RuntimeError("Qdrant is not available")
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            self._client = QdrantClient(
                host=self._settings.QDRANT_HOST,
                port=self._settings.QDRANT_PORT,
            )

            # Ensure collection exists
            if not self._client.collection_exists(COLLECTION_NAME):
                self._client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=VECTOR_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection: %s", COLLECTION_NAME)

        return self._client

    @property
    def embedding_client(self):
        if self._embedding_client is None:
            from openai import AsyncOpenAI
            from app.core.config import get_settings

            settings = get_settings()
            self._embedding_client = AsyncOpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_API_BASE,
            )
        return self._embedding_client

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化"""
        from app.core.config import get_settings
        settings = get_settings()

        response = await self.embedding_client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=texts,
        )
        return [d.embedding for d in response.data]

    async def index(
        self,
        text: str,
        metadata: dict,
        chunk_id: str | None = None,
    ):
        """
        将分析文本向量化并存入 Qdrant。

        Args:
            text: 要索引的分析文本
            metadata: 附加元数据 {assignment_id, grade, subject, ...}
            chunk_id: 可选，自定义主键
        """
        if not self._is_qdrant_available():
            logger.info("RAG index skipped: Qdrant not available")
            return
        if chunk_id is None:
            chunk_id = str(uuid.uuid4())

        # Enrich text for better retrieval
        enriched = self._enrich_text(text, metadata)
        embedding = await self._embed([enriched])

        from qdrant_client.models import PointStruct
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=chunk_id,
                    vector=embedding[0],
                    payload={
                        "text": enriched,
                        "metadata": metadata,
                    },
                )
            ],
        )
        logger.info("Indexed chunk %s in Qdrant", chunk_id)

    async def batch_index(
        self,
        chunks: list[dict],
    ):
        """
        批量索引。

        Args:
            chunks: [{"text": "...", "metadata": {...}}, ...]
        """
        if not chunks:
            return
        if not self._is_qdrant_available():
            logger.info("RAG batch_index skipped: Qdrant not available")
            return

        # Enrich all
        enriched_texts = [self._enrich_text(c["text"], c.get("metadata", {})) for c in chunks]
        embeddings = await self._embed(enriched_texts)

        from qdrant_client.models import PointStruct
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embeddings[i],
                payload={
                    "text": enriched_texts[i],
                    "metadata": chunks[i].get("metadata", {}),
                },
            )
            for i in range(len(chunks))
        ]

        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
        )
        logger.info("Batch indexed %d chunks in Qdrant", len(chunks))

    async def search(
        self,
        query: str,
        filters: dict | None = None,
        limit: int = 5,
        score_threshold: float = 0.7,
    ) -> list[SearchResult]:
        """
        向量相似度检索。

        Args:
            query: 查询文本
            filters: metadata 过滤条件 {"grade": "二年级", "subject": "数学"}
            limit: 返回数量
            score_threshold: 最低相似度阈值

        Returns:
            按相似度降序排列的搜索结果。Qdrant 不可用时返回空列表。
        """
        if not self._is_qdrant_available():
            logger.info("RAG search skipped: Qdrant not available (dev mode fallback)")
            return []

        query_vec = await self._embed([query])

        from qdrant_client.models import Filter, FieldCondition, MatchValue
        search_filter = None
        if filters:
            conditions = [
                FieldCondition(key=f"metadata.{k}", match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            search_filter = Filter(must=conditions)

        results = self.client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vec[0],
            query_filter=search_filter,
            limit=limit,
            score_threshold=score_threshold,
        )

        return [
            SearchResult(
                chunk_id=str(r.id),
                text=r.payload.get("text", ""),
                score=r.score,
                metadata=r.payload.get("metadata", {}),
            )
            for r in results
        ]

    def _enrich_text(self, text: str, metadata: dict) -> str:
        """丰富分析文本，提升向量检索效果"""
        grade = metadata.get("grade", "")
        subject = metadata.get("subject", "")
        semester = metadata.get("semester", "")
        month = metadata.get("month", "")

        prefix = f"作业分析：{grade}{subject}，{semester}学期{month}月。"
        return prefix + text
