"""
Pre-warm the semantic cache with common prompts.
Run this before load testing to ensure cache hits for known queries.

Usage:
    python scripts/seed_cache.py --host http://localhost:8000
"""
from __future__ import annotations

import argparse
import asyncio
import time

import httpx

SEED_PROMPTS = [
    {"prompt": "What is machine learning?", "model": "gpt-4o-mini"},
    {"prompt": "Explain neural networks in simple terms.", "model": "gpt-4o-mini"},
    {"prompt": "What is the difference between SQL and NoSQL?", "model": "gpt-4o-mini"},
    {"prompt": "How does a REST API work?", "model": "gpt-4o-mini"},
    {"prompt": "What is Docker and why is it used?", "model": "gpt-4o-mini"},
    {"prompt": "Explain the concept of microservices.", "model": "gpt-4o-mini"},
    {"prompt": "What is Kubernetes?", "model": "gpt-4o-mini"},
    {"prompt": "How does OAuth 2.0 work?", "model": "gpt-4o-mini"},
    {"prompt": "What is a hash table?", "model": "gpt-4o-mini"},
    {"prompt": "Explain Big O notation.", "model": "gpt-4o-mini"},
]


async def seed(host: str):
    async with httpx.AsyncClient(base_url=host, timeout=30) as client:
        print(f"Seeding {len(SEED_PROMPTS)} prompts into {host}...")

        job_ids = []
        for p in SEED_PROMPTS:
            resp = await client.post("/api/v1/prompts", json=p)
            data = resp.json()
            job_id = data["job_id"]
            job_ids.append(job_id)
            print(f"  Submitted: {p['prompt'][:50]!r} → {job_id}")
            await asyncio.sleep(0.2)   # Don't burst the queue

        print(f"\nWaiting for {len(job_ids)} jobs to complete...")

        for job_id in job_ids:
            if job_id.startswith("cache_"):
                print(f"  {job_id}: instant cache hit ✓")
                continue

            for _ in range(30):  # Wait up to 30s
                resp = await client.get(f"/api/v1/prompts/{job_id}")
                data = resp.json()
                if data["status"] == "completed":
                    print(f"  {job_id}: completed ✓")
                    break
                elif data["status"] == "failed":
                    print(f"  {job_id}: FAILED ✗")
                    break
                await asyncio.sleep(1)
            else:
                print(f"  {job_id}: timeout waiting for completion")

        print("\nCache pre-warming complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="http://localhost:8000")
    args = parser.parse_args()
    asyncio.run(seed(args.host))
