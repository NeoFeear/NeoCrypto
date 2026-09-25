import logging
import threading
import time
from collections.abc import Callable


class RepeatFilter(logging.Filter):
    """Masks a log line identical to one already emitted within `window_s`.

    "Identical" = same message template and same first argument (the symbol,
    for engine.fifo_engine's rejection warnings), so a DCA pair that keeps
    hitting "BUY rejete: cash insuffisant" every poll logs it once per window
    instead of every cycle, while another symbol's rejection still shows. The
    first line after a window reports how many repeats were masked, so no
    information is lost -- only the noise.
    """

    def __init__(self, window_s: float = 6 * 3600, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._window_s = window_s
        self._clock = clock
        self._seen: dict[tuple, list] = {}  # key -> [last_emitted_at, masked_count]
        self._lock = threading.Lock()

    def filter(self, record: logging.LogRecord) -> bool:
        first_arg = record.args[0] if isinstance(record.args, tuple) and record.args else None
        key = (record.name, record.msg, first_arg)
        now = self._clock()
        with self._lock:
            entry = self._seen.get(key)
            if entry is not None and now - entry[0] < self._window_s:
                entry[1] += 1
                return False
            masked = entry[1] if entry is not None else 0
            self._seen[key] = [now, 0]
        if masked:
            record.msg = f"{record.msg} ({masked} repetition(s) masquee(s))"
        return True
