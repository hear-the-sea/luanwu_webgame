"""Recover idle Redis consumers whose main event loop stops making progress.

The supervisor runs outside Celery's event loop. It deliberately declines to
restart a worker with running/reserved work or an unreachable broker.
"""

from __future__ import annotations

import logging
import os
import signal
from dataclasses import dataclass
from threading import Event, Thread
from time import monotonic

from celery import bootsteps
from celery.signals import task_received
from celery.worker import state
from redis import Redis

logger = logging.getLogger(__name__)


@dataclass
class StallDetector:
    started_at: float
    last_progress: float
    startup_grace: float = 120
    stall_seconds: float = 600
    failure_limit: int = 3
    failures: int = 0

    def observe(self, now: float, backlog: int | None, busy: bool) -> bool:
        """Require consecutive trustworthy idle/backlogged observations."""
        if backlog is None or backlog == 0 or busy:
            self.failures = 0
            # Start a fresh grace window after outages or legitimate work.
            self.last_progress = now
            return False
        if now - self.started_at < self.startup_grace or now - self.last_progress < self.stall_seconds:
            self.failures = 0
            return False
        self.failures += 1
        return self.failures >= self.failure_limit


class RedisConsumerSupervisor(bootsteps.StartStopStep):
    """Watch actual task receipt and queue depth using an independent connection."""

    requires = ("celery.worker.consumer.tasks:Tasks",)

    def __init__(self, consumer, **kwargs):
        super().__init__(consumer, **kwargs)
        self.enabled = bool(consumer.app.conf.get("worker_stall_watchdog_enabled", True))
        self.stopped = Event()
        self.thread = None
        self.client = None
        self.detector = None
        self.consumer = consumer

    def start(self, consumer):
        if not self.enabled or consumer.connection.transport.driver_type != "redis":
            return
        conf = consumer.app.conf
        channel = consumer.task_consumer.channel
        allowed = conf.get("worker_stall_watchdog_queues") or (
            conf.get("timer_queue", "timer"),
            conf.get("timer_scan_queue", "timer_scan"),
            conf.get("timer_maintenance_queue", "timer_maintenance"),
            conf.get("timer_safety_queue", "timer_safety"),
        )
        queue_names = {queue.name for queue in consumer.task_consumer.queues}
        # Protect dedicated timer workers only. A mixed worker may legitimately
        # spend a long time doing another queue's work.
        if not queue_names or not queue_names.issubset(set(allowed)):
            return
        prefix = conf.broker_transport_options.get("global_keyprefix", "")
        self.keys = tuple(
            prefix + channel._q_for_pri(queue.name, priority)
            for queue in consumer.task_consumer.queues
            for priority in channel.priority_steps
        )
        if not self.keys:
            return
        self.stopped.clear()
        now = monotonic()
        self.detector = StallDetector(
            now,
            now,
            startup_grace=float(conf.get("worker_stall_watchdog_startup_grace", 120)),
            stall_seconds=float(conf.get("worker_stall_watchdog_timeout", 600)),
            failure_limit=max(1, int(conf.get("worker_stall_watchdog_failures", 3))),
        )
        self.interval = max(1, float(conf.get("worker_stall_watchdog_interval", 30)))
        self.shutdown_grace = max(1, float(conf.get("worker_stall_watchdog_shutdown_grace", 60)))
        # Do not share the consumer's connection/pool: its unread socket may be
        # precisely the fault being detected. Bound every broker operation.
        self.client = Redis.from_url(
            conf.broker_url, socket_connect_timeout=2, socket_timeout=2, retry_on_timeout=False
        )
        task_received.connect(self._received, weak=False)
        self.thread = Thread(target=self._run, name="redis-consumer-supervisor", daemon=True)
        self.thread.start()
        logger.info("Redis consumer supervision enabled for %s", [q.name for q in consumer.task_consumer.queues])

    def _received(self, **kwargs):
        self.detector.last_progress = monotonic()
        self.detector.failures = 0

    def _busy(self):
        # These collections live in the parent, including early-ack tasks.
        # A slow task must never be treated as a stalled idle consumer.
        if state.active_requests:
            return True
        pool = getattr(self.consumer, "pool", None)
        pool_cache = getattr(getattr(pool, "_pool", None), "_cache", None)
        if pool_cache is not None:
            # Prefork tracks jobs here until their result arrives, including
            # work sent to a child but not accepted yet. An empty pool cache
            # proves that reserved-only records do not represent running work.
            return bool(pool_cache)
        return bool(state.reserved_requests)

    def _backlog(self):
        with self.client.pipeline(transaction=False) as pipe:
            for key in self.keys:
                pipe.llen(key)
            return sum(pipe.execute())

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                backlog = self._backlog()
            except Exception:
                # A storage/network outage is not evidence that this worker's
                # consumer alone is broken. Never create a restart storm.
                self.detector.observe(monotonic(), None, self._busy())
                logger.warning("Redis consumer supervision cannot reach broker", exc_info=True)
                continue
            if self.detector.observe(monotonic(), backlog, self._busy()):
                self._recover(backlog)
                return

    def _recover(self, backlog):
        logger.critical(
            "Idle Redis consumer stalled for %.0fs with %d queued tasks; requesting worker restart",
            monotonic() - self.detector.last_progress,
            backlog,
        )
        os.kill(os.getpid(), signal.SIGTERM)
        # SIGTERM normally lets Celery restore unacknowledged messages and
        # shut its pool down. A broken event loop may never process shutdown.
        if self.stopped.wait(self.shutdown_grace):
            return
        try:
            backlog = self._backlog()
        except Exception:
            return
        if not self._busy() and backlog > 0:
            logger.critical("Worker did not finish graceful shutdown; exiting idle main process")
            # No executing/reserved tasks are killed; Redis retains queued and
            # unacknowledged messages. Container restart policy replaces PID 1.
            os._exit(1)

    def stop(self, consumer):
        self.stopped.set()
        task_received.disconnect(self._received)
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.client is not None:
            self.client.close()

    shutdown = stop


def register_worker_supervision(app):
    app.steps["consumer"].add(RedisConsumerSupervisor)
