# Enterprise Knowledge Assistant

A production-shaped GenAI assistant for the fictional **Acme Corp**. It answers natural-language questions over policies, SOPs, manuals, incident reports, FAQs and structured data, using **hybrid retrieval** (Qdrant dense + BM25 + Neo4j Aura graph traversal, fused with RRF and re-ranked by a cross-encoder). A **bounded LangGraph agent** with SQL, Python, web search and MCP tools does the reasoning, **NeMo Guardrails** (with Presidio and LLM Guard) wraps it, **FastAPI** serves it, and **Langfuse** traces it. The whole thing ships as a single Docker image on Hugging Face Spaces.

The build spec, architecture and progress log live in [`capstonespec.md`](capstonespec.md).

```bash
make setup        # venv + pinned deps (requirements.lock)
cp .env.example .env   # then fill in the keys
make check-env    # verify every external service
make run          # http://localhost:7860/health
```
