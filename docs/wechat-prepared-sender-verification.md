# WeChat prepared sender verification — 2026-10-26

## Existing fix and root cause

The automatic sender fix `e00211098` and retry API completion `bd2a3e117`
were already installed in production `7a05e1126` when this follow-up began.
No duplicate runtime implementation, production retry or additional restart is
performed. Installed `app/wechat/service.py` exactly matches the tested source.

Standard Consumer actions prepare their immutable final body under the action's
delivery key. The original sender loop called `WechatSender.send` without that
prepared record, causing a lookup only under `wechat:{delivery_id}` and the
pre-provider exception `prepared WeChat delivery is required`. The existing
fix resolves and passes the original persisted preparation; the retry APIs do
the same before requeue. No new candidate, body or target is generated here.

## Substitute verification

- Original pre-fix code reproduces the exact exception with a real Store,
  reviewed candidate and action-key preparation; the substitute runner has
  zero calls and the delivery remains ready.
- Current auto sender and explicit approval use that same preparation, reach
  `sent` through the substitute runner once, and send nothing on a second tick.
  Two persistent integration regressions cover these actual Store paths.
- Send-mode/service/accessibility files:85 pass,1 pre-existing xfail in46.18s.
  The xfail is an unrelated old `delete_action('outbound_postfixes')` fixture
  referencing a retired column; it is not newly skipped or called green here.
- Handler/current-review tests including the two new regressions:28 pass in8.25s.
  Ruff and diff checks pass. Existing uncertain-send, failed retry, missing
  preparation, exact review/body/target and no-repeat assertions remain.
- All actual sending in this verification uses substitutes. No production
  message, retry, recall, candidate or Task is created by this follow-up.

## Fresh production readback

At20:07:59 UTC (13:07:59 America/Los_Angeles), production is `7a05e1126`,
PID42152, health200/ok. Status, Attention and error detail APIs return200.
All481 retained matching historical errors are resolved; last occurrence was
19:41:35 UTC and resolution was19:45:25 UTC, recorded as a later successful
sender tick. This error is absent from current Attention. Actual Chrome error
16127 shows resolved and the Attention page omits it.

Read-only delivery inventory has62 sent,6 skipped and74 superseded, with none
ready/sending/unknown. Existing delivery146 is sent at19:54:45 UTC with empty
error; this is existing production state, not a test send. Other business
failures remain: failed Tasks27 and Attention summary4. FK findings are empty.

This run adds regression tests and acceptance documentation only. The already
deployed runtime repair remains the accepted installation; no new runtime
deployment or schema migration is needed.

Exact body-free evidence and cleanup disposition:
[verification record](validation/2026-10-26-wechat-prepared-sender.json).

After complete-marker/full-integrity and zero-open-file/no-active-deploy checks,
the exact current workflow backup and owned temporary root were removed and
their absence verified. Production DB/native trajectories remain. No database
space reduction is attributed to temporary artifact cleanup.
