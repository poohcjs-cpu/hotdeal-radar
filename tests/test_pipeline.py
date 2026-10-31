"""수집 → 정리 → 알림 전체 흐름 (가짜 웹, 네트워크 없음).

  python -m unittest discover -s tests -v
"""
import copy
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock as um

from collector import build, mock, notify, store
from collector.collect import Run, make_client
from collector.probe import Probe

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 31, 21, 7, tzinfo=store.KST)
DANAWA = ("search.danawa.com", "prod.danawa.com")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.json", self.tmp / "config.json")
        self.cfg = store.load_config(self.tmp)
        mock.ANCHOR = NOW

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_at(self, at, cfg=None, **web_kw):
        cfg = cfg or self.cfg
        web = mock.MockWeb(at, **web_kw)
        summary = Run(self.tmp, cfg, at, make_client(cfg, transport=web.transport, sleep=web.sleep, clock=web.clock),
                      log=lambda *_: None).run()
        return summary, web

    def generous(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["danawa"].update(max_verifications_per_run=80, max_product_pages_per_run=80, keywords_per_run=80)
        cfg["run"]["budget_minutes"] = 300
        return cfg

    def posts(self):
        return store.read_json(self.tmp / "data" / "community.json", {})["posts"]

    def post(self, needle):
        return next(p for p in self.posts().values() if needle in p["title"])


class CollectTest(Base):
    def test_full_run(self):
        summary, web = self.run_at(NOW, self.generous())
        st = summary["sources"]
        self.assertEqual(st["fmkorea"], "blocked")
        self.assertEqual(st["ppomppu_overseas"], "off")
        for sid in ("ppomppu", "ruliweb", "coolenjoy", "damoang", "quasarzone", "arcalive", "clien"):
            self.assertEqual(st[sid], "ok", sid)
        self.assertEqual(summary["danawa"], "ok")

        dyson = self.post("다이슨")["verify"]
        self.assertEqual(dyson["status"], "ok")
        self.assertAlmostEqual(dyson["rate"], 52, delta=1.5)
        self.assertEqual(dyson["reference"], "average")
        self.assertGreaterEqual(dyson["malls"], 3)
        self.assertTrue(self.post("갤럭시 S25")["verify"]["suspicious"])
        self.assertEqual(self.post("컬쳐랜드")["verify"]["code"], "non_product")
        self.assertEqual(self.post("엘든 링")["verify"]["code"], "digital")
        self.assertEqual(self.post("로보락")["verify"]["code"], "ended")
        self.assertEqual(self.post("극세사")["verify"]["status"], "nomatch")
        mx = self.post("MX Master")["verify"]  # 다나와 최저가가 오등록(39,000원)이어도 평균가로 제대로 계산
        self.assertAlmostEqual(mx["rate"], 33, delta=1.5)
        kanu = self.post("카누")["verify"]  # '120개입 2개' = 240개
        self.assertAlmostEqual(kanu["rate"], 21, delta=1.5)

        products = store.read_json(self.tmp / "data" / "products.json", {})
        self.assertIn("20480161", products)  # 키워드(에어프라이어)로 찾은 제품
        self.assertEqual(products["20480161"]["market"]["bestMall"], "G마켓")
        self.assertTrue(list((self.tmp / "data" / "history").glob("*.csv")))

        # 다나와 요청 사이 간격은 Crawl-delay(10초) 이상
        for host in DANAWA:
            times = [t for t, u in web.log if host in u and not u.endswith("robots.txt")]
            gaps = [b - a for a, b in zip(times, times[1:])]
            self.assertTrue(gaps and min(gaps) >= 9.9, (host, gaps[:5]))

    def test_limits_and_resume(self):
        s1, _ = self.run_at(NOW)
        posts = self.posts().values()
        done = [p for p in posts if p["verify"]["status"] in ("ok", "nomatch")]
        self.assertLessEqual(len(done), self.cfg["danawa"]["max_verifications_per_run"])
        self.assertTrue(any(p["verify"]["status"] == "pending" for p in posts))
        self.run_at(NOW + timedelta(minutes=30), self.generous())
        self.assertFalse(any(p["verify"]["status"] == "pending" for p in self.posts().values()))

    def test_second_run_reuses_results(self):
        self.run_at(NOW, self.generous())
        before = self.post("다이슨")["verify"]["checkedAt"]
        s2, web = self.run_at(NOW + timedelta(minutes=30), self.generous())
        self.assertEqual(s2["verified"], 0)
        self.assertEqual(self.post("다이슨")["verify"]["checkedAt"], before)
        self.assertFalse([u for _, u in web.log if "dsearch.php" in u])  # 검색 결과는 캐시에서

    def test_danawa_block_backs_off(self):
        s1, _ = self.run_at(NOW, blocked={"search.danawa.com", "www.fmkorea.com"})
        self.assertEqual(s1["danawa"], "blocked")
        state = store.read_json(self.tmp / "data" / "state.json", {})
        self.assertTrue(state["danawa"]["blockedUntil"])
        self.assertTrue(all(p["verify"]["status"] in ("pending", "skipped") for p in self.posts().values()))
        s2, web = self.run_at(NOW + timedelta(hours=1))
        self.assertFalse([u for _, u in web.log if "danawa.com" in u])  # 6시간 동안은 다나와에 다시 가지 않는다
        s3, web = self.run_at(NOW + timedelta(hours=7), self.generous())
        self.assertEqual(s3["danawa"], "ok")

    def test_source_problems_are_reported(self):
        s, _ = self.run_at(NOW, broken={"arca.live"}, down={"www.clien.net"})
        self.assertEqual(s["sources"]["arcalive"], "empty")
        self.assertEqual(s["sources"]["clien"], "unavailable")
        self.assertEqual(s["sources"]["ppomppu"], "ok")

    def test_danawa_layout_change_degrades_gracefully(self):
        s, _ = self.run_at(NOW, self.generous(), broken={"prod.danawa.com"})
        self.assertEqual(s["danawa"], "ok")
        self.assertEqual(self.post("다이슨")["verify"]["status"], "noprice")  # 가격을 못 읽으면 할인율을 지어내지 않는다
        out = build.build(self.tmp, self.cfg, NOW)
        dn = next(x for x in out["sources"] if x["id"] == "danawa")
        self.assertEqual(dn["status"], "warn")
        self.assertIn("판매처 목록", dn["message"])

    def test_robots_disallow_is_respected(self):
        with um.patch.dict(mock.ROBOTS, {"damoang.net": "User-agent: *\nDisallow: /\n"}):
            s, web = self.run_at(NOW)
        self.assertEqual(s["sources"]["damoang"], "robots")
        self.assertEqual([u for _, u in web.log if "damoang" in u], ["https://damoang.net/robots.txt"])

    def test_budget_stops_cleanly(self):
        cfg = self.generous()
        cfg["run"]["budget_minutes"] = 2
        s, _ = self.run_at(NOW, cfg)
        self.assertEqual(s["stoppedBy"], "budget")
        self.assertTrue((self.tmp / "data" / "state.json").exists())

    def test_watch_list_is_tracked_and_kept(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["watch"] = ["20480191"]  # PS5 프로: 커뮤니티 글도 키워드 검색 결과도 아닌 제품
        cfg["categories"] = [dict(c, keywords=[]) for c in cfg["categories"]]
        self.run_at(NOW, cfg)
        products = store.read_json(self.tmp / "data" / "products.json", {})
        self.assertTrue(products["20480191"]["watch"])
        self.assertTrue(products["20480191"].get("lastChecked"))
        self.run_at(NOW + timedelta(days=20), cfg)  # 오래 지나도 지우지 않는다
        self.assertIn("20480191", store.read_json(self.tmp / "data" / "products.json", {}))

    def test_post_cap(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["community"]["max_posts"] = 10
        self.run_at(NOW, cfg)
        self.assertEqual(len(self.posts()), 10)


class BuildNotifyTest(Base):
    def setUp(self):
        super().setUp()
        cfg = self.generous()
        for d in (3, 2, 1):
            self.run_at(NOW - timedelta(days=d), cfg)
        self.run_at(NOW, cfg)
        self.out = build.build(self.tmp, self.cfg, NOW)

    def test_deals_json(self):
        out = self.out
        self.assertEqual(out["version"], 2)
        saved = json.loads((self.tmp / "docs" / "data" / "deals.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["stats"], out["stats"])
        ids = {s["id"] for s in out["sources"]}
        self.assertIn("danawa", ids)
        self.assertEqual(out["categories"][-1]["id"], "etc")
        deals = out["deals"]
        self.assertEqual(deals, sorted(deals, key=lambda d: (-d["rate"], d["id"])))
        self.assertTrue(all(d["rate"] >= build.MIN_RATE_EXPORT for d in deals))
        ssd = [d for d in deals if d["pcode"] == "20480012" and d["kind"] == "community"]
        self.assertEqual(len(ssd), 1)  # 뽐뿌·쿨앤조이에 같은 딜 → 하나로 합침
        self.assertEqual({m["source"] for m in ssd[0]["mentions"]}, {"ppomppu", "coolenjoy"})
        fryer = next(d for d in deals if d["pcode"] == "20480161")
        self.assertEqual((fryer["kind"], fryer["mall"]), ("market", "G마켓"))
        self.assertAlmostEqual(fryer["rate"], 34, delta=1.5)
        tab = next(d for d in deals if d["pcode"] == "20480071")  # 사흘 전부터 이어진 자체 발굴 딜
        self.assertEqual(tab["detectedAt"], (NOW - timedelta(days=3)).isoformat())
        prod = out["products"]["20480141"]
        self.assertGreaterEqual(len(prod["history"]), 3)
        self.assertTrue(prod["offers"])
        self.assertIn(next(p["id"] for p in out["posts"] if "다이슨" in p["title"]), prod["posts"])

    def test_pick_and_format(self):
        picked, extra = notify.pick_deals(self.out, self.cfg["alerts"], {}, NOW)
        names = " ".join(d["name"] for d in picked)
        self.assertIn("다이슨", names)
        self.assertNotIn("S25", names)        # 가격 확인 필요
        self.assertNotIn("로보락", names)      # 끝난 딜
        self.assertTrue(all(d["rate"] >= 30 for d in picked))
        msgs = notify.format_messages(picked, extra, self.out, "https://me.github.io/hotdeal/")
        self.assertTrue(all(len(m) <= 4096 for m in msgs))
        self.assertIn("쇼핑몰 평균", msgs[0])

        alerts = dict(self.cfg["alerts"], max_per_run=100)
        everything, _ = notify.pick_deals(self.out, alerts, {}, NOW)
        state = {notify.deal_key(d): {"price": d["price"], "at": NOW.isoformat()} for d in everything}
        again, _ = notify.pick_deals(self.out, alerts, state, NOW + timedelta(hours=1))
        self.assertEqual(again, [])  # 같은 딜은 다시 알리지 않음
        cheaper = {k: {"price": v["price"] * 1.1, "at": v["at"]} for k, v in state.items()}
        self.assertEqual(len(notify.pick_deals(self.out, alerts, cheaper, NOW)[0]), len(everything))  # 더 내려가면 다시 알림

    def test_keyword_alert_lower_threshold(self):
        alerts = dict(self.cfg["alerts"], threshold=90, keywords=["버즈3"], keyword_threshold=20)
        picked, _ = notify.pick_deals(self.out, alerts, {}, NOW)
        self.assertEqual([d["pcode"] for d in picked], ["20480101"])
        self.assertTrue(picked[0]["_kw"])

    def test_notify_sends_and_remembers(self):
        sent = []
        self.cfg["alerts"]["max_per_run"] = 100
        with um.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}), \
                um.patch.object(notify, "send_telegram", lambda tok, chat, text: sent.append(text)):
            n = notify.notify(self.tmp, self.cfg, NOW, log=lambda *_: None)
            self.assertGreater(n, 0)
            self.assertTrue(sent)
            self.assertEqual(notify.notify(self.tmp, self.cfg, NOW + timedelta(minutes=30), log=lambda *_: None), 0)


class ProbeTest(Base):
    def test_probe_with_mock(self):
        web = mock.MockWeb(NOW)
        out = []
        p = Probe(self.tmp, self.cfg, make_client(self.cfg, transport=web.transport, sleep=web.sleep, clock=web.clock), out=out.append)
        self.assertTrue(p.source("ppomppu"))
        v = p.match("[SSG닷컴] 다이슨 V15 디텍트 (614,600원/무료)")
        self.assertEqual(v["status"], "ok")
        p.save_report()
        self.assertTrue((self.tmp / "data" / "debug" / "probe.json").exists())
        self.assertTrue(list((self.tmp / "data" / "debug").glob("product_*.html")))


class StoreTest(unittest.TestCase):
    def test_config_errors(self):
        base = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        bad = copy.deepcopy(base)
        bad["sources"]["naver"] = True
        with self.assertRaises(store.ConfigError):
            store.validate_config(bad)
        bad = copy.deepcopy(base)
        bad["categories"][0]["id"] = "etc"
        with self.assertRaises(store.ConfigError):
            store.validate_config(bad)
        bad = copy.deepcopy(base)
        bad["alerts"]["categories"] = ["nope"]
        with self.assertRaises(store.ConfigError):
            store.validate_config(bad)

    def test_history_roundtrip_and_prune(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            store.append_history(tmp, [
                {"date": "2026-08-01", "time": "09:00", "pcode": "1", "lowest": 100, "average": 120, "malls": 5, "best_mall": "쿠팡"},
                {"date": "2026-10-30", "time": "09:00", "pcode": "1", "lowest": 90, "average": "", "malls": 5, "best_mall": "쿠팡"},
                {"date": "2026-10-30", "time": "21:00", "pcode": "1", "lowest": 80, "average": 110, "malls": 6, "best_mall": "G마켓"},
            ])
            h = store.read_history(tmp, datetime(2026, 10, 1).date())
            self.assertEqual(h["1"], [(datetime(2026, 10, 30).date(), 80, 110, 6)])  # 하루에 여러 번이면 마지막 값
            self.assertEqual(store.prune_history(tmp, datetime(2026, 10, 31).date(), 60), 1)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
