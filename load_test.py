"""
Load test for the AI Prompt Queue API.
Run with: locust -f scripts/load_test.py --host=http://localhost:8000

Simulates realistic traffic:
  - 80% unique prompts (cache misses) → hit the LLM pipeline
  - 20% repeated/similar prompts (cache hits) → served from semantic cache

Target: sustain 300 req/min to LLM, unlimited cache-hit throughput.
"""
from __future__ import annotations

import random
import time
import uuid

from locust import HttpUser, between, task

UNIQUE_PROMPTS = [
    "Explain the difference between supervised and unsupervised learning.",
    "What are the SOLID principles in software engineering?",
    "How does garbage collection work in Python?",
    "Describe the CAP theorem and give a real-world example.",
    "What is the difference between TCP and UDP?",
    "Explain transformer architecture in neural networks.",
    "How does consistent hashing work?",
    "What is the difference between a mutex and a semaphore?",
    "Explain eventual consistency in distributed systems.",
    "What is the time complexity of quicksort?",
    "How does HTTPS work under the hood?",
    "What is the difference between a process and a thread?",
    "Explain the concept of idempotency in REST APIs.",
    "What is the purpose of a bloom filter?",
    "How does Redis implement sorted sets internally?",
]

SIMILAR_PROMPTS = [
    "Can you explain what supervised learning is vs unsupervised?",
    "What do SOLID principles mean in OOP?",
    "How does Python's garbage collector work?",
]


class PromptQueueUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        self.submitted_jobs = []

    @task(8)
    def submit_unique_prompt(self):
        """Submit a unique prompt — expected cache miss."""
        prompt = random.choice(UNIQUE_PROMPTS) + f" (run {uuid.uuid4().hex[:4]})"
        with self.client.post(
            "/api/v1/prompts",
            json={"prompt": prompt, "model": "gpt-4o-mini"},
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                data = resp.json()
                self.submitted_jobs.append(data["job_id"])
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task(2)
    def submit_similar_prompt(self):
        """Submit a near-duplicate prompt — expected cache hit."""
        prompt = random.choice(SIMILAR_PROMPTS)
        with self.client.post(
            "/api/v1/prompts",
            json={"prompt": prompt, "model": "gpt-4o-mini"},
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                data = resp.json()
                if data.get("cache_hit"):
                    resp.success()
                else:
                    resp.success()  # Still OK, just not cached yet
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task(3)
    def poll_job_status(self):
        """Poll a previously submitted job."""
        if not self.submitted_jobs:
            return
        job_id = random.choice(self.submitted_jobs)
        with self.client.get(
            f"/api/v1/prompts/{job_id}",
            name="/api/v1/prompts/[job_id]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
                if resp.status_code == 200:
                    data = resp.json()
                    if data["status"] == "completed":
                        # Clean up completed jobs from tracking list
                        self.submitted_jobs = [
                            j for j in self.submitted_jobs if j != job_id
                        ]
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task(1)
    def check_health(self):
        self.client.get("/api/v1/health")
