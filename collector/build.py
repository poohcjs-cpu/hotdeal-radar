"""모은 내용을 앱이 읽는 docs/data/deals.json 하나로 정리한다.

  python -m collector.build

핫딜 판단 기준 = '쇼핑몰 평균가보다 얼마나 싼가'
  커뮤니티 딜   그 딜을 올린 쇼핑몰을 뺀 다른 쇼핑몰들의 평균가(다나와 판매처 목록)와 비교 (compare.verify_deal)
  자체 발굴     다나와 판매처 중 가장 싼 곳을 나머지 쇼핑몰들의 평균가와 비교 (compare.market_deal)
같은 제품·같은 쇼핑몰 딜이 여러 게시판에 올라오면 하나로 합치고, 올라온 게시판을 모두 함께 보여 준다.
앱은 할인율 5% 이상인 딜을 모두 받아, 사용자가 정한 기준(예: 30%)으로 다시 거른다.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

from . import store
from .community import SOURCE_BY_ID
from .danawa import product_url

MIN_RATE_EXPORT = 5.0
HISTORY_DAYS = 60
DANAWA_SOURCE = {"id": "danawa", "name": "다나와", "board": "쇼핑몰 가격비교", "home": "https://www.danawa.com/"}


def alerts_summary(cfg: dict) -> dict:
    a = cfg.get("alerts", {}) or {}
    return {
        "enabled": os.environ.get("ALERTS_ENABLED", "").strip().lower() == "true",
        "threshold": a.get("threshold", 30),
        "categories": a.get("categories", []) or [],
        "keywords": a.get("keywords", []) or [],
        "keywordThreshold": a.get("keyword_threshold", 15),
    }


def _source_list(cfg: dict, state: dict, last: dict) -> list[dict]:
    out = []
    for sid, enabled in cfg["sources"].items():
        src = SOURCE_BY_ID[sid]
        st = state.get("sources", {}).get(sid, {})
        out.append({"id": sid, "name": src.name, "board": src.board, "home": src.home, "kind": src.kind,
                    "enabled": bool(enabled), "status": st.get("status", "waiting" if enabled else "off"),
                    "message": st.get("message", ""), "lastOk": st.get("lastOk"), "count": st.get("count"),
                    "retryAfter": st.get("retryAfter")})
    dn = state.get("danawa", {})
    status = dn.get("status") or ("off" if not cfg["danawa"].get("enabled", True) else "waiting")
    message = dn.get("message", "")
    if status == "ok" and dn.get("searchEmpty", 0) >= 5:
        status, message = "warn", "다나와 검색 결과를 여러 번 연속으로 읽지 못했어요. 화면 구조가 바뀐 것 같아요 (probe로 확인)"
    elif status == "ok" and dn.get("productEmpty", 0) >= 5:
        status, message = "warn", ("다나와 상품 페이지에서 판매처 목록을 여러 번 연속으로 읽지 못했어요. "
                                   "지금은 다나와 최저가로만 비교해요 (probe로 확인)")
    out.append({**DANAWA_SOURCE, "kind": "price", "enabled": bool(cfg["danawa"].get("enabled", True)), "status": status,
                "message": message, "lastOk": dn.get("lastOk"), "count": None,
                "detail": {"search": dn.get("searchStrategy"), "product": dn.get("productStrategy")},
                "retryAfter": dn.get("blockedUntil")})
    return out


def _post_view(p: dict) -> dict:
    v = p.get("verify") or {}
    keep = ("status", "reason", "code", "rate", "reference", "referencePrice", "average", "lowest", "lowestMall", "malls",
            "pcode", "danawaName", "suspicious", "basis", "unitAdjusted", "dealTotal", "checkedAt", "matchScore")
    return {
        "id": p["key"], "source": p["source"], "url": p["url"], "title": p["title"], "name": p.get("name", ""),
        "mall": p.get("mall", ""), "price": p.get("price"), "currency": p.get("currency"), "shipping": p.get("shipping", ""),
        "postedAt": p.get("postedAt"), "firstSeen": p.get("firstSeen"), "cat": p.get("cat", "etc"),
        "board": p.get("category", ""), "votes": p.get("votes"), "views": p.get("views"), "comments": p.get("comments"),
        "ended": bool(p.get("ended")), "flags": p.get("flags", []),
        "verify": {k: v[k] for k in keep if k in v and v[k] is not None},
    }


def build(root, cfg: dict | None = None, now=None) -> dict:
    cfg = cfg or store.load_config(root)
    now = now or store.now_kst()
    state = store.read_json(root / "data" / "state.json", {})
    last = store.read_json(root / "data" / "last_run.json", {})
    posts = store.read_json(root / "data" / "community.json", {}).get("posts", {})
    products = store.read_json(root / "data" / "products.json", {})
    history = store.read_history(root, (now - timedelta(days=HISTORY_DAYS)).date())
    cat_ids = {c["id"] for c in cfg["categories"]}

    # ── 커뮤니티 글 ─────────────────────────────────────────
    post_views = sorted((_post_view(p) for p in posts.values()), key=lambda p: p.get("postedAt") or "", reverse=True)
    by_pcode_posts: dict[str, list] = {}
    for pv in post_views:
        pc = pv["verify"].get("pcode")
        if pc:
            by_pcode_posts.setdefault(pc, []).append(pv["id"])

    # ── 딜 목록 (커뮤니티 검증 + 자체 발굴) ───────────────────
    deals: dict[tuple, dict] = {}
    for pv in sorted(post_views, key=lambda p: p.get("postedAt") or ""):
        v = pv["verify"]
        if v.get("status") != "ok" or v.get("rate") is None or v["rate"] < MIN_RATE_EXPORT:
            continue
        prod = products.get(v["pcode"], {})
        key = (v["pcode"], pv["mall"] or pv["id"])
        mention = {"id": pv["id"], "source": pv["source"], "url": pv["url"], "votes": pv["votes"], "comments": pv["comments"],
                   "postedAt": pv["postedAt"], "ended": pv["ended"]}
        if key in deals:
            d = deals[key]
            d["mentions"].append(mention)
            if pv["price"] and pv["price"] < d["price"]:  # 더 싸게 올라온 글이 있으면 그 가격으로
                d.update(price=pv["price"], shipping=pv["shipping"], rate=v["rate"], url=pv["url"], title=pv["title"],
                         dealTotal=v.get("dealTotal"), referencePrice=v.get("referencePrice"))
            d["ended"] = d["ended"] and pv["ended"]
            continue
        deals[key] = {
            "id": f"c-{pv['source']}-{pv['id'].split(':', 1)[1]}", "kind": "community", "title": pv["title"],
            "name": pv["name"] or v.get("danawaName", ""), "danawaName": v.get("danawaName", ""), "mall": pv["mall"],
            "price": pv["price"], "shipping": pv["shipping"], "url": pv["url"], "source": pv["source"],
            "cat": pv["cat"] if pv["cat"] in cat_ids else (prod.get("cat") or pv["cat"]),
            "postedAt": pv["postedAt"], "detectedAt": pv["firstSeen"], "pcode": v["pcode"],
            "rate": v["rate"], "reference": v.get("reference"), "referencePrice": v.get("referencePrice"), "dealTotal": v.get("dealTotal"),
            "average": v.get("average"), "lowest": v.get("lowest"), "lowestMall": v.get("lowestMall"), "malls": v.get("malls"),
            "basis": v.get("basis"), "unitAdjusted": v.get("unitAdjusted", False), "suspicious": v.get("suspicious", False),
            "ended": pv["ended"], "votes": pv["votes"], "views": pv["views"], "comments": pv["comments"],
            "image": prod.get("image", ""), "mentions": [mention],
        }
    for d in deals.values():
        d["votes"] = max((m["votes"] or 0) for m in d["mentions"]) if any(m["votes"] is not None for m in d["mentions"]) else None
        d["comments"] = sum((m["comments"] or 0) for m in d["mentions"]) if any(m["comments"] is not None for m in d["mentions"]) else None

    for pcode, prod in products.items():
        md = prod.get("market")
        if not md or md.get("rate") is None or md["rate"] < MIN_RATE_EXPORT:
            continue
        key = (pcode, md["bestMall"])
        if key in deals:  # 커뮤니티 딜과 같은 쇼핑몰이면 다나와 목록에서도 확인됐다는 표시만
            deals[key]["listed"] = True
            continue
        deals[key] = {
            "id": f"m-{pcode}", "kind": "market", "title": prod.get("name", ""), "name": prod.get("name", ""),
            "danawaName": prod.get("name", ""), "mall": md["bestMall"], "price": md["bestPrice"],
            "shipping": md.get("bestShipping", ""), "url": product_url(pcode), "source": "danawa",
            "cat": prod.get("cat") or "etc", "postedAt": md.get("since"), "detectedAt": md.get("since"), "pcode": pcode,
            "rate": md["rate"], "reference": "average", "referencePrice": md["othersAverage"], "average": md["othersAverage"],
            "dealTotal": md["bestPrice"],
            "lowest": md["bestPrice"], "lowestMall": md["bestMall"], "malls": md["malls"], "basis": md.get("basis"),
            "secondPrice": md.get("secondPrice"), "unitAdjusted": False,
            "suspicious": md["rate"] >= float(cfg["pricing"].get("suspicious_rate", 60)), "ended": False,
            "votes": None, "views": None, "comments": None, "image": prod.get("image", ""), "mentions": [],
        }
    deal_list = sorted(deals.values(), key=lambda d: (-d["rate"], d["id"]))

    # ── 제품 (검색·상세 화면용) ───────────────────────────────
    need_history = {d["pcode"] for d in deal_list} | set(by_pcode_posts) | {pc for pc, p in products.items() if p.get("watch")}
    product_views = {}
    for pcode, prod in products.items():
        if not prod.get("name"):
            continue
        md = prod.get("market") or {}
        pv = {"pcode": pcode, "name": prod.get("name", ""), "cat": prod.get("cat") or "etc", "image": prod.get("image", ""),
              "lowest": prod.get("lowest"), "average": md.get("average"), "othersAverage": md.get("othersAverage"),
              "malls": len(prod.get("offers", [])) or None, "watch": bool(prod.get("watch")),
              "offerCount": prod.get("offerCount"), "rate": md.get("rate"), "bestMall": md.get("bestMall"),
              "checkedAt": prod.get("lastChecked"), "url": product_url(pcode), "keyword": prod.get("keyword", ""),
              "posts": by_pcode_posts.get(pcode, [])}
        if pcode in need_history:
            pv["offers"] = prod.get("offers", [])
            pv["history"] = [[d.isoformat(), lo, av] for d, lo, av, _ in history.get(pcode, [])]
        product_views[pcode] = pv

    hot = [d for d in deal_list if d["rate"] >= float(cfg["alerts"].get("threshold", 30)) and not d["suspicious"] and not d["ended"]]
    cats = [{k: c.get(k) for k in ("id", "name", "icon", "hue", "keywords")} for c in cfg["categories"]]
    cats.append({"id": "etc", "name": "기타", "icon": "🛒", "hue": 0, "keywords": []})
    out = {
        "version": 2,
        "generatedAt": now.isoformat(),
        "lastRun": last.get("at"),
        "demo": bool(os.environ.get("HOTDEAL_DEMO")),
        "params": {
            "minMalls": cfg["pricing"].get("min_malls_for_average", 3),
            "suspiciousRate": cfg["pricing"].get("suspicious_rate", 60),
            "keepHours": cfg["community"].get("keep_hours", 72),
            "recheckHours": cfg["danawa"].get("recheck_hours", 6),
            "minRate": MIN_RATE_EXPORT,
        },
        "alerts": alerts_summary(cfg),
        "categories": cats,
        "sources": _source_list(cfg, state, last),
        "stats": {
            "posts": len(post_views),
            "verified": sum(1 for p in post_views if p["verify"].get("status") == "ok"),
            "products": len(product_views),
            "deals": len(deal_list),
            "hot": len(hot),
            "requests": last.get("requests"),
        },
        "deals": deal_list,
        "posts": post_views,
        "products": product_views,
    }
    path = root / cfg.get("output", {}).get("path", "docs/data/deals.json")
    store.write_json(path, out, compact=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="앱용 deals.json 만들기")
    ap.add_argument("--root")
    ap.add_argument("--config")
    ap.add_argument("--now")
    args = ap.parse_args(argv)
    root = store.repo_root(args.root)
    try:
        cfg = store.load_config(root, args.config)
    except store.ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        return 2
    now = store.parse_time(args.now) if args.now else store.now_kst()
    out = build(root, cfg, now)
    s = out["stats"]
    print(f"[정리] 커뮤니티 글 {s['posts']}개(검증 {s['verified']}개) · 지켜보는 제품 {s['products']}개 · "
          f"딜 {s['deals']}개 · 기준({cfg['alerts'].get('threshold', 30)}%) 이상 {s['hot']}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
