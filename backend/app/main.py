import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.admin import auth as admin_auth
from app.api.admin import customers as admin_customers
from app.api.admin import plans as admin_plans
from app.api.admin import staff as admin_staff
from app.api.admin import system as admin_system
from app.api.admin import workspaces as admin_workspaces
from app.api.customer import account, auth, billing
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import register_error_handlers
from app.core.middleware import RequestIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", settings.csrf_header_name, "X-Workspace-Id", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    customer = APIRouter(prefix="/api/v1")
    customer.include_router(auth.router)
    customer.include_router(account.router)
    customer.include_router(billing.public_router)
    customer.include_router(billing.router)
    app.include_router(customer)

    admin = APIRouter(prefix="/api/admin")
    admin.include_router(admin_auth.router)
    admin.include_router(admin_customers.router)
    admin.include_router(admin_staff.router)
    admin.include_router(admin_system.router)
    admin.include_router(admin_plans.router)
    admin.include_router(admin_workspaces.router)
    app.include_router(admin)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
