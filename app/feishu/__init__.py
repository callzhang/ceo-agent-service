"""Official Feishu Bot channel.

The package is intentionally import-safe without the optional Feishu SDK.  The
real SDK is imported only by :func:`app.feishu.client.build_channel` when an
operator has explicitly enabled and configured the channel.
"""

FEISHU_CHANNEL = "feishu"

__all__ = ["FEISHU_CHANNEL"]
