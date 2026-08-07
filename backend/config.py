"""Typed application configuration, validated at startup.

All configuration comes from environment variables (12-factor). When
`ENVIRONMENT=production`, settings that would be unsafe in a hospital
deployment (no API key, wide-open CORS, exposed API docs) abort startup
rather than silently degrading security.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCTION_ENVIRONMENTS = {"production", "prod", "preproduction", "preprod"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Identity and runtime ----------------------------------------------
    app_name: str = "MediBot RAG"
    environment: str = "local"
    api_key: str | None = None
    log_level: str = "INFO"
    # JSON logs: required for log shipping into the hospital's Loki/ELK stack.
    log_json: bool = False
    # Serves /docs and /openapi.json. Forced off in production.
    docs_enabled: bool = True

    # --- Qdrant -------------------------------------------------------------
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    # Takes precedence over host/port; lets you target a managed Qdrant over https.
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_timeout_s: float = 10.0
    collection_name: str = Field("medical_docs", validation_alias="QDRANT_COLLECTION")
    # HNSW search effort: higher recall at the cost of latency.
    qdrant_hnsw_ef: int = 128

    # --- Ollama / LLM -------------------------------------------------------
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "mistral"
    ollama_timeout_s: float = 120.0
    # How long Ollama keeps the model resident after a request. On CPU a reload
    # costs several minutes, so keep this high.
    ollama_keep_alive: str = "60m"
    ollama_max_retries: int = 2
    # A CPU-only backend can only usefully serve a couple of generations at a
    # time; beyond that they compete for the CPU and *all* of them slow down.
    # The semaphore caps concurrent calls and makes the rest queue instead.
    llm_max_concurrency: int = 2
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    # Context window requested from Ollama. It must cover system prompt + RAG
    # context + history + answer, otherwise Ollama silently truncates the
    # BEGINNING of the prompt and the instructions are lost.
    llm_num_ctx: int = 4096
    # Language the assistant answers in. The default targets French hospitals;
    # change it to serve another audience.
    llm_response_language: str = "French"
    # Returned verbatim when retrieval finds nothing relevant. Keep it in sync
    # with `llm_response_language`: it bypasses the model entirely.
    llm_no_context_answer: str = "Je ne dispose pas de cette information dans ma base de connaissances."

    # --- Embeddings and RAG -------------------------------------------------
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Torch threads per process. With several uvicorn workers, letting torch
    # grab every core causes contention; 1-2 is optimal.
    embedding_threads: int = 2
    embedding_cache_size: int = 512
    rag_top_k: int = 5
    min_relevance_score: float = 0.35
    max_context_chars: int = 12000
    max_history_messages: int = 6

    chunk_size: int = 1000
    chunk_overlap: int = 150

    # --- Ingestion ----------------------------------------------------------
    upload_dir: Path = Path("/tmp/medical-rag-uploads")
    max_upload_mb: int = 25
    demo_docs_dir: Path = Path("/demo_docs")

    # --- Exposure -----------------------------------------------------------
    allowed_origins_raw: str = Field("http://localhost:3000", validation_alias="ALLOWED_ORIGINS")
    # Only enable behind a trusted reverse proxy: otherwise the header is
    # forgeable and defeats rate limiting.
    trust_proxy_headers: bool = False
    metrics_enabled: bool = True
    # Protects /metrics when it is not already isolated by the network or ingress.
    metrics_require_api_key: bool = False

    # --- Rate limiting ------------------------------------------------------
    # In-memory sliding window, keyed by identity (API key, else IP). Fine for a
    # single instance; behind several replicas, move it to the reverse proxy or
    # a shared Redis (see README).
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 30
    rate_limit_window_s: int = 60

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid LOG_LEVEL: {value}")
        return level

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_below_chunk(cls, value: int, info) -> int:
        chunk_size = info.data.get("chunk_size")
        if chunk_size is not None and value >= chunk_size:
            raise ValueError("CHUNK_OVERLAP must be strictly smaller than CHUNK_SIZE.")
        return value

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> "Settings":
        if not self.is_production:
            return self

        if not self.api_key or len(self.api_key) < 16:
            raise ValueError(
                "ENVIRONMENT=production requires an API_KEY of at least 16 characters "
                "(or strong upstream authentication, see README)."
            )
        if "*" in self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS cannot be '*' in production.")
        if not self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must list at least one origin in production.")

        # Never publish the API schema on a hospital deployment.
        self.docs_enabled = False
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in PRODUCTION_ENVIRONMENTS

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins_raw.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def qdrant_kwargs(self) -> dict:
        """Connection arguments: managed URL when provided, otherwise host/port."""
        if self.qdrant_url:
            return {
                "url": self.qdrant_url,
                "api_key": self.qdrant_api_key,
                "timeout": self.qdrant_timeout_s,
            }
        return {
            "host": self.qdrant_host,
            "port": self.qdrant_port,
            "api_key": self.qdrant_api_key,
            "timeout": self.qdrant_timeout_s,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
