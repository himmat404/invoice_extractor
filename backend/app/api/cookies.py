from fastapi import Response

from app.core.config import get_settings


def set_session_cookie(response: Response, name: str, token: str, max_age: int, path: str) -> None:
    response.set_cookie(
        name,
        token,
        max_age=max_age,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path=path,
    )


def clear_session_cookie(response: Response, name: str, path: str) -> None:
    response.delete_cookie(name, path=path, httponly=True, samesite="lax")
