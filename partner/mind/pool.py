"""Mind Pool — 全局念头池。

核心：asyncio.PriorityQueue 用于 async 环境。
扩展：thread_safe_queue (queue.PriorityQueue) 用于跨线程投递。
延迟：wake_after 支持 — 事件设置 wake_after 后不到时间不出队。
"""

import asyncio
import json
import logging
import os
import queue
import time as _time
from typing import Optional

from .event_types import MindEvent

logger = logging.getLogger(__name__)


class MindPool:
    """全局念头池单例。

    支持 wake_after 延迟唤醒：事件设置 wake_after=time.time()+900
    后，900 秒内不会被 get() 取出。到时间后自动回到主队列。
    """

    _instance: Optional['MindPool'] = None
    _lock = asyncio.Lock()

    def __init__(self, maxsize: int = 0, save_path: str = ""):
        self._queue: asyncio.PriorityQueue[MindEvent] = asyncio.PriorityQueue(maxsize=maxsize)
        self._thread_queue: queue.PriorityQueue[MindEvent] = queue.PriorityQueue(maxsize=0)
        # 等待室：{event.id: (wake_after, event)} — 不到时间的暂存这里
        self._waiting_room: dict = {}
        # 已被 scheduler 取出、但 handler 还没执行完的事件。
        # 这段窗口内事件已经不在 queue 里，也可能尚未进入 executor 的
        # _running_projects，因此必须在 pool 层参与去重。
        self._inflight: dict[str, MindEvent] = {}
        self._total_put: int = 0
        self._total_get: int = 0
        self._save_path: str = save_path
        self._auto_save: bool = bool(save_path)
        import atexit
        atexit.register(self._atexit_save)

    @classmethod
    def set_save_path(cls, path: str):
        if cls._instance:
            cls._instance._save_path = path
            cls._instance._auto_save = bool(path)

    def save(self):
        """Save current pool state to JSON file."""
        if not self._save_path:
            return
        try:
            import json as _json
            data = []
            for ev in self._queue._queue:
                data.append(self._event_to_dict(ev))
            for ev in self._inflight.values():
                data.append(self._event_to_dict(ev))
            # Important: this is only a persistence snapshot.  Do not drain
            # _thread_queue here.  QQ/bridge threads use put_threadsafe(); if
            # save() consumes that queue, the running scheduler will never see
            # the event until a process restart reloads mind_pool.json.
            with self._thread_queue.mutex:
                for ev in list(self._thread_queue.queue):
                    data.append(self._event_to_dict(ev))
            for eid, (wake_at, ev) in self._waiting_room.items():
                d = self._event_to_dict(ev)
                d["wake_after"] = wake_at
                data.append(d)
            data = self._dedupe_event_dicts(data)
            os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
            with open(self._save_path, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[MIND] Save failed: {e}")

    def _atexit_save(self):
        """Save on program exit."""
        if self._save_path and os.path.exists(os.path.dirname(self._save_path)):
            try:
                import json as _json
                data = []
                for ev in self._queue._queue:
                    data.append(self._event_to_dict(ev))
                for ev in self._inflight.values():
                    data.append(self._event_to_dict(ev))
                with self._thread_queue.mutex:
                    for ev in list(self._thread_queue.queue):
                        data.append(self._event_to_dict(ev))
                for eid, (wake_at, ev) in self._waiting_room.items():
                    d = self._event_to_dict(ev)
                    d["wake_after"] = wake_at
                    data.append(d)
                data = self._dedupe_event_dicts(data)
                with open(self._save_path, "w", encoding="utf-8") as f:
                    _json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    async def load(self) -> int:
        """Load saved pool state from JSON file. Returns restored count."""
        if not self._save_path or not os.path.exists(self._save_path):
            return 0
        try:
            import json as _json
            with open(self._save_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            data = self._dedupe_event_dicts(data if isinstance(data, list) else [])
            count = 0
            for d in data:
                event_type = str(d.get("type", "")).lower()
                if event_type == "report":
                    try:
                        from datetime import datetime, timezone
                        created = str(d.get("created_at") or "")
                        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=timezone.utc)
                        if (_time.time() - created_dt.timestamp()) > 300:
                            logger.info(f"[MIND] Drop stale persisted report event: {d.get('id', '')[:8]}")
                            continue
                    except Exception:
                        # A report without a parseable timestamp is unsafe to
                        # replay because QQ users may receive duplicate stale
                        # progress messages after restart.
                        logger.info(f"[MIND] Drop stale persisted report with invalid timestamp: {d.get('id', '')[:8]}")
                        continue
                ev = self._dict_to_event(d)
                if ev.wake_after and ev.wake_after > _time.time():
                    self._waiting_room[ev.id] = (ev.wake_after, ev)
                else:
                    await self._queue.put(ev)
                count += 1
            logger.info(f"[MIND] Loaded {count} events from {self._save_path}")
            try:
                os.remove(self._save_path)
            except Exception:
                pass
            return count
        except Exception as e:
            logger.warning(f"[MIND] Load failed: {e}")
            return 0

    @staticmethod
    def _event_to_dict(ev) -> dict:
        return {
            "id": ev.id,
            "type": ev.type.value if hasattr(ev.type, "value") else str(ev.type),
            "priority": ev.priority,
            "payload": ev.payload,
            "created_at": ev.created_at,
            "source": ev.source,
            "parent_id": ev.parent_id,
            "wake_after": ev.wake_after,
        }

    @staticmethod
    def _dict_to_event(d: dict):
        from .event_types import MindEvent, EventType
        return MindEvent(
            id=d.get("id", ""),
            type=EventType(d["type"]),
            priority=d.get("priority", 5),
            payload=d.get("payload", {}),
            created_at=d.get("created_at", ""),
            source=d.get("source", ""),
            parent_id=d.get("parent_id"),
            wake_after=d.get("wake_after"),
        )

    @staticmethod
    def _dedupe_key_from_dict(d: dict) -> tuple | None:
        if not isinstance(d, dict):
            return None
        payload = d.get("payload") or {}
        event_type = str(d.get("type", "")).lower()
        if event_type in {
            "project",
            "direct_task",
            "literature_review",
            "data_analysis",
            "evidence_audit",
            "artifact_build",
            "pdf_report",
            "project_think",
            "curiosity_explore",
            "habit_update",
            "stop_project",
        }:
            title = str(payload.get("title") or "").strip()
            if not title:
                return None
            event_kind = str(payload.get("event_kind") or "").strip()
            # PROJECT is a lifeline, not a one-shot task.  While step N is
            # inflight, the executor must be able to enqueue step N+1.  Deduping
            # only by title incorrectly blocks that continuation and leaves the
            # instance idle after a successful round.
            step = payload.get("step")
            if step is not None and str(step).strip() != "":
                try:
                    step_key = int(step)
                except Exception:
                    step_key = str(step).strip()
                return (event_type, title, event_kind, step_key)
            return (event_type, title, event_kind)
        if event_type == "content_digest":
            content_id = str(payload.get("content_id") or "").strip()
            if content_id:
                return ("content_digest", content_id)
            # Without a stable content_id we cannot safely dedupe by project:
            # rapid multi-message shares often belong to the same project but
            # must be digested independently.
            return None
        return None

    @classmethod
    def _dedupe_event_dicts(cls, rows: list[dict]) -> list[dict]:
        """Keep duplicate work out of persisted queues.

        PROJECT events are deduped by title + step when step is available so a
        running project can persist a valid next-step continuation.
        """
        best_by_key = {}
        out = []
        for row in rows or []:
            key = cls._dedupe_key_from_dict(row)
            if not key:
                out.append(row)
                continue
            current = best_by_key.get(key)
            if current is None:
                best_by_key[key] = row
                continue
            cur_wake = current.get("wake_after") or 0
            row_wake = row.get("wake_after") or 0
            if row_wake and (not cur_wake or row_wake < cur_wake):
                best_by_key[key] = row
        out.extend(best_by_key.values())
        return out

    def _has_duplicate_event(self, event: MindEvent) -> bool:
        d = self._event_to_dict(event)
        key = self._dedupe_key_from_dict(d)
        if not key:
            return False
        for ev in list(getattr(self._queue, "_queue", [])):
            if self._dedupe_key_from_dict(self._event_to_dict(ev)) == key:
                return True
        with self._thread_queue.mutex:
            for ev in list(self._thread_queue.queue):
                if self._dedupe_key_from_dict(self._event_to_dict(ev)) == key:
                    return True
        for ev in self._inflight.values():
            if self._dedupe_key_from_dict(self._event_to_dict(ev)) == key:
                return True
        for _, (_, ev) in self._waiting_room.items():
            if self._dedupe_key_from_dict(self._event_to_dict(ev)) == key:
                return True
        return False

    def mark_inflight(self, event: MindEvent):
        self._inflight[event.id] = event
        if self._auto_save:
            self.save()

    def unmark_inflight(self, event_id: str):
        if event_id in self._inflight:
            self._inflight.pop(event_id, None)
            if self._auto_save:
                self.save()

    @classmethod
    async def get_instance(cls) -> 'MindPool':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def get_sync_instance(cls) -> 'MindPool':
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    async def put(self, event: MindEvent):
        """放入一个念头。如设置了 wake_after 则进入等待室。"""
        if self._has_duplicate_event(event):
            logger.info(f"[MIND] SKIP duplicate event type={event.type.value} payload={event.payload}")
            return
        if event.wake_after and event.wake_after > _time.time():
            self._waiting_room[event.id] = (event.wake_after, event)
            logger.info(f"[MIND] PUT event_type={event.type.value}, id={event.id[:8]}, "
                        f"pri={event.priority} [delayed until {event.wake_after:.0f}]")
        else:
            await self._queue.put(event)
            logger.info(f"[MIND] PUT event_type={event.type.value}, id={event.id[:8]}, "
                        f"pri={event.priority}")
        self._total_put += 1
        if self._auto_save:
            self.save()

    def put_threadsafe(self, event: MindEvent):
        if self._has_duplicate_event(event):
            logger.info(f"[MIND] SKIP duplicate event type={event.type.value} payload={event.payload} [threadsafe]")
            return
        self._thread_queue.put(event)
        self._total_put += 1
        logger.info(f"[MIND] PUT(event_type={event.type.value}, id={event.id[:8]}, "
                    f"pri={event.priority})  [threadsafe]")
        if self._auto_save:
            self.save()

    def drop_project_events_except(self, keep_title: str) -> int:
        """Drop pending PROJECT events for other titles after a user switches focus."""
        keep = (keep_title or "").strip()
        removed = 0

        def should_drop(ev: MindEvent) -> bool:
            event_type = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
            if event_type != "project":
                return False
            title = str((ev.payload or {}).get("title") or "").strip()
            return bool(title and keep and title != keep)

        kept = []
        while True:
            try:
                ev = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if should_drop(ev):
                removed += 1
            else:
                kept.append(ev)
        for ev in kept:
            self._queue.put_nowait(ev)

        kept_thread = []
        while True:
            try:
                ev = self._thread_queue.get_nowait()
            except queue.Empty:
                break
            if should_drop(ev):
                removed += 1
            else:
                kept_thread.append(ev)
        for ev in kept_thread:
            self._thread_queue.put(ev)

        for eid, (_, ev) in list(self._waiting_room.items()):
            if should_drop(ev):
                self._waiting_room.pop(eid, None)
                removed += 1

        if removed:
            logger.info(f"[MIND] Dropped {removed} stale project event(s), keep={keep}")
            if self._auto_save:
                self.save()
        return removed

    async def _drain_thread_queue(self):
        while True:
            try:
                ev = self._thread_queue.get_nowait()
                await self._queue.put(ev)
            except queue.Empty:
                break

    async def _release_waiting(self):
        """将等待室中到时间的事件放回主队列。"""
        now = _time.time()
        ready = [eid for eid, (wake_at, ev) in self._waiting_room.items() if wake_at <= now]
        for eid in ready:
            _, ev = self._waiting_room.pop(eid)
            await self._queue.put(ev)
            logger.info(f"[MIND] Released from waiting room: {ev.type.value}, "
                        f"id={ev.id[:8]}")
        return len(ready)

    async def get(self) -> Optional[MindEvent]:
        """取出优先级最高的、已到唤醒时间的念头。非阻塞，用 get_nowait 避免 TimerHandle 泄漏。"""
        await self._drain_thread_queue()
        await self._release_waiting()

        # 用 try_get_nowait 循环处理 wake_after 事件，避免递归
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None

            # 如果事件设置了 wake_after 但还没到，放回等待室
            if event.wake_after and event.wake_after > _time.time():
                self._waiting_room[event.id] = (event.wake_after, event)
                logger.debug(f"[MIND] Event {event.id[:8]} not yet due "
                             f"(wake_after={event.wake_after:.0f}), back to waiting room")
                continue

            self._total_get += 1
            logger.info(f"[MIND] START event_type={event.type.value}, id={event.id[:8]}, "
                        f"pri={event.priority}, topic={event.payload.get('topic', '')}")
            return event

    def qsize(self) -> int:
        return self._queue.qsize() + self._thread_queue.qsize() + len(self._waiting_room)

    def stats(self) -> dict:
        return {
            "pool_size": self.qsize(),
            "async_queue_size": self._queue.qsize(),
            "thread_queue_size": self._thread_queue.qsize(),
            "waiting_room_size": len(self._waiting_room),
            "inflight_size": len(self._inflight),
            "total_put": self._total_put,
            "total_get": self._total_get,
        }
