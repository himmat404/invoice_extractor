from typing import Any

from sqlalchemy.orm import Session

from app.models import SystemSetting

#: Defaults used when an administrator has not set a value.
DEFAULTS: dict[str, Any] = {
    "billing.grace_period_days": 3,
}


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(SystemSetting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, default)
