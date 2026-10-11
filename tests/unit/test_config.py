"""Test unitari della lettura dei secret in `marketmind_common.config`.

Ogni variabile può arrivare anche da un file indicato da `<NOME>_FILE` (secret
montati in un volume Docker): il file ha precedenza sulla variabile semplice.
"""

from __future__ import annotations

import pytest

from marketmind_common.config import get_api_key, get_database_url

_DB_VARS = (
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _DB_VARS + ("TEST_API_KEY",):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    return monkeypatch


def test_api_key_from_env(clean_env):
    clean_env.setenv("TEST_API_KEY", "abc")
    assert get_api_key("TEST_API_KEY") == "abc"


def test_api_key_from_file_strips_trailing_newline(clean_env, tmp_path):
    secret = tmp_path / "key"
    secret.write_text("from-file\n")
    clean_env.setenv("TEST_API_KEY_FILE", str(secret))
    assert get_api_key("TEST_API_KEY") == "from-file"


def test_api_key_file_takes_precedence_over_env(clean_env, tmp_path):
    secret = tmp_path / "key"
    secret.write_text("from-file")
    clean_env.setenv("TEST_API_KEY", "from-env")
    clean_env.setenv("TEST_API_KEY_FILE", str(secret))
    assert get_api_key("TEST_API_KEY") == "from-file"


def test_api_key_missing_raises(clean_env):
    with pytest.raises(RuntimeError, match="TEST_API_KEY"):
        get_api_key("TEST_API_KEY")


def test_api_key_empty_file_raises(clean_env, tmp_path):
    secret = tmp_path / "key"
    secret.write_text("\n")
    clean_env.setenv("TEST_API_KEY_FILE", str(secret))
    with pytest.raises(RuntimeError, match="TEST_API_KEY"):
        get_api_key("TEST_API_KEY")


def test_missing_secret_file_raises_with_path(clean_env, tmp_path):
    missing = tmp_path / "nope"
    clean_env.setenv("TEST_API_KEY_FILE", str(missing))
    with pytest.raises(RuntimeError, match="nope"):
        get_api_key("TEST_API_KEY")


def test_database_url_password_from_file(clean_env, tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("s3cret\n")
    clean_env.setenv("POSTGRES_PASSWORD_FILE", str(secret))
    clean_env.setenv("POSTGRES_HOST", "marketmind-db")
    assert get_database_url() == "postgresql+psycopg://marketmind:s3cret@marketmind-db:5432/marketmind"


def test_database_url_defaults(clean_env):
    assert get_database_url() == "postgresql+psycopg://marketmind:marketmind@127.0.0.1:5432/marketmind"
