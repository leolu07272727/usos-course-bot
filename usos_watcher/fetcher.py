"""Thin retry/jitter wrapper around requests.Session for USOS calls."""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)


def get(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    retries: int,
    jitter_max_sec: int,
    allow_redirects: bool = True,
) -> requests.Response:
    """GET with retries and small random jitter before each attempt."""
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        # Jitter — even on attempt 0, to spread load if the cron fires on the
        # exact second every cycle.
        if jitter_max_sec > 0:
            time.sleep(random.uniform(0, jitter_max_sec))
        try:
            log.debug("GET %s (attempt %d)", url, attempt + 1)
            r = session.get(url, timeout=timeout, allow_redirects=allow_redirects)
            if r.status_code in (429, 503):
                log.warning("HTTP %d on GET %s; backing off", r.status_code, url)
                last_exc = requests.HTTPError(f"{r.status_code} {r.reason}")
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
            log.warning("Request failed on attempt %d: %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc


def post(
    session: requests.Session,
    url: str,
    *,
    data,
    timeout: int,
    retries: int,
    jitter_max_sec: int,
    allow_redirects: bool = True,
) -> requests.Response:
    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        if jitter_max_sec > 0:
            time.sleep(random.uniform(0, jitter_max_sec))
        try:
            log.debug("POST %s (attempt %d)", url, attempt + 1)
            r = session.post(url, data=data, timeout=timeout,
                             allow_redirects=allow_redirects)
            if r.status_code in (429, 503):
                last_exc = requests.HTTPError(f"{r.status_code} {r.reason}")
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_exc = e
            log.warning("Request failed on attempt %d: %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc
