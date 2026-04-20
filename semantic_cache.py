"""
Semantic cache using Redis + OpenAI embeddings.

Flow:
  1. Embed incoming prompt → 256-dim float vector
  2. Run RediSearch KNN (k=1) against stored embeddings
  3. If cosine similarity >= THRESHOLD → cache hit, return stored response
  4. On miss: store embedding + response after LLM call

Why cosine similarity and not exact key matching?
  Near-duplicate prompts ("Explain X" vs "Can you explain X?") share
  high embedding similarity and deserve the same cached response.
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Optional

import numpy as np
from openai import AsyncOpenAI

from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.metrics import CACHE_HITS, CACHE_MISSES

logger = get_logger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 256          # Reduced dims for speed + cost
INDEX_NAME = "prompt_cache"
KEY_PREFIX = "cache:prompt:"


class SemanticCache:
    def __init__(self, redis):
        self.redis = redis
        self._openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.threshold = settings.CACHE_SIMILARITY_THRESHOLD
        self.ttl = settings.CACHE_TTL_SECONDS

    async def initialize(self):
        """Create RediSearch index for vector similarity search."""
        try:
            await self.redis.execute_command("FT.INFO", INDEX_NAME)
            logger.info("RediSearch index already exists", index=INDEX_NAME)
        except Exception:
            # Index does not exist, create it
            await self.redis.execute_command(
                "FT.CREATE", INDEX_NAME,
                "ON", "HASH",
                "PREFIX", "1", KEY_PREFIX,
                "SCHEMA",
                "embedding", "VECTOR", "HNSW", "6",
                    "TYPE", "FLOAT32",
                    "DIM", str(EMBEDDING_DIMS),
                    "DISTANCE_METRIC", "COSINE",
                "model", "TEXT",
                "prompt_hash", "TAG",
            )
            logger.info("RediSearch index created", index=INDEX_NAME)

    async def _embed(self, text: str) -> np.ndarray:
        """Generate embedding for text via OpenAI."""
        response = await self._openai.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
            dimensions=EMBEDDING_DIMS,
        )
        return np.array(response.data[0].embedding, dtype=np.float32)

    def _vec_to_bytes(self, vec: np.ndarray) -> bytes:
        return struct.pack(f"{len(vec)}f", *vec)

    async def lookup(self, prompt: str, model: str = "gpt-4o-mini") -> Optional[dict]:
        """
        Returns cached response dict if similarity >= threshold, else None.
        Response dict: { job_id, response, similarity }
        """
        try:
            embedding = await self._embed(prompt)
            vec_bytes = self._vec_to_bytes(embedding)

            results = await self.redis.execute_command(
                "FT.SEARCH", INDEX_NAME,
                f"(@model:{{{model}}})",
                "=>[KNN", "1", "@embedding", "$vec", "AS", "score", "]",
                "PARAMS", "2", "vec", vec_bytes,
                "RETURN", "3", "response", "job_id", "score",
                "SORTBY", "score",
                "DIALECT", "2",
            )

            if not results or results[0] == 0:
                CACHE_MISSES.inc()
                return None

            # results = [count, key, [field, val, ...], ...]
            fields = dict(zip(results[2][::2], results[2][1::2]))
            similarity = 1.0 - float(fields.get("score", 1.0))  # COSINE distance → similarity

            if similarity >= self.threshold:
                logger.info("Semantic cache hit", similarity=round(similarity, 4), model=model)
                CACHE_HITS.inc()
                return {
                    "job_id": fields.get("job_id", "unknown"),
                    "response": fields.get("response", ""),
                    "similarity": similarity,
                }

            CACHE_MISSES.inc()
            return None

        except Exception as exc:
            logger.warning("Cache lookup failed, treating as miss", error=str(exc))
            CACHE_MISSES.inc()
            return None

    async def store(self, job_id: str, prompt: str, model: str, response: str):
        """Store prompt + embedding + response in Redis."""
        try:
            embedding = await self._embed(prompt)
            vec_bytes = self._vec_to_bytes(embedding)
            prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

            key = f"{KEY_PREFIX}{job_id}"
            mapping = {
                "job_id": job_id,
                "prompt": prompt[:500],        # Truncate for storage efficiency
                "prompt_hash": prompt_hash,
                "model": model,
                "response": response,
                "embedding": vec_bytes,
            }

            await self.redis.hset(key, mapping=mapping)
            await self.redis.expire(key, self.ttl)
            logger.info("Cached response", job_id=job_id, model=model)

        except Exception as exc:
            logger.warning("Cache store failed", job_id=job_id, error=str(exc))

    async def invalidate(self, job_id: str):
        await self.redis.delete(f"{KEY_PREFIX}{job_id}")

    async def size(self) -> int:
        try:
            info = await self.redis.execute_command("FT.INFO", INDEX_NAME)
            info_dict = dict(zip(info[::2], info[1::2]))
            return int(info_dict.get("num_docs", 0))
        except Exception:
            return 0
