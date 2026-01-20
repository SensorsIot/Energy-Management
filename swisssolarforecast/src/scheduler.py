"""
Scheduler for SwissSolarForecast add-on.

Runs two independent tasks:
1. Fetcher: Downloads ICON GRIB data on schedule (CH1 every 3h, CH2 every 6h)
2. Calculator: Reads GRIB files and writes forecast to InfluxDB (every 15 min)
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class ForecastScheduler:
    """
    Manages scheduled tasks for ICON data fetching and forecast calculation.

    The fetcher and calculator are decoupled:
    - Fetcher writes GRIB files to disk
    - Calculator reads GRIB files from disk
    """

    def __init__(
        self,
        data_dir: Path,
        ch1_cron: str = "30 2,5,8,11,14,17,20,23 * * *",
        ch2_cron: str = "45 2,8,14,20 * * *",
        calculator_interval_minutes: int = 15,
        timezone: str = "UTC",
        local_timezone: str = "Europe/Zurich",
    ):
        """
        Initialize scheduler.

        Args:
            data_dir: Directory for GRIB file storage
            ch1_cron: Cron expression for CH1 fetch (default: 2.5h after model runs)
            ch2_cron: Cron expression for CH2 fetch (default: 2.75h after model runs)
            calculator_interval_minutes: How often to run calculator
            timezone: Timezone for cron schedules (weather fetching, UTC)
            local_timezone: Local timezone for accuracy tracking (decision time)
        """
        self.data_dir = Path(data_dir)
        self.ch1_cron = ch1_cron
        self.ch2_cron = ch2_cron
        self.calculator_interval = calculator_interval_minutes
        self.timezone = timezone
        self.local_timezone = local_timezone

        self.scheduler = BackgroundScheduler(timezone=timezone)

        # Callbacks set by run.py
        self.fetch_ch1_callback: Optional[Callable] = None
        self.fetch_ch2_callback: Optional[Callable] = None
        self.calculate_callback: Optional[Callable] = None
        self.snapshot_callback: Optional[Callable] = None

        # Status tracking
        self.last_fetch_ch1: Optional[datetime] = None
        self.last_fetch_ch2: Optional[datetime] = None
        self.last_calculation: Optional[datetime] = None
        self.last_snapshot: Optional[datetime] = None

    def set_callbacks(
        self,
        fetch_ch1: Callable,
        fetch_ch2: Callable,
        calculate: Callable,
        snapshot: Optional[Callable] = None,
    ):
        """Set callback functions for scheduled tasks."""
        self.fetch_ch1_callback = fetch_ch1
        self.fetch_ch2_callback = fetch_ch2
        self.calculate_callback = calculate
        self.snapshot_callback = snapshot

    def _fetch_ch1_job(self):
        """Job wrapper for CH1 fetch."""
        logger.info("Scheduled CH1 fetch starting...")
        try:
            if self.fetch_ch1_callback:
                self.fetch_ch1_callback()
                self.last_fetch_ch1 = datetime.now()
                logger.info("CH1 fetch completed")
            else:
                logger.warning("No CH1 fetch callback registered")
        except Exception as e:
            logger.error(f"CH1 fetch failed: {e}", exc_info=True)

    def _fetch_ch2_job(self):
        """Job wrapper for CH2 fetch."""
        logger.info("Scheduled CH2 fetch starting...")
        try:
            if self.fetch_ch2_callback:
                self.fetch_ch2_callback()
                self.last_fetch_ch2 = datetime.now()
                logger.info("CH2 fetch completed")
            else:
                logger.warning("No CH2 fetch callback registered")
        except Exception as e:
            logger.error(f"CH2 fetch failed: {e}", exc_info=True)

    def _calculate_job(self):
        """Job wrapper for forecast calculation."""
        logger.info("Scheduled calculation starting...")
        try:
            if self.calculate_callback:
                self.calculate_callback()
                self.last_calculation = datetime.now()
                logger.info("Calculation completed")
            else:
                logger.warning("No calculate callback registered")
        except Exception as e:
            logger.error(f"Calculation failed: {e}", exc_info=True)

    def _snapshot_job(self):
        """Job wrapper for forecast snapshot (21:00 daily)."""
        logger.info("Scheduled forecast snapshot starting...")
        try:
            if self.snapshot_callback:
                self.snapshot_callback()
                self.last_snapshot = datetime.now()
                logger.info("Forecast snapshot completed")
            else:
                logger.warning("No snapshot callback registered")
        except Exception as e:
            logger.error(f"Forecast snapshot failed: {e}", exc_info=True)

    def setup_jobs(self):
        """Configure scheduled jobs."""
        # CH1 fetch job
        logger.info(f"Setting up CH1 fetch: cron={self.ch1_cron}")
        self.scheduler.add_job(
            self._fetch_ch1_job,
            CronTrigger.from_crontab(self.ch1_cron, timezone=self.timezone),
            id="fetch_ch1",
            name="Fetch ICON-CH1 data",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # CH2 fetch job
        logger.info(f"Setting up CH2 fetch: cron={self.ch2_cron}")
        self.scheduler.add_job(
            self._fetch_ch2_job,
            CronTrigger.from_crontab(self.ch2_cron, timezone=self.timezone),
            id="fetch_ch2",
            name="Fetch ICON-CH2 data",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Calculator job
        logger.info(f"Setting up calculator: every {self.calculator_interval} minutes")
        self.scheduler.add_job(
            self._calculate_job,
            IntervalTrigger(minutes=self.calculator_interval),
            id="calculate",
            name="Calculate PV forecast",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Accuracy tracking: 21:00 snapshot in LOCAL time (decision time)
        if self.snapshot_callback:
            logger.info(f"Setting up accuracy snapshot: 21:00 {self.local_timezone} daily")
            self.scheduler.add_job(
                self._snapshot_job,
                CronTrigger.from_crontab("0 21 * * *", timezone=self.local_timezone),
                id="accuracy_snapshot",
                name="Snapshot forecast for accuracy",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )

    def start(self):
        """Start the scheduler."""
        self.setup_jobs()
        self.scheduler.start()
        logger.info("Scheduler started")

        # Log next run times
        for job in self.scheduler.get_jobs():
            logger.info(f"  {job.name}: next run at {job.next_run_time}")

    def stop(self):
        """Stop the scheduler gracefully."""
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")

    def trigger_fetch_ch1(self):
        """Manually trigger CH1 fetch."""
        logger.info("Manual CH1 fetch triggered")
        self._fetch_ch1_job()

    def trigger_fetch_ch2(self):
        """Manually trigger CH2 fetch."""
        logger.info("Manual CH2 fetch triggered")
        self._fetch_ch2_job()

    def trigger_calculate(self):
        """Manually trigger calculation."""
        logger.info("Manual calculation triggered")
        self._calculate_job()

    def get_status(self) -> Dict:
        """Get scheduler status."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            })

        return {
            "running": self.scheduler.running,
            "jobs": jobs,
            "last_fetch_ch1": str(self.last_fetch_ch1) if self.last_fetch_ch1 else None,
            "last_fetch_ch2": str(self.last_fetch_ch2) if self.last_fetch_ch2 else None,
            "last_calculation": str(self.last_calculation) if self.last_calculation else None,
            "last_snapshot": str(self.last_snapshot) if self.last_snapshot else None,
        }
