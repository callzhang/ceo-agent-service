"""Hard memory and time budget for a test process.

Derek, 2026-10-23: a parallel full run put six pytest workers at 13-23 GB each
(about 80 GB together) and froze the machine. A test process that reaches the
ceiling is ended at once, with the test it was running in the message, so the
run fails loudly instead of taking the machine down.

Derek, 2026-10-25: a test run must also finish within a fixed time. A process
that has been running longer than the budget is ended the same way. Both
limits are constants in tests/conftest.py with no environment variable or
option to change them.
"""

import ctypes
import os
import sys
import threading
import time

POLL_SECONDS = 0.2
EXIT_CODE = 70
TIME_EXIT_CODE = 71


def footprint_bytes() -> int:
    """Memory this process holds, including pages the system compressed or swapped out.

    RSS is not enough: under memory pressure macOS compresses a process, its RSS
    drops to a few hundred MB, and a 2026-10-23 run held gigabytes the RSS never showed.
    """
    if sys.platform == "darwin":
        # rusage_info_v0: ri_uuid (2 words), then 7 counters; word 9 is ri_phys_footprint.
        info = (ctypes.c_uint64 * 32)()
        if ctypes.CDLL(None).proc_pid_rusage(os.getpid(), 0, info) != 0:
            raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
        return info[9]
    status = {}
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            name, _, value = line.partition(":")
            status[name] = value
    return sum(int(status[key].split()[0]) * 1024 for key in ("VmRSS", "VmSwap"))


class MemoryLimit:
    def __init__(self, limit_bytes: int, time_limit_seconds: float | None = None) -> None:
        self.limit_bytes = limit_bytes
        self.time_limit_seconds = time_limit_seconds
        self.current_test = "(before the first test)"
        self.started_at = time.monotonic()

    def start(self) -> None:
        self.started_at = time.monotonic()
        threading.Thread(target=self._watch, name="test-memory-limit", daemon=True).start()

    def _watch(self) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            held = footprint_bytes()
            if held > self.limit_bytes:
                message = (
                    f"\nMEMORY LIMIT EXCEEDED: this test process reached {held / 2**30:.1f} GB "
                    f"(limit {self.limit_bytes / 2**30:.0f} GB) while running {self.current_test}.\n"
                    "The limit is fixed and must not be raised. Fix the test, or the code it runs, "
                    "so it stays under the limit.\n"
                )
                os.write(2, message.encode())
                os._exit(EXIT_CODE)
            elapsed = time.monotonic() - self.started_at
            if self.time_limit_seconds is not None and elapsed > self.time_limit_seconds:
                message = (
                    f"\nTIME LIMIT EXCEEDED: this test process has run {elapsed:.0f} s "
                    f"(limit {self.time_limit_seconds:.0f} s) and was running {self.current_test}.\n"
                    "The limit is fixed and must not be raised. Make the tests faster, or run fewer "
                    "of them, so the run finishes inside the limit.\n"
                )
                os.write(2, message.encode())
                os._exit(TIME_EXIT_CODE)
