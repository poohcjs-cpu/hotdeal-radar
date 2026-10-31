"""같은 제품 찾기와 쇼핑몰 평균가 계산.

핫딜 기준 (사용자 정의)
  쇼핑몰 평균가 = 다나와에 올라온 쇼핑몰별 가격의 평균 (이상치 제외)
  할인율       = (쇼핑몰 평균가 − 이 딜 가격) ÷ 쇼핑몰 평균가
커뮤니티 딜은 그 딜을 올린 쇼핑몰을 뺀 '다른 쇼핑몰들'의 평균과 비교한다.
쇼핑몰 가격이 3곳보다 적게 읽히면 평균 대신 다나와 최저가와 비교하고, 그 사실을 함께 기록한다
(최저가는 평균보다 낮으므로 이 경우 할인율은 더 보수적으로 나온다).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .danawa import Candidate, Option, ProductPage
from .textnorm import is_variant_marker, model_tokens, normalize_mall, quantities, words


# ── 문자열 유사도 ───────────────────────────────────────────

def _bigrams(text: str) -> set[str]:
    s = "".join(ch for ch in (text or "").lower() if ch.isalnum())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def coverage(deal: str, cand: str) -> float:
    """딜 상품명의 단어(또는 글자쌍)가 후보 이름에 얼마나 들어 있는지 (0~1)."""
    dw, cw = words(deal), words(cand)
    word_cov = len(dw & cw) / len(dw) if dw else 0.0
    db, cb = _bigrams(deal), _bigrams(cand)
    bigram_cov = len(db & cb) / len(db) if db else 0.0
    return max(word_cov, bigram_cov * 0.95)


def similarity(deal: str, cand: str) -> float:
    """양쪽 방향을 함께 본다. 커뮤니티 제목은 '무선 마우스'처럼 설명을 덧붙이는 일이 많아서,
    다나와 상품명의 단어가 모두 딜 제목에 들어 있으면(상품명이 충분히 구체적일 때) 높게 친다."""
    forward = coverage(deal, cand)
    backward = coverage(cand, deal)
    weight = 0.9 if len(words(cand)) >= 3 else 0.7
    return max(forward, backward * weight)


def _close(a: float, b: float, tol: float = 0.03) -> bool:
    return abs(a - b) <= max(a, b) * tol


@dataclass
class Match:
    candidate: Candidate
    option: Option | None
    pcode: str
    score: float
    unit_ratio: float = 1.0   # 딜 수량 ÷ 다나와 수량 (개당 가격으로 맞출 때 쓰는 비율)
    reason: str = ""


def match_deal(name: str, price: int | None, candidates: list[Candidate], *, min_score: float = 0.62) -> Match | None:
    """커뮤니티 딜 상품명과 다나와 검색 결과에서 같은 제품을 고른다. 확신이 없으면 None."""
    dq = quantities(name)
    dm = model_tokens(name)
    dv = is_variant_marker(name)
    best: Match | None = None
    for cand in candidates[:12]:
        if cand.variant - dv:
            continue  # 해외구매·중고·벌크 같은 다른 구성
        cm = model_tokens(cand.name)
        if dm:
            if dm & cm:
                model = 1.0 if dm <= cm else 0.75
            elif cm:
                continue  # 모델 번호가 서로 다르면 다른 제품
            else:
                model = 0.35
        else:
            model = 0.6
        cov = similarity(name, cand.name)
        if cov < 0.45:
            continue

        # 수량·용량: 옵션 줄에서 같은 수량을 먼저 찾고, 없으면 개당 가격으로 맞춘다
        option, unit_ratio, qty_ok = None, 1.0, True
        cq = quantities(cand.name)
        for key in ("ml", "g", "gb", "inch"):
            if key in dq and key in cq and not _close(dq[key], cq[key]):
                qty_ok = False
        if not qty_ok:
            continue
        ref_price = cand.price
        options = [o for o in cand.options if o.price and not (is_variant_marker(o.label) - dv)]
        if cand.options and not options:
            continue  # 남은 옵션이 모두 벌크·병행 같은 다른 구성
        if options:
            def fits(o, keys):
                oq = quantities(o.label)
                return all(_close(oq.get(k, cq.get(k, dq[k])), dq[k]) for k in keys if k in dq)
            sizes = ("ml", "g", "gb", "inch")
            same = [o for o in options if fits(o, sizes + ("count",))]
            if not same and any(k in dq for k in sizes):
                same = [o for o in options if fits(o, sizes)]
                if not same:
                    continue  # 용량이 맞는 옵션이 없다
            option = min(same or options, key=lambda o: o.price)
            ref_price = option.price
            cand_count = quantities(option.label).get("count", cq.get("count"))
            if "count" in dq and cand_count and not _close(dq["count"], cand_count):
                unit_ratio = dq["count"] / cand_count
        elif "count" in dq and "count" in cq and not _close(dq["count"], cq["count"]):
            unit_ratio = dq["count"] / cq["count"]

        price_score = 0.5
        if price and ref_price:
            ratio = price / (ref_price * unit_ratio)
            if ratio < 0.25:
                continue  # 딜이 너무 싸면 본품이 아니라 부속품·소모품 딜일 가능성이 높다
            # 딜이 다나와 최저가보다 많이 비싸면 최저가 쪽이 오등록일 수도 있어 바로 버리지 않고 점수만 낮춘다
            # (상품 페이지를 읽은 뒤 verify_deal이 평균가와 다시 비교해 걸러 낸다)
            price_score = 1.0 if 0.45 <= ratio <= 1.25 else (0.4 if ratio <= 2.0 else 0.0)
        score = 0.55 * cov + 0.3 * model + 0.15 * price_score
        if unit_ratio != 1.0:
            score -= 0.05
        if best is None or score > best.score:
            best = Match(cand, option, (option.pcode if option else cand.pcode), round(score, 3), unit_ratio,
                         "개당 가격 비교" if unit_ratio != 1.0 else "")
    if best is None or best.score < min_score:
        return None
    return best


# ── 쇼핑몰 가격 통계 ────────────────────────────────────────

def _value(o, with_shipping: bool) -> int:
    if with_shipping and getattr(o, "shipping_fee", None) is not None:
        return o.price + o.shipping_fee
    return o.price


def shipping_known(offers) -> bool:
    """판매처 대부분(60% 이상)의 배송비를 알 때만 배송비 포함 금액으로 비교한다."""
    offers = [o for o in offers if o.price]
    return bool(offers) and sum(1 for o in offers if getattr(o, "shipping_fee", None) is not None) >= 0.6 * len(offers)


def offer_stats(offers, *, exclude_mall: str | None = None, low: float = 0.5, high: float = 1.8,
                with_shipping: bool = False) -> dict:
    ex = normalize_mall(exclude_mall) if exclude_mall else None
    prices = [(o.mall, _value(o, with_shipping)) for o in offers if o.price and (not ex or o.mall != ex)]
    if not prices:
        return {"count": 0}
    med = statistics.median(p for _, p in prices)
    kept = [(m, p) for m, p in prices if low * med <= p <= high * med]
    if not kept:
        kept = prices
    lowest_mall, lowest = min(kept, key=lambda x: x[1])
    return {
        "count": len(kept),
        "dropped": len(prices) - len(kept),
        "lowest": lowest,
        "lowestMall": lowest_mall,
        "average": int(round(statistics.fmean(p for _, p in kept) / 10.0)) * 10,
        "median": int(med),
        "highest": max(p for _, p in kept),
    }


def verify_deal(price: int, shipping_fee: int | None, mall: str, page: ProductPage, match: Match, *,
                min_malls: int = 3, suspicious_rate: float = 60, low: float = 0.5, high: float = 1.8) -> dict:
    """커뮤니티 딜 하나를 다나와 쇼핑몰 가격과 비교한다.

    양쪽 배송비를 모두 알면 '배송비 포함 금액'끼리, 아니면 '상품 가격'끼리 비교한다 (basis).
    """
    with_ship = shipping_fee is not None and shipping_known(page.offers)
    total = price + (shipping_fee or 0) if with_ship else price
    stats = offer_stats(page.offers, exclude_mall=mall, low=low, high=high, with_shipping=with_ship)
    ratio = match.unit_ratio or 1.0
    if stats.get("count", 0) >= min_malls:
        reference, ref_kind = stats["average"] * ratio, "average"
    elif page.lowest or stats.get("lowest"):
        reference, ref_kind = (stats.get("lowest") or page.lowest) * ratio, "lowest"
    else:
        return {"status": "noprice", "reason": "다나와에서 가격을 읽지 못했어요", "pcode": page.pcode}
    rate = (reference - total) / reference * 100
    if total > reference * 1.8:
        return {"status": "nomatch", "reason": "다나와 가격과 차이가 너무 커서 다른 제품으로 판단했어요", "pcode": page.pcode,
                "referencePrice": int(round(reference / 10.0)) * 10}
    return {
        "status": "ok",
        "pcode": page.pcode,
        "danawaName": page.name or match.candidate.name,
        "matchScore": match.score,
        "unitAdjusted": ratio != 1.0,
        "reference": ref_kind,
        "referencePrice": int(round(reference / 10.0)) * 10,
        "average": int(round(stats["average"] * ratio / 10.0)) * 10 if stats.get("average") else None,
        "lowest": int(round((stats.get("lowest") or page.lowest) * ratio / 10.0)) * 10 if (stats.get("lowest") or page.lowest) else None,
        "lowestMall": stats.get("lowestMall"),
        "malls": stats.get("count", 0),
        "offerCount": page.offer_count,
        "dealTotal": total,
        "basis": "total" if with_ship else "price",
        "rate": round(rate, 1),
        "suspicious": rate >= suspicious_rate,
    }


def market_deal(page: ProductPage, *, min_malls: int = 3, low: float = 0.5, high: float = 1.8) -> dict | None:
    """자체 발굴: 가장 싼 쇼핑몰이 다른 쇼핑몰들의 평균보다 얼마나 싼가.

    가장 싼 값이 다른 곳들의 중간값보다 터무니없이 낮으면(이상치) 다른 제품·옵션일 가능성이 커서
    그 판매처는 건너뛰고 그다음 판매처를 '최저'로 본다.
    """
    if len(page.offers) < min_malls:
        return None
    with_ship = shipping_known(page.offers)
    ranked = sorted(page.offers, key=lambda o: _value(o, with_ship))
    skipped = 0
    while len(ranked) - skipped >= min_malls:
        best = ranked[skipped]
        others = offer_stats(ranked[skipped + 1:], low=low, high=high, with_shipping=with_ship)
        if others.get("count", 0) < max(2, min_malls - 1):
            return None
        if _value(best, with_ship) >= low * others["median"]:
            break
        skipped += 1
    else:
        return None
    allstats = offer_stats(ranked[skipped:], low=low, high=high, with_shipping=with_ship)
    best_value = _value(best, with_ship)
    rate = (others["average"] - best_value) / others["average"] * 100
    return {
        "bestMall": best.mall,
        "bestPrice": best_value,
        "bestShipping": best.shipping,
        "othersAverage": others["average"],
        "average": allstats["average"],
        "secondPrice": _value(ranked[skipped + 1], with_ship),
        "malls": allstats["count"],
        "basis": "total" if with_ship else "price",
        "skippedOutliers": skipped,
        "rate": round(rate, 1),
    }
