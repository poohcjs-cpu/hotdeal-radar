"""수집 한 번 실행하기.

  python -m collector.collect           # 실제 사이트에서 수집
  python -m collector.collect --mock    # 가짜 사이트로 동작 확인 (네트워크 없이)

한 번 실행할 때 하는 일 (실행 시간 예산 안에서, 앞의 일부터)
  1. 커뮤니티 핫딜 게시판 읽기 (RSS 또는 첫 페이지)
  2. 새 커뮤니티 딜 검증: 다나와에서 같은 제품을 찾아 쇼핑몰별 가격과 비교
  3. 제품군 키워드로 다나와 검색해 지켜볼 제품 목록 갱신 (하루 한 번)
  4. 지켜보는 제품의 다나와 상품 페이지를 돌아가며 확인 → 쇼핑몰 간 가격 차이로 핫딜 자체 발굴
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from . import danawa, store
from .community import SOURCE_BY_ID
from .compare import market_deal, match_deal, verify_deal
from .danawa import Candidate, Option
from .http import Blocked, BudgetExceeded, FetchError, PoliteClient, RobotsDisallowed, RobotsUnavailable
from .textnorm import parse_title, search_query

SKIP_REASONS = {
    "non_product": "상품권·포인트·쿠폰 같은 글이라 가격 비교를 하지 않아요",
    "digital": "디지털 콘텐츠라 쇼핑몰 가격 비교를 하지 않아요",
    "overseas": "해외 딜이라 국내 쇼핑몰 가격과 비교하지 않아요",
    "free": "무료 배포 글이에요",
    "noprice": "제목에서 가격을 찾지 못했어요",
    "ended": "이미 끝난 딜이에요",
    "multi": "가격이 여러 개라 하나로 비교하기 어려워요",
}


def classify(cfg: dict, text: str) -> str:
    best, best_n = "etc", 0
    low = (text or "").lower()
    for c in cfg["categories"]:
        n = sum(1 for w in c.get("match_words", []) if w.lower() in low)
        if n > best_n:
            best, best_n = c["id"], n
    return best


def _cand_to_json(c: Candidate) -> dict:
    return {"pcode": c.pcode, "name": c.name, "price": c.price, "malls": c.malls, "image": c.image,
            "variant": sorted(c.variant), "options": [o.__dict__ for o in c.options]}


def _cand_from_json(d: dict) -> Candidate:
    return Candidate(d["pcode"], d["name"], d.get("price"), d.get("malls"), d.get("image", ""),
                     [Option(**o) for o in d.get("options", [])], set(d.get("variant", [])))


class Run:
    def __init__(self, root, cfg: dict, now: datetime, client: PoliteClient, log=print):
        self.root, self.cfg, self.now, self.client, self.log = root, cfg, now, client, log
        self.now_iso = now.isoformat()
        self.state = store.read_json(root / "data" / "state.json", {})
        for k in ("sources", "danawa", "searchCache", "keywords"):
            self.state.setdefault(k, {})
        self.community = store.read_json(root / "data" / "community.json", {}).get("posts", {})
        self.products = store.read_json(root / "data" / "products.json", {})
        self.history_rows: list[dict] = []
        self.summary = {"at": self.now_iso, "sources": {}, "verified": 0, "nomatch": 0, "pages": 0, "searches": 0,
                        "newPosts": 0, "keywordsRefreshed": 0, "stoppedBy": None}
        d = cfg["danawa"]
        self.dn_delay = float(d.get("min_delay_seconds", 10))
        client.host_delay["search.danawa.com"] = self.dn_delay
        client.host_delay["prod.danawa.com"] = self.dn_delay
        self.danawa_ok = bool(d.get("enabled", True))
        dst = self.state["danawa"]
        until = store.parse_time(dst.get("blockedUntil"))
        if until and until > now:
            self.danawa_ok = False
            dst["status"] = "blocked"

    # ── 1. 커뮤니티 ─────────────────────────────────────────
    def fetch_community(self) -> None:
        for sid, enabled in self.cfg["sources"].items():
            src = SOURCE_BY_ID[sid]
            st = self.state["sources"].setdefault(sid, {})
            if not enabled:
                st.update(status="off", message="설정에서 꺼 두었어요")
                continue
            until = store.parse_time(st.get("retryAfter"))
            if until and until > self.now:
                continue
            try:
                resp = self.client.get(src.url)
                raws = src.parser(resp.text())
            except RobotsDisallowed:
                st.update(status="robots", message="사이트의 robots.txt가 자동 수집을 허용하지 않아 읽지 않아요",
                          retryAfter=(self.now + timedelta(hours=24)).isoformat())
                continue
            except RobotsUnavailable:
                st.update(status="unavailable", message="robots.txt를 확인할 수 없어 이번에는 건너뛰었어요")
                continue
            except Blocked as e:
                st.update(status="blocked", message="사이트가 자동 접속을 막았어요. 6시간 뒤 다시 시도해요",
                          retryAfter=(self.now + timedelta(hours=6)).isoformat(), detail=str(e)[:200])
                continue
            except BudgetExceeded:
                self.summary["stoppedBy"] = "budget"
                return
            except FetchError as e:
                st.update(status="error", message="접속에 실패했어요", detail=str(e)[:200])
                continue
            except Exception as e:  # 파서 오류 하나가 전체 실행을 멈추지 않게
                st.update(status="parse_error", message="받은 내용을 읽지 못했어요 (형식이 바뀐 것 같아요)",
                          detail=f"{type(e).__name__}: {e}"[:200])
                continue
            if not raws:
                st.update(status="empty", message="글을 하나도 찾지 못했어요. 게시판 구조가 바뀐 것 같아요")
                continue
            st.update(status="ok", message="", lastOk=self.now_iso, count=len(raws), retryAfter=None, detail=None)
            for raw in raws:
                self._merge_post(raw)
        self._prune_posts()

    def _merge_post(self, raw) -> None:
        key = f"{raw.source}:{raw.id}"
        post = self.community.get(key)
        if post:
            for f in ("votes", "views", "comments"):
                v = getattr(raw, f)
                if v is not None:
                    post[f] = v
            if raw.ended and not post.get("ended"):
                post["ended"] = True
                post["endedAt"] = self.now_iso
            return
        tp = parse_title(raw.title, mall=raw.mall, price=raw.price, shipping=raw.shipping)
        cat = classify(self.cfg, f"{raw.category} {tp.name}")
        post = {
            "key": key, "source": raw.source, "id": raw.id, "url": raw.url, "title": tp.raw, "author": raw.author,
            "postedAt": raw.posted_at or self.now_iso, "firstSeen": self.now_iso, "category": raw.category, "cat": cat,
            "mall": tp.mall, "price": tp.price, "currency": tp.currency, "shipping": tp.shipping, "shippingFee": tp.shipping_fee,
            "name": tp.name, "votes": raw.votes, "views": raw.views, "comments": raw.comments,
            "ended": bool(raw.ended or tp.ended), "flags": tp.flags, "verify": {"status": "pending"}, "tries": 0,
        }
        reason = None
        if "non_product" in tp.flags:
            reason = "non_product"
        elif "digital" in tp.flags:
            reason = "digital"
        elif "overseas" in tp.flags and not self.cfg["community"].get("verify_overseas"):
            reason = "overseas"
        elif "free" in tp.flags:
            reason = "free"
        elif tp.price is None or tp.currency != "KRW":
            reason = "noprice"
        elif tp.multi_price:
            reason = "multi"
        if reason:
            post["verify"] = {"status": "skipped", "reason": SKIP_REASONS[reason], "code": reason}
        self.community[key] = post
        self.summary["newPosts"] += 1

    def _prune_posts(self) -> None:
        keep = timedelta(hours=float(self.cfg["community"].get("keep_hours", 72)))
        for key in [k for k, p in self.community.items()
                    if self.now - (store.parse_time(p.get("firstSeen")) or self.now) > keep]:
            del self.community[key]
        cap = int(self.cfg["community"].get("max_posts", 500))
        if len(self.community) > cap:
            ordered = sorted(self.community.values(), key=lambda p: p.get("firstSeen", ""), reverse=True)
            self.community = {p["key"]: p for p in ordered[:cap]}

    # ── 다나와 공통 ─────────────────────────────────────────
    def _danawa_fail(self, e: Exception) -> None:
        dst = self.state["danawa"]
        if isinstance(e, Blocked):
            self.danawa_ok = False
            dst.update(status="blocked", message="다나와가 자동 접속을 막았어요. 6시간 뒤 다시 시도해요",
                       blockedUntil=(self.now + timedelta(hours=6)).isoformat(), detail=str(e)[:200])
        elif isinstance(e, (RobotsDisallowed, RobotsUnavailable)):
            self.danawa_ok = False
            dst.update(status="robots", message="다나와 robots.txt가 이 경로를 허용하지 않거나 확인할 수 없어 멈췄어요", detail=str(e)[:200])
        elif isinstance(e, BudgetExceeded):
            self.summary["stoppedBy"] = "budget"
            self.danawa_ok = False
        else:
            dst.update(status="error", message="다나와 접속에 실패했어요", detail=str(e)[:200])

    def search(self, query: str) -> list[Candidate]:
        cache = self.state["searchCache"].get(query)
        hours = float(self.cfg["danawa"].get("search_cache_hours", 6))
        if cache and (t := store.parse_time(cache.get("at"))) and self.now - t < timedelta(hours=hours):
            return [_cand_from_json(c) for c in cache.get("items", [])]
        resp = self.client.get(danawa.search_url(query))
        self.summary["searches"] += 1
        cands, strategy = danawa.parse_search(resp.text())
        dst = self.state["danawa"]
        dst["searchStrategy"] = strategy
        if strategy == "none":
            dst["searchEmpty"] = dst.get("searchEmpty", 0) + 1
        else:
            dst["searchEmpty"] = 0
        self.state["searchCache"][query] = {"at": self.now_iso, "items": [_cand_to_json(c) for c in cands[:12]]}
        return cands

    def product_page(self, pcode: str) -> danawa.ProductPage:
        resp = self.client.get(danawa.product_url(pcode))
        self.summary["pages"] += 1
        page = danawa.parse_product(resp.text(), pcode)
        dst = self.state["danawa"]
        dst["productStrategy"] = page.strategy
        dst["productEmpty"] = 0 if page.offers else dst.get("productEmpty", 0) + 1
        dst.update(status="ok", message="", lastOk=self.now_iso)
        self._store_page(page)
        return page

    def _store_page(self, page: danawa.ProductPage) -> None:
        p = self.products.setdefault(page.pcode, {"pcode": page.pcode, "addedAt": self.now_iso, "from": "community"})
        pr = self.cfg["pricing"]
        md = market_deal(page, min_malls=int(pr.get("min_malls_for_average", 3)),
                         low=float(pr.get("outlier_low", 0.5)), high=float(pr.get("outlier_high", 1.8)))
        prev = p.get("market") or {}
        if md:
            # 같은 쇼핑몰이 계속 가장 싸고 할인 폭이 크게 바뀌지 않았으면 '처음 발견한 때'를 유지한다
            same = prev.get("bestMall") == md["bestMall"] and md["rate"] - float(prev.get("rate") or 0) < 10
            md["since"] = prev.get("since", self.now_iso) if same else self.now_iso
        p.update({
            "name": page.name or p.get("name", ""), "image": page.image or p.get("image", ""),
            "lowest": page.lowest, "highest": page.highest, "offerCount": page.offer_count,
            "offers": [{"mall": o.mall, "price": o.price, "shipping": o.shipping, "fee": o.shipping_fee}
                       for o in page.offers[:24]],
            "market": md, "strategy": page.strategy, "lastChecked": self.now_iso, "fails": 0,
        })
        if page.lowest:
            avg = (md or {}).get("average")
            self.history_rows.append({"date": self.now.date().isoformat(), "time": self.now.strftime("%H:%M"),
                                      "pcode": page.pcode, "lowest": page.lowest, "average": avg or "",
                                      "malls": len(page.offers), "best_mall": page.offers[0].mall if page.offers else ""})

    # ── 2. 커뮤니티 딜 검증 ──────────────────────────────────
    def verify_posts(self) -> None:
        limit = int(self.cfg["danawa"].get("max_verifications_per_run", 15))
        min_score = float(self.cfg["pricing"].get("match_min_score", 0.62))
        min_malls = int(self.cfg["pricing"].get("min_malls_for_average", 3))
        susp = float(self.cfg["pricing"].get("suspicious_rate", 60))
        queue = [p for p in self.community.values() if p["verify"].get("status") == "pending"
                 or (p["verify"].get("status") == "error" and p.get("tries", 0) < 3)]
        queue.sort(key=lambda p: p.get("postedAt", ""), reverse=True)
        done = 0
        for post in queue:
            if not self.danawa_ok or done >= limit:
                break
            if post.get("ended"):
                post["verify"] = {"status": "skipped", "reason": SKIP_REASONS["ended"], "code": "ended"}
                continue
            q = search_query(post["name"])
            if len(q) < 2:
                post["verify"] = {"status": "skipped", "reason": "상품명을 알아보기 어려워요", "code": "noname"}
                continue
            post["tries"] = post.get("tries", 0) + 1
            try:
                cands = self.search(q)
                m = match_deal(post["name"], post["price"], cands, min_score=min_score)
                if m is None:
                    post["verify"] = {"status": "nomatch", "reason": "다나와에서 같은 제품을 찾지 못했어요", "query": q,
                                      "checkedAt": self.now_iso}
                    self.summary["nomatch"] += 1
                    done += 1
                    continue
                cached = self.products.get(m.pcode)
                fresh = cached and (t := store.parse_time(cached.get("lastChecked"))) and self.now - t < timedelta(hours=1)
                if fresh:
                    page = danawa.ProductPage(m.pcode, cached.get("name", ""), cached.get("lowest"), cached.get("highest"),
                                              cached.get("offerCount"), cached.get("image", ""), "",
                                              [danawa.Offer(o["mall"], o["price"], o.get("shipping", ""), o.get("fee"))
                                               for o in cached.get("offers", [])])
                else:
                    page = self.product_page(m.pcode)
                v = verify_deal(post["price"], post.get("shippingFee"), post.get("mall", ""), page, m,
                                min_malls=min_malls, suspicious_rate=susp,
                                low=float(self.cfg["pricing"].get("outlier_low", 0.5)),
                                high=float(self.cfg["pricing"].get("outlier_high", 1.8)))
                v["checkedAt"] = self.now_iso
                v["query"] = q
                post["verify"] = v
                if post["cat"] == "etc":  # 제목만으로 제품군을 몰랐으면 다나와 상품명·분류로 다시 판단
                    post["cat"] = classify(self.cfg, f"{v.get('danawaName', '')} {page.category}")
                prod = self.products.setdefault(m.pcode, {"pcode": m.pcode, "addedAt": self.now_iso, "from": "community"})
                if prod.get("cat") in (None, "etc"):
                    prod["cat"] = post["cat"]
                prod["linkedAt"] = self.now_iso
                if v["status"] == "ok":
                    self.summary["verified"] += 1
                done += 1
            except FetchError as e:
                post["verify"] = {"status": "error", "reason": "다나와 확인 중 오류가 났어요. 다음 실행 때 다시 해요",
                                  "checkedAt": self.now_iso}
                self._danawa_fail(e)
                if not self.danawa_ok:
                    post["tries"] -= 1
                    post["verify"] = {"status": "pending"}
                    break
            except Exception as e:  # 다나와 화면이 바뀌어 해석이 깨져도 다른 글은 계속
                post["verify"] = {"status": "error", "reason": "다나와 페이지를 해석하지 못했어요. 다음 실행 때 다시 해요",
                                  "checkedAt": self.now_iso}
                self.state["danawa"]["detail"] = f"{type(e).__name__}: {e}"[:200]
                self.log(f"  ! 검증 실패 {post['key']}: {type(e).__name__}: {e}")
                done += 1

    # ── 3. 키워드로 지켜볼 제품 고르기 ─────────────────────────
    def refresh_keywords(self) -> None:
        d = self.cfg["danawa"]
        every = timedelta(hours=float(d.get("keyword_refresh_hours", 24)))
        per_kw = int(d.get("products_per_keyword", 6))
        todo = []
        for c in self.cfg["categories"]:
            for kw in c.get("keywords", []):
                last = store.parse_time(self.state["keywords"].get(kw))
                if not last or self.now - last >= every:
                    todo.append((last or datetime.min.replace(tzinfo=store.KST), c["id"], kw))
        todo.sort()
        for _, cat, kw in todo[: int(d.get("keywords_per_run", 4))]:
            if not self.danawa_ok:
                return
            try:
                cands = self.search(kw)
            except FetchError as e:
                self._danawa_fail(e)
                return
            added = 0
            for c in cands:
                if c.variant or not c.price or added >= per_kw:
                    continue
                p = self.products.setdefault(c.pcode, {"pcode": c.pcode, "addedAt": self.now_iso, "from": "keyword"})
                if p.get("cat") in (None, "etc"):
                    p["cat"] = cat
                p.setdefault("keyword", kw)
                p["name"] = p.get("name") or c.name
                p["image"] = p.get("image") or c.image
                p["seenAt"] = self.now_iso
                added += 1
            self.state["keywords"][kw] = self.now_iso
            self.summary["keywordsRefreshed"] += 1

    # ── 찜 추적 목록 (config.json의 watch) ─────────────────────
    def ensure_watch(self) -> None:
        watch = {str(x) for x in self.cfg.get("watch", [])}
        for pcode in watch:
            p = self.products.setdefault(pcode, {"pcode": pcode, "addedAt": self.now_iso, "from": "watch"})
            p["watch"] = True
            p["seenAt"] = self.now_iso
        for p in self.products.values():
            if p.get("watch") and p["pcode"] not in watch:
                p["watch"] = False

    # ── 4. 제품 페이지 돌아가며 확인 ──────────────────────────
    def scan_products(self) -> None:
        d = self.cfg["danawa"]
        recheck = timedelta(hours=float(d.get("recheck_hours", 6)))
        due = []
        for p in self.products.values():
            last = store.parse_time(p.get("lastChecked"))
            if last and self.now - last < recheck:
                continue
            linked = store.parse_time(p.get("linkedAt"))
            recent_link = linked and self.now - linked < timedelta(hours=48)
            priority = 0 if not last else (1 if recent_link or p.get("watch") else 2)
            due.append((priority, p.get("lastChecked") or "", p["pcode"]))
        due.sort()
        for _, _, pcode in due[: int(d.get("max_product_pages_per_run", 25))]:
            if not self.danawa_ok:
                return
            try:
                self.product_page(pcode)
            except (Blocked, RobotsDisallowed, RobotsUnavailable, BudgetExceeded) as e:
                self._danawa_fail(e)
                return
            except Exception as e:  # 접속 실패·해석 실패 모두 이 제품만 건너뛴다
                p = self.products[pcode]
                p["fails"] = p.get("fails", 0) + 1
                p["lastChecked"] = self.now_iso
                if p["fails"] >= 3 and p.get("from") == "keyword" and not p.get("watch"):
                    del self.products[pcode]
                self.log(f"  ! 다나와 상품 {pcode}: {e}")

    def _prune_products(self) -> None:
        cap = int(self.cfg["danawa"].get("max_products", 400))
        stale = timedelta(days=14)
        for pcode in list(self.products):
            p = self.products[pcode]
            seen = store.parse_time(p.get("seenAt") or p.get("linkedAt") or p.get("addedAt"))
            if seen and self.now - seen > stale and not p.get("watch"):
                del self.products[pcode]
        if len(self.products) > cap:
            ordered = sorted(self.products.values(), key=lambda p: (bool(p.get("watch")), p.get("linkedAt") or p.get("seenAt") or ""),
                             reverse=True)
            self.products = {p["pcode"]: p for p in ordered[:cap]}

    # ── 저장 ────────────────────────────────────────────────
    def save(self) -> None:
        self._prune_products()
        # 검색 캐시는 오래된 것부터 정리
        cache = self.state["searchCache"]
        hours = float(self.cfg["danawa"].get("search_cache_hours", 6))
        for q in [q for q, v in cache.items() if (t := store.parse_time(v.get("at"))) is None or self.now - t > timedelta(hours=hours * 4)]:
            del cache[q]
        self.state["danawa"]["requests"] = self.client.requests
        self.state["lastRun"] = self.now_iso
        store.write_json(self.root / "data" / "state.json", self.state)
        store.write_json(self.root / "data" / "community.json", {"posts": self.community})
        store.write_json(self.root / "data" / "products.json", self.products)
        if self.history_rows:
            store.append_history(self.root, self.history_rows)
        store.prune_history(self.root, self.now.date(), int(self.cfg["storage"].get("history_days", 120)))
        self.summary["sources"] = {k: v.get("status") for k, v in self.state["sources"].items()}
        self.summary["danawa"] = self.state["danawa"].get("status")
        self.summary["requests"] = self.client.requests
        store.write_json(self.root / "data" / "last_run.json", self.summary)

    def run(self) -> dict:
        self.fetch_community()
        if self.danawa_ok:
            self.verify_posts()
        self.ensure_watch()
        if self.danawa_ok:
            self.refresh_keywords()
        if self.danawa_ok:
            self.scan_products()
        self.save()
        return self.summary


def make_client(cfg: dict, *, transport=None, sleep=time.sleep, clock=time.monotonic) -> PoliteClient:
    budget = float(cfg["run"].get("budget_minutes", 12)) * 60
    return PoliteClient(default_delay=float(cfg["run"].get("default_delay_seconds", 3)), deadline=clock() + budget,
                        transport=transport, sleep=sleep, clock=clock)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="커뮤니티 핫딜 수집과 다나와 가격 비교")
    ap.add_argument("--root")
    ap.add_argument("--config")
    ap.add_argument("--mock", action="store_true", help="가짜 사이트로 실행 (네트워크 없이)")
    ap.add_argument("--now", help="실행 시각 지정 (테스트용)")
    args = ap.parse_args(argv)
    root = store.repo_root(args.root)
    try:
        cfg = store.load_config(root, args.config)
    except store.ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        return 2
    now = store.parse_time(args.now) if args.now else store.now_kst()
    if args.mock:
        from .mock import MockWeb
        web = MockWeb(now)
        client = make_client(cfg, transport=web.transport, sleep=web.sleep, clock=web.clock)
    else:
        client = make_client(cfg)
    print(f"[수집] {now:%Y-%m-%d %H:%M} KST 시작")
    summary = Run(root, cfg, now, client).run()
    names = {k: SOURCE_BY_ID[k].name + ("(해외)" if k.endswith("overseas") else "") for k in summary["sources"]}
    print("[수집] 게시판: " + ", ".join(f"{names[k]} {v}" for k, v in summary["sources"].items()))
    print(f"[수집] 새 글 {summary['newPosts']}개 · 검증 {summary['verified']}개 · 같은 제품 못 찾음 {summary['nomatch']}개 · "
          f"다나와 검색 {summary['searches']}회 · 상품 페이지 {summary['pages']}회 · 다나와 상태 {summary['danawa'] or '-'} · "
          f"요청 {summary['requests']}회" + (" · 시간 예산 소진" if summary["stoppedBy"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
