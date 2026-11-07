import os
import tempfile
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

# Must run before any `cqa` import: isolated Postgres schema, mock LLM, offline data.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://cqa:cqa@localhost:5432/cqa")
_schemas: list[str] = []


def new_database_url() -> str:
    """A fresh empty schema on the test server; DATABASE_URL pointing at it. Dropped when the session ends."""
    schema = f"cqa_test_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as con:
            con.execute(f"CREATE SCHEMA {schema}")
    except psycopg.OperationalError as exc:
        raise SystemExit(f"tests need PostgreSQL at TEST_DATABASE_URL={TEST_DATABASE_URL} (see README): {exc}")
    _schemas.append(schema)
    return make_conninfo(TEST_DATABASE_URL, options=f"-csearch_path={schema}")


_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = new_database_url()
os.environ["CQA_LOG_DIR"] = os.path.join(_tmp, "logs")
os.environ["LLM_PROVIDER"] = "mock"
os.environ["DATASET_API_URL"] = ""


@pytest.fixture(scope="session")
def fresh_database_url():
    return new_database_url


def pytest_sessionfinish(session, exitstatus):
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as con:
        for schema in _schemas:
            con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
