"""Admin permission catalogue and default roles (§5.1)."""

ALL = "*"

PERMISSIONS: dict[str, str] = {
    "users.read": "View customer users and workspaces",
    "users.write": "Suspend, reactivate and edit customer accounts",
    "plans.write": "Create and edit plans, pricing and entitlements",
    "credits.write": "Grant or revoke customer credits",
    "billing.read": "View subscriptions, payments and billing invoices",
    "billing.write": "Refunds, adjustments and subscription overrides",
    "coupons.write": "Manage coupons and promotions",
    "ai.read": "View AI providers, models and usage",
    "ai.manage": "Configure AI providers, models, fallbacks and prompts",
    "credentials.manage": "Create, rotate and delete provider/payment credentials",
    "costs.read": "View AI cost, revenue and margin reports",
    "jobs.read": "View processing jobs",
    "jobs.manage": "Retry or cancel processing jobs",
    "exports.manage": "Manage export formats and templates",
    "settings.read": "View system settings",
    "settings.write": "Change system settings and feature flags",
    "audit.read": "View admin audit logs",
    "admins.manage": "Manage admin users and roles",
    "support.read": "View support cases and safe customer summaries",
    "support.access": "Request time-limited access to customer data",
    "status.manage": "Publish status incidents",
}

DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    "super_admin": ("Full access to every admin capability", [ALL]),
    "operations": (
        "Processing, AI configuration and system operations",
        [
            "users.read",
            "ai.read",
            "ai.manage",
            "jobs.read",
            "jobs.manage",
            "exports.manage",
            "settings.read",
            "audit.read",
            "status.manage",
        ],
    ),
    "finance": (
        "Plans, billing, credits and cost reporting",
        [
            "users.read",
            "plans.write",
            "credits.write",
            "billing.read",
            "billing.write",
            "coupons.write",
            "costs.read",
            "audit.read",
        ],
    ),
    "support": (
        "Customer support with audited, limited access",
        ["users.read", "jobs.read", "billing.read", "support.read", "support.access"],
    ),
}


def has_permission(granted: list[str], required: str) -> bool:
    return ALL in granted or required in granted
