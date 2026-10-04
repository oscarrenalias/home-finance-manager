"""Background job worker — polls for pending jobs and executes them."""

import logging
import signal
import time

logger = logging.getLogger(__name__)

_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info("Shutdown signal received, stopping worker.")
    _running = False


def run():
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    logger.info("Worker started. Waiting for jobs.")
    while _running:
        time.sleep(5)
    logger.info("Worker stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
