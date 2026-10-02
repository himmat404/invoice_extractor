"""Operational commands. Usage: ``uv run python -m app.cli --help``."""

import getpass

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import AdminRole, AdminUser
from app.services.audit import record_audit
from app.services.auth import normalize_email
from app.services.permissions import DEFAULT_ROLES

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
