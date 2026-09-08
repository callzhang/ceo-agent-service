from __future__ import annotations

from importlib import import_module

import pytest


def _important_module():
    return import_module("app.email_important")


@pytest.mark.parametrize(
    ("provider", "raw_signal_names"),
    (
        ("dingtalk", ("11",)),
        ("dingtalk", ("1",)),
        ("dingmail", ("107",)),
        ("dingmail", ("PRY_HIGH",)),
        ("gmail", ("STARRED",)),
        ("gmail", ("IMPORTANT",)),
        ("imap", ("\\Flagged",)),
        ("imap", ("$Important",)),
        ("microsoft_graph", ("flagged",)),
        ("microsoft_graph", ("importance=high",)),
        ("microsoft_graph", ("focused",)),
    ),
)
def test_trusted_provider_signals_form_the_important_union(
    provider: str,
    raw_signal_names: tuple[str, ...],
) -> None:
    module = _important_module()
    assert module.normalize_important_signals(
        provider=provider,
        raw_signal_names=raw_signal_names,
    ) == module.ImportantSignals(
        raw_signal_names=raw_signal_names,
        provider_important=True,
    )


@pytest.mark.parametrize(
    ("provider", "raw_signal_names"),
    (
        ("dingtalk", ("X-Priority: 1",)),
        ("gmail", ("URGENT",)),
        ("imap", ("Priority",)),
        ("microsoft_graph", ("classifier_confidence=0.99",)),
    ),
)
def test_headers_keywords_and_classifier_features_do_not_set_provider_important(
    provider: str,
    raw_signal_names: tuple[str, ...],
) -> None:
    module = _important_module()
    assert module.normalize_important_signals(
        provider=provider,
        raw_signal_names=raw_signal_names,
    ) == module.ImportantSignals(
        raw_signal_names=raw_signal_names,
        provider_important=False,
    )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"raw_signal_names": ["STARRED"], "provider_important": True},
        {"raw_signal_names": (" STARRED",), "provider_important": True},
        {"raw_signal_names": ("STARRED", "STARRED"), "provider_important": True},
        {"raw_signal_names": (1,), "provider_important": True},
        {"raw_signal_names": ("STARRED",), "provider_important": 1},
    ),
)
def test_important_signals_reject_non_exact_inputs(kwargs: dict[str, object]) -> None:
    module = _important_module()
    with pytest.raises((TypeError, ValueError)):
        module.ImportantSignals(**kwargs)


def test_important_effective_is_independent_of_category_except_junk() -> None:
    module = _important_module()
    provider_signal = module.ImportantSignals(("STARRED",), True)
    no_provider_signal = module.ImportantSignals((), False)

    assert module.important_effective(
        category="work",
        provider_signals=provider_signal,
        model_important=False,
    )
    assert module.important_effective(
        category="work",
        provider_signals=no_provider_signal,
        model_important=True,
    )
    assert not module.important_effective(
        category="junk",
        provider_signals=provider_signal,
        model_important=True,
    )
