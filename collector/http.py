"""예의 바른 HTTP 클라이언트.

모든 외부 요청은 이 클라이언트를 거친다. 지키는 규칙:
  1. robots.txt를 먼저 읽고 허용된 경로만 요청한다 (RFC 9309, * 와 $ 와일드카드 지원).
     - robots.txt가 4xx(429 제외)면 '제한 없음'으로 보고, 5xx·429·연결 실패면 이번 실행에서는 그 사이트를 건너뛴다.
  2. 같은 사이트에는 Crawl-delay(없으면 설정한 최소 간격) 이상 쉬었다가 요청한다.
  3. 우리가 누구인지 숨기지 않는 User-Agent를 쓴다.
  4. 403·429·봇 확인 페이지를 만나면 '차단됨'으로 기록하고 물러난다. 우회하지 않는다.
  5. 실행 시간 예산을 넘기지 않는다.
"""
from __future__ import annotations

import gzip
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Callable

BOT_NAME = "HotdealRadar"
DEFAULT_UA = f"Mozilla/5.0 (compatible; {BOT_NAME}/2.0; personal price checker)"
MAX_BODY = 4 * 1024 * 1024
CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "<title>Just a moment...</title>", "Attention Required! | Cloudflare",
                     "captcha-delivery", "Access denied | ")


class FetchError(RuntimeError):
    """일반 요청 실패(네트워크, 5xx 등)."""


class RobotsDisallowed(FetchError):
    """robots.txt가 막은 경로."""


class RobotsUnavailable(FetchError):
    """robots.txt를 확인할 수 없어 이번에는 요청하지 않음."""


class Blocked(FetchError):
    """사이트가 봇 요청을 막음 (403/429/봇 확인 페이지)."""


class BudgetExceeded(FetchError):
    """실행 시간 예산을 다 썼음."""


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes

    def text(self) -> str:
        return decode_body(self.body, self.headers.get("content-type", ""))


def decode_body(body: bytes, content_type: str = "") -> str:
    """선언된 문자셋(헤더 → XML 선언 → meta)으로 해독. EUC-KR은 상위 호환인 CP949로 읽는다."""
    charset = None
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    head = body[:2048].decode("ascii", "ignore")
    if not charset:
        m = re.search(r'<\?xml[^>]*encoding=["\']([\w-]+)', head, re.I) or \
            re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.I)
        if m:
            charset = m.group(1)
    charset = (charset or "utf-8").lower().replace("_", "-")
    if charset in ("euc-kr", "ks-c-5601-1987", "ksc5601", "x-windows-949", "windows-949"):
        charset = "cp949"
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


# ── robots.txt ──────────────────────────────────────────────

@dataclass
class _Group:
    agents: list = field(default_factory=list)
    rules: list = field(default_factory=list)  # (allow: bool, pattern: str)
    delay: float | None = None


class Robots:
    """robots.txt 해석기 (RFC 9309). 가장 긴 규칙이 이기고, 길이가 같으면 Allow가 이긴다."""

    def __init__(self, text: str | None, allow_all: bool = False, disallow_all: bool = False):
        self.allow_all = allow_all
        self.disallow_all = disallow_all
        self.groups: list[_Group] = []
        if text:
            self._parse(text)

    def _parse(self, text: str) -> None:
        cur: _Group | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if cur is None or not last_was_agent:
                    cur = _Group()
                    self.groups.append(cur)
                cur.agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if cur is None:
                continue
            if key in ("allow", "disallow"):
                if value:
                    cur.rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    cur.delay = float(value)
                except ValueError:
                    pass

    def _group_for(self, agent: str) -> _Group | None:
        agent = agent.lower()
        specific = [g for g in self.groups if any(a != "*" and a in agent for a in g.agents)]
        if specific:
            merged = _Group(rules=[r for g in specific for r in g.rules])
            delays = [g.delay for g in specific if g.delay is not None]
            merged.delay = max(delays) if delays else None
            return merged
        stars = [g for g in self.groups if "*" in g.agents]
        if not stars:
            return None
        merged = _Group(rules=[r for g in stars for r in g.rules])
        delays = [g.delay for g in stars if g.delay is not None]
        merged.delay = max(delays) if delays else None
        return merged

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
        return re.match(regex + ("$" if anchored else ""), path) is not None

    def allowed(self, agent: str, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        group = self._group_for(agent)
        if group is None:
            return True
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(parts.path or "/") + (("?" + parts.query) if parts.query else "")
        best_len, verdict = -1, True
        for allow, pattern in group.rules:
            if self._match(pattern, path):
                length = len(pattern)
                if length > best_len or (length == best_len and allow):
                    best_len, verdict = length, allow
        return verdict

    def crawl_delay(self, agent: str) -> float | None:
        group = self._group_for(agent)
        return group.delay if group else None


# ── 클라이언트 ───────────────────────────────────────────────

Transport = Callable[[str, dict, float], tuple]  # (url, headers, timeout) -> (status, headers, body, final_url)


def urllib_transport(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BODY + 1)
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, body, resp.geturl()
    except urllib.error.HTTPError as e:
        body = e.read(MAX_BODY + 1) if e.fp else b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body, url


class PoliteClient:
    def __init__(self, *, user_agent: str = DEFAULT_UA, default_delay: float = 3.0, timeout: float = 20.0,
                 deadline: float | None = None, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.user_agent = user_agent
        self.default_delay = default_delay
        self.timeout = timeout
        self.deadline = deadline
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self.clock = clock
        self._robots: dict[str, Robots] = {}
        self._last: dict[str, float] = {}
        self.host_delay: dict[str, float] = {}  # 사이트별 최소 간격(설정)
        self.requests = 0
        self.log: list[str] = []

    # robots ------------------------------------------------
    def robots_for(self, url: str) -> Robots:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        try:
            status, headers, body, _ = self._raw(origin + "/robots.txt", count_delay=True)
        except FetchError:
            robots = Robots(None, disallow_all=True)
            robots.unavailable = True
            self._robots[origin] = robots
            return robots
        if 200 <= status < 300:
            robots = Robots(decode_body(body, headers.get("content-type", "")))
        elif 400 <= status < 500 and status != 429:
            robots = Robots(None, allow_all=True)
        else:
            robots = Robots(None, disallow_all=True)
            robots.unavailable = True
        self._robots[origin] = robots
        return robots

    def can_fetch(self, url: str) -> bool:
        return self.robots_for(url).allowed(BOT_NAME, url)

    # 요청 --------------------------------------------------
    def _wait_for(self, host: str) -> None:
        robots = self._robots.get(self._origin(host))
        delay = max(self.default_delay, self.host_delay.get(host, 0.0),
                    (robots.crawl_delay(BOT_NAME) or 0.0) if robots else 0.0)
        last = self._last.get(host)
        if last is not None:
            wait = last + delay - self.clock()
            if wait > 0:
                if self.deadline is not None and self.clock() + wait > self.deadline:
                    raise BudgetExceeded(f"{host}: 시간 예산 초과")
                self.sleep(wait)

    def _origin(self, host: str) -> str:
        for origin in self._robots:
            if urllib.parse.urlsplit(origin).netloc == host:
                return origin
        return f"https://{host}"

    def _raw(self, url: str, count_delay: bool) -> tuple:
        host = urllib.parse.urlsplit(url).netloc
        if self.deadline is not None and self.clock() > self.deadline:
            raise BudgetExceeded("시간 예산 초과")
        if count_delay:
            self._wait_for(host)
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            self.requests += 1
            status, rheaders, body, final = self.transport(url, headers, self.timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise FetchError(f"{url}: 연결 실패 ({e})") from None
        finally:
            self._last[host] = self.clock()
        enc = (rheaders.get("content-encoding") or "").lower()
        try:
            if enc == "gzip" or body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            elif enc == "deflate":
                body = zlib.decompress(body)
        except (OSError, zlib.error):
            pass
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY]
        return status, rheaders, body, final

    def get(self, url: str) -> Response:
        robots = self.robots_for(url)
        if getattr(robots, "unavailable", False):
            raise RobotsUnavailable(f"{url}: robots.txt를 확인할 수 없어 건너뜀")
        if not robots.allowed(BOT_NAME, url):
            raise RobotsDisallowed(f"{url}: robots.txt가 허용하지 않음")
        last_error = None
        for attempt in range(2):
            status, headers, body, final = self._raw(url, count_delay=True)
            if status in (403, 429, 430, 451) or (status == 503 and _looks_like_challenge(body)):
                raise Blocked(f"{url}: 사이트가 요청을 막음 (HTTP {status})")
            if 200 <= status < 300:
                if _looks_like_challenge(body):
                    raise Blocked(f"{url}: 봇 확인 페이지가 나옴")
                return Response(final, status, headers, body)
            if 500 <= status < 600 and attempt == 0:
                last_error = f"HTTP {status}"
                continue
            raise FetchError(f"{url}: HTTP {status}")
        raise FetchError(f"{url}: {last_error}")


def _looks_like_challenge(body: bytes) -> bool:
    head = body[:6000].decode("utf-8", "ignore")
    return any(marker in head for marker in CHALLENGE_MARKERS)
