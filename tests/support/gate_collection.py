"""Reuse native directory enumeration for one immutable gate checkout.

pytest revisits a directory for every explicitly selected file. Keep the native
collector and its package/fixture semantics; only enumerate its children once.
"""

from functools import cache

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_collect_directory(path, parent):
    collector = yield
    if collector is not None:
        collect = collector.collect

        @cache
        def children():
            return tuple(collect())

        collector.collect = children
    return collector
