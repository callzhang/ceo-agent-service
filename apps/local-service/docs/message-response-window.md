# Message Response Window

The DingTalk reply producer only enqueues messages created within the latest
24 hours.

DWS message payloads expose `createTime` as a Beijing-time string. The worker
compares that instant against `now - 24 hours` in the machine's local timezone,
so Derek's local clock and DWS Beijing timestamps are compared by real time
rather than by wall-clock text.

Messages outside the window are recorded as skipped and marked seen, so they do
not keep reappearing in later producer runs.
