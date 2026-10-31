"""robots.txt 준수, 요청 간격, 차단 시 물러나기."""
import gzip
import unittest

from collector.http import (BOT_NAME, Blocked, BudgetExceeded, FetchError, PoliteClient, Robots, RobotsDisallowed, RobotsUnavailable,
                            decode_body)


class FakeNet:
    def __init__(self, routes):
        self.routes = routes  # url → (status, headers, body) 또는 그 목록(차례로)
        self.t = 0.0
        self.calls = []
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 2))
        self.t += s

    def transport(self, url, headers, timeout):
        self.calls.append((url, headers.get("User-Agent")))
        self.t += 0.1
        r = self.routes.get(url, (404, {}, b""))
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        return r[0], r[1], r[2], url

    def client(self, **kw):
        return PoliteClient(transport=self.transport, sleep=self.sleep, clock=self.clock, **kw)


ROBOTS = b"""User-agent: *
Crawl-delay: 10
Disallow: /info/ajax/
Disallow: /*.json$
Allow: /info/

User-agent: BadBot
Disallow: /
"""


class RobotsTest(unittest.TestCase):
    def test_rules(self):
        r = Robots(ROBOTS.decode())
        self.assertTrue(r.allowed(BOT_NAME, "https://prod.danawa.com/info/?pcode=1"))
        self.assertFalse(r.allowed(BOT_NAME, "https://prod.danawa.com/info/ajax/getAllPriceCompareMallList.ajax.php"))
        self.assertFalse(r.allowed(BOT_NAME, "https://x.com/data/a.json"))
        self.assertTrue(r.allowed(BOT_NAME, "https://x.com/data/a.json?x=1"))  # $ 는 끝을 뜻함
        self.assertFalse(r.allowed("BadBot", "https://x.com/info/"))
        self.assertEqual(r.crawl_delay(BOT_NAME), 10.0)

    def test_longest_match_and_allow_ties(self):
        r = Robots("User-agent: *\nDisallow: /b\nAllow: /b/ok\nDisallow: /c\nAllow: /c\n")
        self.assertTrue(r.allowed(BOT_NAME, "https://x.com/b/ok/1"))
        self.assertFalse(r.allowed(BOT_NAME, "https://x.com/b/no"))
        self.assertTrue(r.allowed(BOT_NAME, "https://x.com/c"))

    def test_decode_korean_legacy_encoding(self):
        body = "뽐뿌".encode("euc-kr")
        self.assertEqual(decode_body(body, "text/html; charset=EUC-KR"), "뽐뿌")
        self.assertEqual(decode_body(b'<meta charset="euc-kr">' + body, ""), '<meta charset="euc-kr">뽐뿌')


class ClientTest(unittest.TestCase):
    def test_respects_robots_and_crawl_delay(self):
        net = FakeNet({"https://prod.danawa.com/robots.txt": (200, {}, ROBOTS),
                       "https://prod.danawa.com/info/?pcode=1": (200, {}, b"<html>ok</html>"),
                       "https://prod.danawa.com/info/?pcode=2": (200, {}, b"<html>ok</html>")})
        c = net.client(default_delay=3)
        c.get("https://prod.danawa.com/info/?pcode=1")
        c.get("https://prod.danawa.com/info/?pcode=2")
        self.assertTrue(all(s >= 9.8 for s in net.sleeps), net.sleeps)  # Crawl-delay 10초
        with self.assertRaises(RobotsDisallowed):
            c.get("https://prod.danawa.com/info/ajax/x.php")
        self.assertIn("HotdealRadar", net.calls[0][1])  # 정체를 밝히는 User-Agent

    def test_robots_missing_means_allowed_but_error_means_skip(self):
        net = FakeNet({"https://a.com/robots.txt": (404, {}, b""), "https://a.com/x": (200, {}, b"ok"),
                       "https://b.com/robots.txt": (503, {}, b""), "https://c.com/robots.txt": ConnectionError("x")})
        c = net.client()
        self.assertEqual(c.get("https://a.com/x").text(), "ok")
        with self.assertRaises(RobotsUnavailable):
            c.get("https://b.com/x")
        with self.assertRaises(RobotsUnavailable):
            c.get("https://c.com/x")

    def test_blocked_and_challenge(self):
        net = FakeNet({"https://a.com/robots.txt": (404, {}, b""), "https://a.com/403": (403, {}, b""),
                       "https://a.com/430": (430, {}, b""),
                       "https://a.com/cf": (200, {}, b"<html><title>Just a moment...</title>cf-chl</html>")})
        c = net.client()
        for path in ("403", "430", "cf"):
            with self.assertRaises(Blocked):
                c.get(f"https://a.com/{path}")

    def test_retry_once_on_5xx_and_gzip(self):
        net = FakeNet({"https://a.com/robots.txt": (404, {}, b""),
                       "https://a.com/x": [(502, {}, b""), (200, {"content-encoding": "gzip"}, gzip.compress("가격".encode()))],
                       "https://a.com/y": [(500, {}, b""), (500, {}, b"")]})
        c = net.client()
        self.assertEqual(c.get("https://a.com/x").text(), "가격")
        with self.assertRaises(FetchError):
            c.get("https://a.com/y")

    def test_budget(self):
        net = FakeNet({"https://a.com/robots.txt": (404, {}, b""), "https://a.com/x": (200, {}, b"ok")})
        c = net.client(default_delay=30, deadline=40)
        c.get("https://a.com/x")  # robots.txt 다음 30초 쉬고 요청 (t≈30)
        with self.assertRaises(BudgetExceeded):
            c.get("https://a.com/x")  # 또 30초를 기다리면 예산(40초)을 넘으므로 요청하지 않는다


if __name__ == "__main__":
    unittest.main()
