"""Async Neo4j Aura client (§B4.3, §B12.3): connectivity with retries, read transactions with a timeout."""
from __future__ import annotations

import asyncio
import logging

from neo4j import AsyncDriver, AsyncGraphDatabase, unit_of_work

log = logging.getLogger(__name__)

QUERY_TIMEOUT_S = 3.0  # §B12.3


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str | None, timeout: float = QUERY_TIMEOUT_S):
        self.driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self.timeout = timeout

    async def connect(self, retries: int = 3, backoff: float = 1.0) -> bool:
        """`verify_connectivity` with exponential backoff (1 s, 2 s, ...). False if Aura stays unreachable."""
        for attempt in range(1, retries + 1):
            try:
                await self.driver.verify_connectivity()
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("neo4j connectivity attempt %d/%d failed: %s", attempt, retries, type(e).__name__)
                if attempt < retries:
                    await asyncio.sleep(backoff * 2 ** (attempt - 1))
        return False

    async def read(self, cypher: str, **params) -> list[dict]:
        """Run one read query in a managed read transaction (retried by the driver on transient errors)."""
        @unit_of_work(timeout=self.timeout)
        async def work(tx):
            result = await tx.run(cypher, params)
            return [r.data() async for r in result]

        async with self.driver.session() as session:
            return await session.execute_read(work)

    async def node_count(self) -> int:
        rows = await self.read("MATCH (n) RETURN count(n) AS c")
        return rows[0]["c"]

    async def close(self) -> None:
        await self.driver.close()
