import threading
import time
from collections import defaultdict

from app.core.config import get_settings
from app.core.errors import RateLimitedError


class _MemoryWindow:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))

    def hit(self, key: str, window: int) -> int:
        now = time.monotonic()
        with self._lock:
            count, started = self._hits[key]
            if now - started >= window:
                count, started = 0, now
            count += 1
            self._hits[key] = (count, started)
            return count

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_memory = _MemoryWindow()
_redis_client = None


def _redis():
    global _redis_client
    url = get_settings().redis_url
    if not url:
        return None
    if _redis_client is None:
        import redis

        _redis_client = redis.Redis.from_url(url, socket_timeout=0.5)
    return _redis_client


def check_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window limiter. Uses Redis when configured, otherwise process memory."""
    if not get_settings().rate_limit_enabled:
        return
    full_key = f"rl:{key}"
    client = _redis()
    count: int
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incr(full_key)
            pipe.expire(full_key, window_seconds, nx=True)
            count = int(pipe.execute()[0])
        except Exception:  # noqa: BLE001 - degrade to in-process limiting if Redis is down
            count = _memory.hit(full_key, window_seconds)
    else:
        count = _memory.hit(full_key, window_seconds)
    if count > limit:
        raise RateLimitedError("Too many attempts. Please wait a moment and try again.")


def reset_memory_limits() -> None:
    _memory.reset()
