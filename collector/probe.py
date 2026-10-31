"""사이트 구조 점검 도구.

사이트 화면이 바뀌면 파서가 0건을 돌려준다. 이 도구로 지금 어떻게 읽히는지 바로 확인할 수 있다.
받은 원본 HTML은 data/debug/ 에 저장되므로, 파서를 고칠 때 그대로 쓰면 된다.

  python -m collector.probe all                       # 켜 둔 게시판 전부 + 다나와 검색·상품 페이지 한 번씩
  python -m collector.probe source ppomppu            # 게시판 하나
  python -m collector.probe search "갤럭시 버즈3 프로"   # 다나와 검색
  python -m collector.probe product 12345678          # 다나와 상품 페이지 (pcode)
  python -m collector.probe match "[쿠팡] 삼성 990 PRO 2TB (189,000원/무료)"   # 커뮤니티 제목 하나를 끝까지 검증
  python -m collector.probe product 12345678 --file 저장한페이지.html    # 네트워크 없이 저장된 파일로

모든 요청은 수집기와 같은 규칙(robots.txt, 요청 간격, 차단 시 중단)을 따른다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import danawa, store
from .community import SOURCE_BY_ID
from .compare import market_deal, match_deal, verify_deal
from .http import FetchError, PoliteClient
from .textnorm import parse_title, search_query


def _slug(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", s)[:40].strip("_") or "page"


class Probe:
    def __init__(self, root: Path, cfg: dict, client: PoliteClient | None = None, out=print):
        self.root, self.cfg, self.out = root, cfg, out
        self.client = client or PoliteClient(default_delay=float(cfg["run"].get("default_delay_seconds", 3)))
        delay = float(cfg["danawa"].get("min_delay_seconds", 10))
        self.client.host_delay["search.danawa.com"] = delay
        self.client.host_delay["prod.danawa.com"] = delay
        self.debug = root / "data" / "debug"
        self.report: dict = {}

    def _fetch(self, url: str, name: str, file: str | None = None) -> str | None:
        if file:
            return Path(file).read_text(encoding="utf-8", errors="replace")
        try:
            resp = self.client.get(url)
        except FetchError as e:
            self.out(f"  ✗ {type(e).__name__}: {e}")
            self.report[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return None
        text = resp.text()
        self.debug.mkdir(parents=True, exist_ok=True)
        (self.debug / f"{name}.html").write_text(text, encoding="utf-8")
        self.out(f"  ✓ HTTP {resp.status} · {len(resp.body):,} bytes → data/debug/{name}.html")
        return text

    def source(self, sid: str, file: str | None = None) -> list:
        src = SOURCE_BY_ID[sid]
        self.out(f"\n[{src.name} {src.board}] {src.url}")
        text = self._fetch(src.url, f"source_{sid}", file)
        if text is None:
            return []
        try:
            posts = src.parser(text)
        except Exception as e:  # 점검 도구이므로 어떤 오류든 보여 준다
            self.out(f"  ✗ 해석 실패: {type(e).__name__}: {e}")
            self.report[f"source_{sid}"] = {"ok": False, "error": str(e)}
            return []
        self.out(f"  글 {len(posts)}개를 읽었어요" + ("" if posts else "  ← 0개면 게시판 구조가 바뀐 거예요"))
        for p in posts[:6]:
            tp = parse_title(p.title, mall=p.mall, price=p.price, shipping=p.shipping)
            price = f"{tp.price:,}" if tp.price and tp.currency == "KRW" else (f"${tp.price / 100:.2f}" if tp.price else "?")
            self.out(f"   · {tp.mall or '?':8} {price:>10} {tp.shipping or '':10} {tp.name[:40]}"
                     + (f"  [{','.join(tp.flags)}]" if tp.flags else "") + ("  (종료)" if p.ended or tp.ended else ""))
        self.report[f"source_{sid}"] = {"ok": bool(posts), "count": len(posts)}
        return posts

    def search(self, query: str, file: str | None = None) -> list:
        url = danawa.search_url(query)
        self.out(f"\n[다나와 검색] {query} → {url}")
        text = self._fetch(url, f"search_{_slug(query)}", file)
        if text is None:
            return []
        cands, strategy = danawa.parse_search(text)
        self.out(f"  읽은 방식: {strategy} · 제품 {len(cands)}개" + ("  ← 0개면 검색 화면 구조가 바뀌었을 수 있어요" if not cands else ""))
        for c in cands[:8]:
            opts = ", ".join(f"{o.label or '-'} {o.price:,}" for o in c.options[:3] if o.price)
            self.out(f"   · {c.pcode} {c.name[:42]} | {c.price or 0:,}원 | {c.malls or '?'}몰" + (f" | {opts}" if opts else "")
                     + (f" [{','.join(sorted(c.variant))}]" if c.variant else ""))
        self.report[f"search_{_slug(query)}"] = {"ok": bool(cands), "strategy": strategy, "count": len(cands)}
        return cands

    def product(self, pcode: str, file: str | None = None):
        url = danawa.product_url(pcode)
        self.out(f"\n[다나와 상품] {pcode} → {url}")
        text = self._fetch(url, f"product_{pcode}", file)
        if text is None:
            return None
        page = danawa.parse_product(text, pcode)
        self.out(f"  읽은 방식: {page.strategy} · {page.name[:50]}")
        self.out(f"  최저가 {page.lowest or 0:,}원 · 판매처 수(다나와 표기) {page.offer_count or '?'} · 읽은 판매처 {len(page.offers)}곳")
        for o in page.offers[:12]:
            self.out(f"   · {o.mall:10} {o.price:>11,}원  {o.shipping}")
        md = market_deal(page, min_malls=int(self.cfg["pricing"].get("min_malls_for_average", 3)))
        if md:
            self.out(f"  가장 싼 곳 {md['bestMall']} {md['bestPrice']:,}원 · 다른 곳 평균 {md['othersAverage']:,}원 → {md['rate']}% 저렴")
        elif len(page.offers) < 3:
            self.out("  판매처를 3곳 넘게 읽지 못해 평균가를 낼 수 없어요. (판매처 목록 구조가 바뀌었거나 판매처가 적은 상품)")
        self.report[f"product_{pcode}"] = {"ok": len(page.offers) >= 3, "strategy": page.strategy, "offers": len(page.offers),
                                           "lowest": page.lowest}
        return page

    def match(self, title: str) -> dict | None:
        tp = parse_title(title)
        self.out(f"\n[제목 해석] 쇼핑몰 {tp.mall or '?'} · 가격 {tp.price} {tp.currency} · 배송 {tp.shipping or '?'} · 상품명 '{tp.name}'"
                 + (f" · 표시 {tp.flags}" if tp.flags else ""))
        if not tp.price:
            self.out("  가격을 찾지 못해 검증할 수 없어요.")
            return None
        q = search_query(tp.name)
        cands = self.search(q)
        m = match_deal(tp.name, tp.price, cands, min_score=float(self.cfg["pricing"].get("match_min_score", 0.62)))
        if m is None:
            self.out("  같은 제품을 확신할 수 없어요 (점수 미달).")
            return None
        self.out(f"  같은 제품: {m.candidate.name} (pcode {m.pcode}, 점수 {m.score}{', ' + m.reason if m.reason else ''})")
        page = self.product(m.pcode)
        if page is None:
            return None
        pr = self.cfg["pricing"]
        v = verify_deal(tp.price, tp.shipping_fee, tp.mall, page, m, min_malls=int(pr.get("min_malls_for_average", 3)),
                        suspicious_rate=float(pr.get("suspicious_rate", 60)))
        self.out("  결과: " + json.dumps(v, ensure_ascii=False))
        return v

    def save_report(self) -> None:
        self.debug.mkdir(parents=True, exist_ok=True)
        store.write_json(self.debug / "probe.json", {"at": store.now_kst().isoformat(), "requests": self.client.requests,
                                                     "results": self.report})
        self.out(f"\n점검 결과: data/debug/probe.json (요청 {self.client.requests}회)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="사이트 구조 점검")
    ap.add_argument("what", choices=["all", "source", "search", "product", "match"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--file", help="네트워크 대신 저장된 HTML/RSS 파일을 해석")
    ap.add_argument("--root")
    args = ap.parse_args(argv)
    root = store.repo_root(args.root)
    cfg = store.load_config(root)
    p = Probe(root, cfg)
    if args.what == "source":
        if args.arg not in SOURCE_BY_ID:
            print("게시판 id: " + ", ".join(SOURCE_BY_ID), file=sys.stderr)
            return 2
        p.source(args.arg, args.file)
    elif args.what == "search":
        p.search(args.arg or "갤럭시 버즈3 프로", args.file)
    elif args.what == "product":
        if not args.arg:
            print("pcode를 넣어 주세요", file=sys.stderr)
            return 2
        p.product(args.arg, args.file)
    elif args.what == "match":
        p.match(args.arg or "")
    else:
        for sid, on in cfg["sources"].items():
            if on:
                p.source(sid)
        kw = next((k for c in cfg["categories"] for k in c.get("keywords", [])), "무선 이어폰")
        cands = p.search(kw)
        target = next((c for c in cands if not c.variant), None)
        if target:
            p.product(target.pcode)
    p.save_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
