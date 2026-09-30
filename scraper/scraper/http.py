from __future__ import annotations

import time
import urllib.robotparser
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from .config import APPROVED_DOMAINS, Settings
from .utils import canonical_url, utc_now


class FetchError(RuntimeError):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


@dataclass
class FetchResult:
    url: str
    status_code: int
    content_type: str
    content: bytes
    headers: dict[str, str]


class PoliteClient:
    def __init__(self, settings: Settings, log_callback=None):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"})
        self.last_request: dict[str, float] = defaultdict(float)
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.log_callback = log_callback

    def approved(self, url: str) -> bool:
        return (urlsplit(url).hostname or "").lower() in APPROVED_DOMAINS

    def _log(self, event: str, url: str, **extra) -> None:
        if self.log_callback:
            self.log_callback({"timestamp": utc_now(), "event": event, "url": url, **extra})

    def _wait(self, host: str) -> None:
        remaining = self.settings.request_delay - (time.monotonic() - self.last_request[host])
        if remaining > 0:
            time.sleep(remaining)

    def _load_robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        robots_url = origin + "/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            self._wait(parts.hostname or "")
            response = self.session.get(robots_url, timeout=self.settings.timeout)
            self.last_request[parts.hostname or ""] = time.monotonic()
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
                self.robots[origin] = parser
            else:
                self.robots[origin] = None
                self._log("robots_unavailable", robots_url, http_status=response.status_code)
        except requests.RequestException as exc:
            self.robots[origin] = None
            self._log("robots_unavailable", robots_url, error=str(exc))
        return self.robots[origin]

    def get(self, url: str) -> FetchResult:
        url = canonical_url(url)
        if not self.approved(url):
            raise FetchError(f"unapproved domain: {url}")
        robots = self._load_robots(url)
        if robots is not None and not robots.can_fetch(self.settings.user_agent, url):
            self._log("robots_disallowed", url)
            raise FetchError("robots.txt disallows access")
        host = urlsplit(url).hostname or ""
        last_error = "unknown error"
        last_status = None
        for attempt in range(self.settings.max_retries + 1):
            self._wait(host)
            try:
                response = self.session.get(url, timeout=self.settings.timeout, allow_redirects=True)
                self.last_request[host] = time.monotonic()
                final_url = canonical_url(response.url)
                if not self.approved(final_url):
                    raise FetchError(f"redirected to unapproved domain: {final_url}")
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.max_retries:
                    delay = min(2**attempt, 8)
                    self._log("retry", url, http_status=response.status_code, delay=delay)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                self._log("fetched", final_url, http_status=response.status_code, content_type=content_type, bytes=len(response.content))
                return FetchResult(final_url, response.status_code, content_type, response.content, dict(response.headers))
            except FetchError:
                raise
            except requests.RequestException as exc:
                last_error = str(exc)
                last_status = exc.response.status_code if exc.response is not None else None
                self.last_request[host] = time.monotonic()
                if attempt < self.settings.max_retries:
                    delay = min(2**attempt, 8)
                    self._log("retry", url, error=last_error, delay=delay)
                    time.sleep(delay)
        raise FetchError(last_error, last_status)
