"""Application settings, loaded from the environment / `.env` (§B4.4)."""
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- External services ---
    openai_api_key: SecretStr | None = None
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    tavily_api_key: SecretStr | None = None
    hf_token: SecretStr | None = None  # deploy only; not used by the app

    # --- Models ---
    model_strong: str = "gpt-4o"
    model_fast: str = "gpt-4o-mini"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "BAAI/bge-reranker-base"

    # --- Storage ---
    qdrant_path: str = "./data/qdrant"
    sqlite_path: str = "./data/acme.db"
    sessions_db: str = "./data/sessions.db"  # LangGraph checkpointer (§B9.2)
    raw_dir: str = "./data/raw"  # filesystem MCP server root

    # --- MCP servers (§B9.3); pinned so a new release can't break startup (D-021) ---
    use_mcp: bool = True
    mcp_sqlite_package: str = "mcp-server-sqlite==2025.4.25"
    mcp_filesystem_package: str = "@modelcontextprotocol/server-filesystem@2.1.0"

    # --- Behaviour ---
    cache_sim_threshold: float = 0.92
    cache_ttl_seconds: int = 86400
    agent_max_steps: int = 5
    admin_token: SecretStr = SecretStr("change-me")

    # --- Feature flags (ablations, §B14.2) ---
    use_bm25: bool = True
    use_graph: bool = True
    use_rerank: bool = True
    use_cache: bool = True
    use_guardrails: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
