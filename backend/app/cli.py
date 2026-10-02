"""Operational commands. Usage: ``uv run python -m app.cli --help``."""

import getpass
from decimal import Decimal

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import AdminRole, AdminUser, CreditPackage, Plan
from app.services.audit import record_audit
from app.services.auth import normalize_email
from app.services.entitlements import validate_entitlements
from app.services.permissions import DEFAULT_ROLES
from app.services.subscriptions import ensure_default_plan

cli = typer.Typer(no_args_is_help=True)


def seed_roles(db: Session) -> dict[str, AdminRole]:
    roles = {r.name: r for r in db.scalars(select(AdminRole))}
    for name, (description, permissions) in DEFAULT_ROLES.items():
        if name not in roles:
            roles[name] = AdminRole(name=name, description=description, permissions=permissions)
            db.add(roles[name])
    db.flush()
    return roles


@cli.command("seed-roles")
def seed_roles_command() -> None:
    """Create the default admin roles if they are missing."""
    with SessionLocal() as db:
        seed_roles(db)
        db.commit()
    typer.echo("Default admin roles are present.")


# Starter catalogue. Prices are placeholders: adjust them in the admin console.
DEFAULT_PLANS = [
    {
        "code": "starter",
        "name": "Starter",
        "sort_order": 10,
        "monthly_price": Decimal("19.00"),
        "annual_price": Decimal("190.00"),
        "included_credits": 100,
        "description": "For freelancers and small businesses.",
        "features": ["100 invoices per month", "Bulk upload up to 25 files", "PDF summaries"],
        "entitlements": {
            "max_file_size_mb": 15,
            "max_files_per_batch": 25,
            "export_formats": ["csv", "xlsx", "json", "pdf"],
            "history_retention_days": 365,
            "max_team_members": 3,
            "credit_purchases": True,
        },
    },
    {
        "code": "professional",
        "name": "Professional",
        "sort_order": 20,
        "monthly_price": Decimal("49.00"),
        "annual_price": Decimal("490.00"),
        "included_credits": 500,
        "description": "For accountants and growing finance teams.",
        "features": [
            "500 invoices per month",
            "Accounting software exports",
            "API & webhooks",
            "Scheduled exports",
            "Item directory",
        ],
        "entitlements": {
            "max_file_size_mb": 25,
            "max_files_per_batch": 200,
            "zip_upload": True,
            "export_formats": [
                "csv",
                "xlsx",
                "json",
                "pdf",
                "tally",
                "quickbooks",
                "zoho_books",
                "xero",
            ],
            "history_retention_days": None,
            "max_team_members": 10,
            "credit_purchases": True,
            "api_access": True,
            "webhooks": True,
            "max_webhook_endpoints": 5,
            "scheduled_exports": True,
            "advanced_reports": True,
            "item_directory": True,
        },
    },
    {
        "code": "business",
        "name": "Business",
        "sort_order": 30,
        "monthly_price": Decimal("149.00"),
        "annual_price": Decimal("1490.00"),
        "included_credits": 2000,
        "description": "High-volume processing with overage and custom rules.",
        "features": [
            "2,000 invoices per month",
            "Overage billing",
            "Custom duplicate rules",
            "Everything in Professional",
        ],
        "entitlements": {
            "max_file_size_mb": 50,
            "max_files_per_batch": 1000,
            "zip_upload": True,
            "export_formats": [
                "csv",
                "xlsx",
                "json",
                "pdf",
                "tally",
                "quickbooks",
                "zoho_books",
                "xero",
            ],
            "history_retention_days": None,
            "max_team_members": 50,
            "credit_purchases": True,
            "overage_allowed": True,
            "api_access": True,
            "webhooks": True,
            "max_webhook_endpoints": 20,
            "scheduled_exports": True,
            "advanced_reports": True,
            "item_directory": True,
            "duplicate_rule_overrides": True,
        },
    },
]

DEFAULT_PACKAGES = [
    {"name": "50 credits", "credits": 50, "price": Decimal("10.00"), "sort_order": 10},
    {"name": "200 credits", "credits": 200, "price": Decimal("35.00"), "sort_order": 20},
    {"name": "1,000 credits", "credits": 1000, "price": Decimal("150.00"), "sort_order": 30},
]


@cli.command("seed-plans")
def seed_plans() -> None:
    """Create the default plan catalogue and credit packages if they are missing."""
    with SessionLocal() as db:
        ensure_default_plan(db)
        existing = set(db.scalars(select(Plan.code)))
        for spec in DEFAULT_PLANS:
            if spec["code"] not in existing:
                db.add(
                    Plan(**{**spec, "entitlements": validate_entitlements(spec["entitlements"])})
                )
        if not db.scalar(select(CreditPackage.id).limit(1)):
            db.add_all(CreditPackage(**p) for p in DEFAULT_PACKAGES)
        db.commit()
    typer.echo("Default plans and credit packages are present.")


@cli.command("seed-ai")
def seed_ai(
    gemini_model: str = typer.Option("gemini-2.5-flash", help="Gemini model to configure"),
) -> None:
    """Create AI providers and the default prompt. If IF_GEMINI_API_KEY is set, also store it
    (encrypted) and configure it as the primary model."""
    import os

    from app.core.crypto import encrypt_secret, secret_hint
    from app.models import ModelConfiguration, ModelProvider, ProviderCredential
    from app.services.ai.prompts import active_prompt

    with SessionLocal() as db:
        providers = {p.code: p for p in db.scalars(select(ModelProvider))}
        for code, name, adapter in (
            ("gemini", "Google Gemini", "gemini"),
            ("fake", "Fake (testing)", "fake"),
        ):
            if code not in providers:
                providers[code] = ModelProvider(
                    code=code, name=name, adapter=adapter, is_active=code != "fake"
                )
                db.add(providers[code])
        db.flush()
        active_prompt(db)
        key = os.environ.get("IF_GEMINI_API_KEY")
        if key and not db.scalar(
            select(ModelConfiguration.id).where(
                ModelConfiguration.provider_id == providers["gemini"].id
            )
        ):
            cred = ProviderCredential(
                provider_id=providers["gemini"].id,
                label="Default",
                encrypted_secret=encrypt_secret(key),
                secret_hint=secret_hint(key),
            )
            db.add(cred)
            db.flush()
            db.add(
                ModelConfiguration(
                    provider_id=providers["gemini"].id,
                    credential_id=cred.id,
                    model_name=gemini_model,
                    display_name=gemini_model,
                    priority=0,
                    is_active=True,
                )
            )
            typer.echo(f"Configured {gemini_model} as the primary model.")
        db.commit()
    typer.echo("AI providers and default prompt are present.")


@cli.command("generate-encryption-key")
def generate_encryption_key() -> None:
    """Print a new key for IF_ENCRYPTION_KEYS."""
    from app.core.crypto import generate_key

    typer.echo(generate_key())


@cli.command("create-admin")
def create_admin(
    email: str = typer.Option(...),
    full_name: str = typer.Option(...),
    role: str = typer.Option("super_admin"),
) -> None:
    """Create an admin user (prompts for the password)."""
    password = getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        raise typer.BadParameter("Password must be at least 12 characters.")
    if password != getpass.getpass("Confirm password: "):
        raise typer.BadParameter("Passwords do not match.")
    with SessionLocal() as db:
        roles = seed_roles(db)
        if role not in roles:
            raise typer.BadParameter(f"Unknown role {role!r}.")
        email = normalize_email(email)
        if db.scalar(select(AdminUser.id).where(AdminUser.email == email)):
            raise typer.BadParameter("An admin with this email already exists.")
        admin = AdminUser(
            email=email,
            full_name=full_name,
            password_hash=hash_password(password),
            role_id=roles[role].id,
        )
        db.add(admin)
        db.flush()
        record_audit(
            db,
            action="admin_user.created",
            actor_admin_id=None,
            entity_type="admin_user",
            entity_id=admin.id,
            summary={"email": email, "role": role, "via": "cli"},
        )
        db.commit()
    typer.echo(f"Admin {email} created with role {role}.")


if __name__ == "__main__":
    cli()
