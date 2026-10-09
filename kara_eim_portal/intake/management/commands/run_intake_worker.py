import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from intake.services.fetch import HostThrottle
from intake.services.worker import run_once

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 1
ERROR_SLEEP_SECONDS = 5


class Command(BaseCommand):
    help = "Process queued intake documents. Runs until interrupted unless --once is given."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Process every job that is ready now, then exit."
        )

    def handle(self, *args, once=False, **options):
        throttle = HostThrottle(settings.INTAKE_HOST_DELAY)
        processed = 0
        try:
            while True:
                try:
                    if run_once(throttle):
                        processed += 1
                        continue
                    if once:
                        break
                    time.sleep(IDLE_SLEEP_SECONDS)
                except Exception:  # noqa: BLE001 - a DB outage must not kill the worker
                    # e.g. tables not migrated yet, "database is locked", Postgres restart.
                    logger.exception("Intake worker loop failed; retrying.")
                    close_old_connections()
                    if once:
                        break
                    time.sleep(ERROR_SLEEP_SECONDS)
        except KeyboardInterrupt:
            self.stdout.write("Stopping intake worker.")
        self.stdout.write(f"Processed {processed} job(s).")
