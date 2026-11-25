"""Idle-debounced, serial scheduling for one Painter project at a time."""

from __future__ import annotations

from dataclasses import dataclass, field

WHOLE = None
Scope = dict[str, set[tuple[int, int]] | None]


@dataclass
class Batch:
    number: int
    generation: int
    project_id: str | None
    scope: Scope
    full: bool = False
    trigger: str = "auto"
    auto_generation: int = 0


@dataclass
class Scheduler:
    debounce: float = 1.0
    enabled: bool = False
    generation: int = 0  # Project lifetime, shared by automatic and manual batches.
    project_id: str | None = None
    paused_reason: str | None = None
    requires_full_recovery: bool = False
    auto_generation: int = field(default=0, init=False)
    _pending: Scope = field(default_factory=dict, init=False)
    _last_change: float | None = field(default=None, init=False)
    _running: Batch | None = field(default=None, init=False)
    _full_requested: bool = field(default=False, init=False)
    _immediate_requested: bool = field(default=False, init=False)
    _counter: int = field(default=0, init=False)

    def record(self, texture_set: str, tile: tuple[int, int] | None, now: float) -> bool:
        if self.project_id is None:
            return False
        self._merge({texture_set: WHOLE if tile is None else {tuple(tile)}})
        self._last_change = now
        return True

    def _merge(self, scope: Scope) -> None:
        for name, tiles in scope.items():
            if tiles is WHOLE:
                self._pending[name] = WHOLE
            elif self._pending.get(name, set()) is not WHOLE:
                self._pending.setdefault(name, set()).update(tiles)

    def set_enabled(self, enabled: bool) -> bool:
        if enabled == self.enabled:
            return False
        if not enabled:
            self.auto_generation += 1
            self._pending.clear()
            self._last_change = None
            self._full_requested = self._immediate_requested = False
        self.enabled = enabled
        return True

    def invalidate_project(self, new_project_id: str | None = None) -> None:
        self.generation += 1
        self._pending.clear()
        self._full_requested = self._immediate_requested = False
        self._last_change = None
        self.paused_reason = None
        self.requires_full_recovery = False
        self.project_id = new_project_id

    def set_project(self, project_id: str | None) -> bool:
        if project_id == self.project_id:
            return False
        self.invalidate_project(project_id)
        return True

    def request_immediate(self) -> tuple[bool, str | None]:
        if self.project_id is None:
            return False, "没有可同步的工程"
        if self.requires_full_recovery:
            return False, "请先执行完整同步以恢复"
        self.paused_reason = None
        if not self._pending:
            return False, "没有待同步的更改"
        self._immediate_requested = True
        return True, None

    def request_full(self) -> tuple[bool, str | None]:
        if self.project_id is None:
            return False, "没有可同步的工程"
        self._full_requested = True
        return True, None

    def next_batch(self, now: float) -> Batch | None:
        if self._running is not None or self.project_id is None:
            return None
        if self._full_requested:
            self._full_requested = self._immediate_requested = False
            self._pending.clear()
            self._last_change = None
            return self._start({}, "full")
        if self._immediate_requested:
            self._immediate_requested = False
            if self._pending and not self.requires_full_recovery:
                self.paused_reason = None
                return self._start(self._take_pending(), "immediate")
        if (self.enabled and self.paused_reason is None and not self.requires_full_recovery and self._pending
                and self._last_change is not None and now - self._last_change >= self.debounce):
            return self._start(self._take_pending(), "auto")
        return None

    def _take_pending(self) -> Scope:
        scope, self._pending = self._pending, {}
        return scope

    def _start(self, scope: Scope, trigger: str) -> Batch:
        self._counter += 1
        self._running = Batch(self._counter, self.generation, self.project_id,
                              scope, full=trigger == "full", trigger=trigger,
                              auto_generation=self.auto_generation)
        return self._running

    def is_same_project(self, batch: Batch) -> bool:
        return (batch.generation == self.generation and self.project_id is not None
                and batch.project_id == self.project_id)

    def is_current(self, batch: Batch) -> bool:
        return self.is_same_project(batch) and (
            batch.trigger != "auto" or batch.auto_generation == self.auto_generation)

    def finish(self, batch: Batch, failed: bool = False, pause_reason: str | None = None,
               require_full_recovery: bool = False) -> None:
        if self._running is not batch:
            return
        self._running = None
        current = self.is_current(batch)
        if failed and (current or (require_full_recovery and self.is_same_project(batch))):
            self.paused_reason = pause_reason or "同步失败"
            self.requires_full_recovery = self.requires_full_recovery or require_full_recovery or batch.full
            if self.requires_full_recovery:
                self._immediate_requested = False
            else:
                self._merge(batch.scope)
        elif not failed and current and batch.full:
            self.requires_full_recovery = False
            self.paused_reason = None

    def pending(self) -> Scope:
        return {name: None if tiles is WHOLE else tiles.copy()
                for name, tiles in self._pending.items()}

    def has_pending(self) -> bool:
        return bool(self._pending or self._full_requested or self._immediate_requested)

    def running_batch(self) -> Batch | None:
        return self._running
