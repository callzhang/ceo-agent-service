"""Hard memory ceiling for a test process.

Derek, 2026-10-23: a parallel full run put six pytest workers at 13-23 GB each
(about 80 GB together) and froze the machine. A test process that reaches the
ceiling is ended at once, with the test it was running in the message, so the
run fails loudly instead of taking the machine down.
"""

import os
import resource
import sys
import threading
import time

POLL_SECONDS = 0.2
EXIT_CODE = 70


def peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


class MemoryLimit:
    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        self.current_test = "(before the first test)"

    def start(self) -> None:
        threading.Thread(target=self._watch, name="test-memory-limit", daemon=True).start()

    def _watch(self) -> None:
        while True:
            time.sleep(POLL_SECONDS)
            peak = peak_rss_bytes()
            if peak > self.limit_bytes:
                message = (
                    f"\nMEMORY LIMIT EXCEEDED: this test process reached {peak / 2**30:.1f} GB "
                    f"(limit {self.limit_bytes / 2**30:.0f} GB) while running {self.current_test}.\n"
                    "The limit is fixed and must not be raised. Fix the test, or the code it runs, "
                    "so it stays under the limit.\n"
                )
                os.write(2, message.encode())
                os._exit(EXIT_CODE)
