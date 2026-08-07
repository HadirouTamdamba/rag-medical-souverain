import pytest

from config import Settings

BASE = {"_env_file": None, "environment": "production", "api_key": "a" * 32}

# conftest.py sets environment variables for the API; these tests validate the
# model defaults and therefore need a clean environment.
CONFIG_ENV_VARS = (
    "APP_NAME",
    "ENVIRONMENT",
    "API_KEY",
    "ALLOWED_ORIGINS",
    "LOG_LEVEL",
    "MAX_UPLOAD_MB",
    "DEMO_DOCS_DIR",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_REQUESTS",
    "RATE_LIMIT_WINDOW_S",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides):
    return Settings(**{**BASE, **overrides})


def test_production_requires_a_strong_api_key():
    with pytest.raises(ValueError, match="API_KEY"):
        _settings(api_key=None)

    with pytest.raises(ValueError, match="API_KEY"):
        _settings(api_key="court")


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        _settings(ALLOWED_ORIGINS="*")


def test_production_disables_the_openapi_docs():
    settings = _settings(ALLOWED_ORIGINS="https://medibot.chu.fr", docs_enabled=True)
    assert settings.docs_enabled is False
    assert settings.is_production is True


def test_local_environment_stays_permissive():
    settings = Settings(_env_file=None, environment="local")
    assert settings.is_production is False
    assert settings.docs_enabled is True
    assert settings.api_key is None


def test_allowed_origins_are_split_and_trimmed():
    settings = Settings(_env_file=None, ALLOWED_ORIGINS="http://a.fr, http://b.fr ,")
    assert settings.allowed_origins == ["http://a.fr", "http://b.fr"]


def test_chunk_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        Settings(_env_file=None, chunk_size=500, chunk_overlap=500)


def test_invalid_log_level_is_rejected():
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        Settings(_env_file=None, log_level="verbeux")


def test_qdrant_url_takes_precedence_over_host_and_port():
    settings = Settings(_env_file=None, qdrant_url="https://qdrant.chu.fr:6333", qdrant_api_key="k")
    assert settings.qdrant_kwargs["url"] == "https://qdrant.chu.fr:6333"
    assert "host" not in settings.qdrant_kwargs

    default = Settings(_env_file=None)
    assert default.qdrant_kwargs["host"] == "localhost"
    assert default.qdrant_kwargs["port"] == 6333


def test_max_upload_bytes_conversion():
    assert Settings(_env_file=None, max_upload_mb=3).max_upload_bytes == 3 * 1024 * 1024
