"""Processing worker. Run with: ``uv run python -m app.worker``."""

import logging
import os
import signal
import socket
import time

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services import jobs

logger = logging.getLogger("invoiceflow.worker")
_stop = False


def _handle_stop(*_):
    global _stop
    _stop = True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    logger.info("Worker %s started (handler=%s)", worker_id, settings.extraction_handler)
    last_reap = 0.0
    while not _stop:
        with SessionLocal() as db:
            if time.monotonic() - last_reap > 30:
                if recovered := jobs.requeue_expired(db):
                    logger.warning("Recovered %d expired jobs", recovered)
                last_reap = time.monotonic()
            try:
                worked = jobs.work_once(db, worker_id)
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("Worker loop error")
                db.rollback()
                worked = False
        if not worked:
            time.sleep(settings.worker_poll_seconds)
    logger.info("Worker %s stopped", worker_id)


if __name__ == "__main__":
    main()
