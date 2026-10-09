import time

from django.conf import settings
from django.core.management.base import BaseCommand

from intake.services.fetch import HostThrottle
from intake.services.worker import run_once

IDLE_SLEEP_SECONDS = 1


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
                if run_once(throttle):
                    processed += 1
                    continue
                if once:
                    break
                time.sleep(IDLE_SLEEP_SECONDS)
        except KeyboardInterrupt:
            self.stdout.write("Stopping intake worker.")
        self.stdout.write(f"Processed {processed} job(s).")
