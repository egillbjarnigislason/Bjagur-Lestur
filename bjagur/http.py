"""Polite HTTP client: per-host delay, retries, and 429 back-off."""

from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; BjagurBot/1.0; "
    "+https://github.com/egillbjarnigislason/Bjagur-Lestur)"
)


class FetchError(Exception):
    """A page could not be fetched. `permanent` means retrying will not help."""

    def __init__(self, message: str, status: int | None = None, permanent: bool = False):
        super().__init__(message)
        self.status = status
        self.permanent = permanent


class Http:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 0.0,
        timeout: tuple[float, float] = (10, 30),
        max_429_retries: int = 4,
        sleep=time.sleep,
    ):
        self.delay = delay
        self.timeout = timeout
        self.max_429_retries = max_429_retries
        self._sleep = sleep
        self._last_request = 0.0

        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept-Language": "is,en;q=0.8"}
        )
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))

    def _wait_turn(self) -> None:
        if self.delay and self._last_request:
            remaining = self.delay - (time.monotonic() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = time.monotonic()

    def get(self, url: str) -> requests.Response:
        """GET with rate limiting. Raises FetchError on any non-2xx result."""
        for attempt in range(self.max_429_retries + 1):
            self._wait_turn()
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                raise FetchError(f"{url}: {exc}") from exc

            if response.status_code == 429 and attempt < self.max_429_retries:
                wait = _retry_after(response) or min(30 * 2**attempt, 300)
                print(f"  429 from {url}; backing off {wait:.0f}s", flush=True)
                self._sleep(wait)
                continue

            if response.status_code in (404, 410):
                raise FetchError(f"{url}: HTTP {response.status_code}", response.status_code, True)
            if not response.ok:
                raise FetchError(f"{url}: HTTP {response.status_code}", response.status_code)
            return response

        raise FetchError(f"{url}: rate limited", 429)


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value and value.isdigit():
        return min(float(value), 600)
    return None
