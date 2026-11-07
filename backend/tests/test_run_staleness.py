"""Offline: the stale-run threshold comes from the real call limits, not the 300 s agent budget."""

from types import SimpleNamespace

import openai._constants

from app.runs import service


def limits(timeout=30.0, retries=4):
    return SimpleNamespace(llm_timeout_seconds=timeout, llm_max_retries=retries)


def test_retry_after_cap_matches_the_installed_sdk():
    # If the SDK changes how long it honours retry-after, the threshold must change with it.
    assert service.SDK_MAX_RETRY_AFTER_SECONDS == openai._constants.MAX_RETRY_AFTER_DELAY


def test_default_threshold_covers_the_longest_silent_model_call():
    # 5 attempts x 30 s + 4 retry waits x 120 s = 630 s of possible silence, + 120 s margin.
    assert service.stale_run_seconds(limits()) == 750
    assert service.stale_run_seconds(limits()) > 300  # the agent budget alone would be unsafe


def test_threshold_follows_the_configured_limits():
    assert service.stale_run_seconds(limits(timeout=60, retries=0)) == 60 + service.STALE_RUN_MARGIN_SECONDS
    assert service.stale_run_seconds(limits(timeout=30, retries=8)) == 30 * 9 + 8 * 120 + 120
