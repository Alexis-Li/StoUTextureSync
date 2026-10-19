"""Idle-debounced, serial batch scheduling without host dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field

WHOLE = None  # a whole Texture Set instead of individual tiles


@dataclass
class Batch:
    number: int
    generation: int
    scope: dict[str, set | None]     # Texture Set -> set of (u, v) tiles, or WHOLE
    full: bool = False


@dataclass
class Scheduler:
    """Records changes and hands out at most one batch at a time.

    `generation` increases whenever earlier work becomes invalid (real-time
    switched off, project closed or switched, binding changed); a batch is
    current only while its generation is the scheduler's generation.
    """

    debounce: float = 1.0
    enabled: bool = True
    generation: int = 0
    _pending: dict[str, set | None] = field(default_factory=dict)
    _last_change: float | None = None
    _running: Batch | None = None
    _full_requested: bool = False
    _counter: int = 0

    def record(self, texture_set: str, tile: tuple[int, int] | None, now: float) -> None:
        """A texture changed; `tile=None` widens the scope to the whole Texture Set."""
        if tile is None or self._pending.get(texture_set, set()) is WHOLE:
            self._pending[texture_set] = WHOLE
        else:
            self._pending.setdefault(texture_set, set()).add(tuple(tile))
        self._last_change = now

    def request_full(self) -> None:
        self._full_requested = True

    def set_enabled(self, enabled: bool) -> None:
        if not enabled and self.enabled:
            self.invalidate()
        self.enabled = enabled

    def invalidate(self) -> None:
        self.generation += 1

    def pending(self) -> dict[str, set | None]:
        return dict(self._pending)

    def next_batch(self, now: float, immediate: bool = False) -> Batch | None:
        """Return the next batch to export, or None if nothing should start now."""
        if self._running is not None:
            return None
        if self._full_requested:
            self._full_requested = False
            self._pending.clear()
            return self._start({}, full=True)
        if not self._pending:
            return None
        if not immediate:
            if not self.enabled or self._last_change is None:
                return None
            if now - self._last_change < self.debounce:
                return None
        scope, self._pending = self._pending, {}
        return self._start(scope)

    def _start(self, scope, full=False) -> Batch:
        self._counter += 1
        self._running = Batch(self._counter, self.generation, scope, full)
        return self._running

    def is_current(self, batch: Batch) -> bool:
        return batch.generation == self.generation

    def finish(self, batch: Batch, failed: bool = False) -> None:
        """End the running batch; a failed batch's scope is merged back into pending."""
        if self._running is batch:
            self._running = None
        if failed and self.is_current(batch):
            for texture_set, tiles in batch.scope.items():
                if tiles is WHOLE:
                    self._pending[texture_set] = WHOLE
                elif self._pending.get(texture_set, set()) is not WHOLE:
                    self._pending.setdefault(texture_set, set()).update(tiles)
