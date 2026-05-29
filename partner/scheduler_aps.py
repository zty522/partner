"""
APScheduler-based scheduling for Partner instances.
Replaces external crontab with in-process Python scheduling.

Each instance runs its own scheduler instance with:
- Self-check at configurable time (default 04:00 daily)
- Diary summary at configurable time (default 23:00 daily)  
- Clock tick every 15 minutes (or configured interval)
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Callable, Dict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class InstanceScheduler:
    """APScheduler-based scheduler for a single Partner instance."""

    def __init__(self, instance_id: str, workspace: str, config: dict = None):
        self.instance_id = instance_id
        self.workspace = workspace
        self.config = config or {}
        self._scheduler: Optional[BackgroundScheduler] = None
        self._running = False
        
        # Callbacks (set by the instance at init time)
        self.on_self_check: Optional[Callable] = None
        self.on_diary: Optional[Callable] = None
        self.on_clock_tick: Optional[Callable] = None

    def start(self):
        """Start the scheduler with configured jobs."""
        if self._running:
            return
        self._scheduler = BackgroundScheduler()
        
        # 1. Self-check (default 04:00 daily) - health check + restart
        check_time = self.config.get('self_check_time', '04:00')
        hour, minute = check_time.split(':')
        self._scheduler.add_job(
            self._run_self_check,
            CronTrigger(hour=int(hour), minute=int(minute), timezone=timezone.utc),
            id=f'{self.instance_id}_self_check',
            replace_existing=True,
            name=f'{self.instance_id} daily self-check'
        )
        
        # 2. Diary summary (default 23:00 daily)
        diary_time = self.config.get('diary_time', '23:00')
        hour, minute = diary_time.split(':')
        self._scheduler.add_job(
            self._run_diary,
            CronTrigger(hour=int(hour), minute=int(minute), timezone=timezone.utc),
            id=f'{self.instance_id}_diary',
            replace_existing=True,
            name=f'{self.instance_id} daily diary'
        )
        
        # 3. Clock tick (default every 15 minutes)
        tick_interval = self.config.get('clock_tick_interval', 900)
        self._scheduler.add_job(
            self._run_clock_tick,
            IntervalTrigger(seconds=tick_interval),
            id=f'{self.instance_id}_clock_tick',
            replace_existing=True,
            name=f'{self.instance_id} clock tick'
        )
        
        self._scheduler.start()
        self._running = True
        logger.info(f"[{self.instance_id}] Scheduler started (self_check={check_time}, diary={diary_time}, tick={tick_interval}s)")

    def stop(self):
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info(f"[{self.instance_id}] Scheduler stopped")

    def is_running(self) -> bool:
        return self._running and self._scheduler is not None and self._scheduler.running

    def add_custom_job(self, job_id: str, trigger, func: Callable, **kwargs):
        """Allow user to add custom scheduled jobs at runtime."""
        if self._scheduler:
            self._scheduler.add_job(func, trigger, id=job_id, replace_existing=True, **kwargs)
            logger.info(f"[{self.instance_id}] Custom job added: {job_id}")

    def remove_job(self, job_id: str):
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
                logger.info(f"[{self.instance_id}] Job removed: {job_id}")
            except Exception:
                pass

    def _run_self_check(self):
        if self.on_self_check:
            logger.info(f"[{self.instance_id}] Running self-check...")
            try:
                self.on_self_check()
            except Exception as e:
                logger.error(f"[{self.instance_id}] Self-check failed: {e}")

    def _run_diary(self):
        if self.on_diary:
            logger.info(f"[{self.instance_id}] Generating daily diary...")
            try:
                self.on_diary()
            except Exception as e:
                logger.error(f"[{self.instance_id}] Diary generation failed: {e}")

    def _run_clock_tick(self):
        if self.on_clock_tick:
            try:
                self.on_clock_tick()
            except Exception as e:
                logger.debug(f"[{self.instance_id}] Clock tick error: {e}")
