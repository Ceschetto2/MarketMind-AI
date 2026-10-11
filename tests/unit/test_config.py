"""Test unitari della lettura dei secret in `marketmind_common.config`.

Ogni variabile può arrivare anche da un file indicato da `<NOME>_FILE` (secret
montati in un volume Docker): il file ha precedenza sulla variabile semplice.
"""

from __future__ import annotations

import pytest

from marketmind_common.config import get_api_key, get_database_url, get_role_database_url

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


# --- URL per un ruolo applicativo specifico ------------------------------------
# La dashboard usa due ruoli diversi da `POSTGRES_USER` (`marketmind_readonly`
# per leggere, `marketmind_app` per la gestione dei portfolio): stesso host,
# porta e database, credenziali proprie.


@pytest.fixture
def role_env(clean_env):
    clean_env.delenv("TEST_ROLE_PASSWORD", raising=False)
    clean_env.delenv("TEST_ROLE_PASSWORD_FILE", raising=False)
    return clean_env


def test_role_url_uses_role_credentials_and_shared_host(role_env):
    role_env.setenv("POSTGRES_USER", "marketmind")
    role_env.setenv("POSTGRES_PASSWORD", "superuser-secret")
    role_env.setenv("POSTGRES_HOST", "db.example")
    role_env.setenv("POSTGRES_PORT", "6543")
    role_env.setenv("POSTGRES_DB", "mm")
    role_env.setenv("TEST_ROLE_PASSWORD", "role-secret")

    url = get_role_database_url("marketmind_readonly", "TEST_ROLE_PASSWORD")

    assert url == "postgresql+psycopg://marketmind_readonly:role-secret@db.example:6543/mm"


def test_role_url_password_from_file(role_env, tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("from-file\n")
    role_env.setenv("TEST_ROLE_PASSWORD_FILE", str(secret))

    url = get_role_database_url("marketmind_app", "TEST_ROLE_PASSWORD")

    assert url == "postgresql+psycopg://marketmind_app:from-file@127.0.0.1:5432/marketmind"


def test_role_url_quotes_special_characters_in_password(role_env):
    role_env.setenv("TEST_ROLE_PASSWORD", "a@b:c/d")

    url = get_role_database_url("marketmind_app", "TEST_ROLE_PASSWORD")

    assert "marketmind_app:a%40b%3Ac%2Fd@" in url


def test_role_url_is_none_without_password(role_env):
    assert get_role_database_url("marketmind_readonly", "TEST_ROLE_PASSWORD") is None
