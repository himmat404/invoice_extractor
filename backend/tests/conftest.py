import os

os.environ.setdefault(
    "IF_DATABASE_URL",
    "postgresql+psycopg://invoiceflow:invoiceflow@localhost:5432/invoiceflow_test",
)
os.environ["IF_ENVIRONMENT"] = "test"
os.environ["IF_EMAIL_DRIVER"] = "memory"
os.environ["IF_REDIS_URL"] = ""

import re  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.ratelimit import reset_memory_limits  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminUser, Base  # noqa: E402
from app.services.email import memory_outbox  # noqa: E402

CSRF = {"X-Requested-With": "InvoiceFlow"}
PASSWORD = "correct horse battery"


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    memory_outbox().clear()
    reset_memory_limits()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app, headers=CSRF) as c:
        yield c


def make_client() -> TestClient:
    return TestClient(app, headers=CSRF)


def register(client: TestClient, email: str = "ada@example.com", **extra) -> dict:
    payload = {"email": email, "password": PASSWORD, "full_name": "Ada Lovelace", **extra}
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def last_token(kind: str) -> str:
    """Extract the token from the newest email whose link contains ``kind``."""
    for message in reversed(memory_outbox()):
        match = re.search(rf"/{kind}\?token=([\w-]+)", message.text)
        if match:
            return match.group(1)
    raise AssertionError(f"No {kind} email found")


def create_admin(db, role_name: str = "super_admin", email: str = "root@invoiceflow.example.com"):
    from app.cli import seed_roles

    roles = seed_roles(db)
    admin = AdminUser(
        email=email,
        full_name="Root Admin",
        password_hash=hash_password(PASSWORD),
        role_id=roles[role_name].id,
    )
    db.add(admin)
    db.commit()
    return admin


def admin_login(client: TestClient, email: str = "root@invoiceflow.example.com") -> None:
    resp = client.post("/api/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
