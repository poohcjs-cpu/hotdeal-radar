"""다나와에서 같은 제품의 쇼핑몰별 가격을 읽는다.

쓰는 페이지 (robots.txt 허용 경로만)
  검색:   https://search.danawa.com/dsearch.php?query=…   (search.danawa.com robots: /dsearch.php 허용, Crawl-delay 10)
  상품:   https://prod.danawa.com/info/?pcode=…           (prod.danawa.com robots: /info/ajax/ 등은 금지, /info/?pcode= 허용)
쇼핑몰 전체 목록을 주는 /info/ajax/… 는 robots.txt가 막아 두었으므로 쓰지 않는다.
그래서 상품 페이지에 처음부터 실려 오는 판매처 목록과 검색엔진용 구조화 데이터(JSON-LD)만 읽는다.

다나와는 화면 구조를 종종 바꾼다. 그래서 파서마다 여러 방식을 차례로 시도하고,
어떤 방식으로 읽었는지(strategy)를 함께 돌려줘 수집 상태 화면과 진단 도구(probe)에서 볼 수 있게 했다.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .textnorm import clean_spaces, is_variant_marker, normalize_mall, parse_shipping

SEARCH_URL = "https://search.danawa.com/dsearch.php?query={q}"
PRODUCT_URL = "https://prod.danawa.com/info/?pcode={pcode}"
_PRICE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,})\s*원")
_MALLS = re.compile(r"(\d[\d,]*)\s*몰")


def search_url(query: str) -> str:
    return SEARCH_URL.format(q=urllib.parse.quote(query))


def product_url(pcode: str) -> str:
    return PRODUCT_URL.format(pcode=urllib.parse.quote(str(pcode)))


def _int(text) -> int | None:
    if text is None:
        return None
    d = re.sub(r"[^\d]", "", str(text))
    return int(d) if d else None


def _pcode(href: str | None) -> str | None:
    if not href:
        return None
    m = re.search(r"[?&](?:pcode|code)=(\d{4,12})", href)
    return m.group(1) if m else None


def _img(el) -> str:
    if el is None:
        return ""
    for attr in ("data-original", "data-src", "src"):
        v = el.get(attr)
        if v and "noImg" not in v and not v.startswith("data:"):
            return ("https:" + v) if v.startswith("//") else v
    return ""


@dataclass
class Option:
    pcode: str
    label: str
    price: int | None
    malls: int | None = None


@dataclass
class Candidate:
    pcode: str
    name: str
    price: int | None
    malls: int | None = None
    image: str = ""
    options: list = field(default_factory=list)
    variant: set = field(default_factory=set)


@dataclass
class Offer:
    mall: str
    price: int
    shipping: str = ""
    shipping_fee: int | None = None


@dataclass
class ProductPage:
    pcode: str
    name: str = ""
    lowest: int | None = None
    highest: int | None = None
    offer_count: int | None = None
    image: str = ""
    category: str = ""
    offers: list = field(default_factory=list)
    strategy: str = ""


# ── 검색 결과 ───────────────────────────────────────────────

def parse_search(html: str) -> tuple[list[Candidate], str]:
    soup = BeautifulSoup(html, "html.parser")
    for strategy, fn in (("legacy", _search_legacy), ("dnw", _search_dnw), ("links", _search_links)):
        found = fn(soup)
        if found:
            return found, strategy
    return [], "none"


def _search_legacy(soup) -> list[Candidate]:
    out = []
    for li in soup.select("li.prod_item"):
        if "ad" in " ".join(li.get("class", [])).split() or li.select_one(".ad_header"):
            continue
        a = li.select_one("p.prod_name a, .prod_name a, a[name=productName]")
        pcode = _pcode(a.get("href")) if a else None
        if not pcode:
            pid = (li.get("id") or "").replace("productItem", "")
            pcode = pid if pid.isdigit() else None
        if not a or not pcode:
            continue
        name = clean_spaces(a.get_text(" ", strip=True))
        price = None
        mp = li.select_one(f"#min_price_{pcode}") or soup.select_one(f"#min_price_{pcode}")
        if mp is not None:
            price = _int(mp.get("value"))
        options = []
        for row in li.select(".prod_pricelist li, .prod_pricelist > ul > li"):
            pa = row.select_one("a[href*=pcode]")
            ptag = row.select_one(".price_sect strong, .price_sect a strong, strong")
            label = row.select_one(".memory_sect, .text__option, .opt")
            op = _pcode(pa.get("href")) if pa else None
            pr = _int(ptag.get_text()) if ptag else None
            mm = _MALLS.search(row.get_text(" ", strip=True))
            if pr:
                options.append(Option(op or pcode, clean_spaces(label.get_text(" ", strip=True)) if label else "", pr,
                                      _int(mm.group(1)) if mm else None))
        if price is None:
            ptag = li.select_one(".price_sect strong, .prod_pricelist strong")
            price = _int(ptag.get_text()) if ptag else (options[0].price if options else None)
        mm = _MALLS.search(li.get_text(" ", strip=True))
        out.append(Candidate(pcode, name, price, _int(mm.group(1)) if mm else None,
                             _img(li.select_one(".thumb_image img, img")), options, is_variant_marker(name)))
    return out


def _search_dnw(soup) -> list[Candidate]:
    out = []
    items = soup.select(".dnw-product-list-item") or [t.find_parent("li") or t.parent
                                                       for t in soup.select('[data-testid="ProductListTitle"]')]
    for item in items:
        if item is None:
            continue
        title = item.select_one('[data-testid="ProductListTitle"] a') or item.select_one("a[aria-label][href*=pcode]")
        if not title:
            continue
        name = clean_spaces(title.get_text(" ", strip=True) or (title.get("aria-label") or "").replace("상세보기", ""))
        pcode = _pcode(title.get("href"))
        options = []
        for row in item.select('[data-testid="ProductListPriceCompare"] li') or item.select("li"):
            link = row.select_one("a[href*=pcode]")
            if not link:
                continue
            txt = link.get("aria-label") or link.get_text(" ", strip=True)
            m = _PRICE.search(txt) or _PRICE.search(row.get_text(" ", strip=True))
            if not m:
                continue
            label = row.select_one(".truncate")
            mm = _MALLS.search(row.get_text(" ", strip=True))
            options.append(Option(_pcode(link.get("href")) or pcode, clean_spaces(label.get_text(" ", strip=True)) if label else "",
                                  _int(m.group(1)), _int(mm.group(1)) if mm else None))
        if not pcode and options:
            pcode = options[0].pcode
        if not pcode or not name:
            continue
        price = min((o.price for o in options if o.price), default=None)
        malls = max((o.malls for o in options if o.malls), default=None)
        out.append(Candidate(pcode, name, price, malls, _img(item.select_one("img")), options, is_variant_marker(name)))
    return out


def _search_links(soup) -> list[Candidate]:
    seen, out = set(), []
    for a in soup.select("a[href*='prod.danawa.com/info/'], a[href*='pcode=']"):
        pcode = _pcode(a.get("href"))
        name = clean_spaces(a.get_text(" ", strip=True))
        if not pcode or pcode in seen or len(name) < 4 or _PRICE.fullmatch(name):
            continue
        box = a.find_parent(["li", "div"])
        m = _PRICE.search(box.get_text(" ", strip=True)) if box else None
        seen.add(pcode)
        out.append(Candidate(pcode, name, _int(m.group(1)) if m else None, None, "", [], is_variant_marker(name)))
    return out[:30]


# ── 상품 페이지 ─────────────────────────────────────────────

def parse_product(html: str, pcode: str) -> ProductPage:
    soup = BeautifulSoup(html, "html.parser")
    page = ProductPage(pcode=str(pcode))
    strategies = []

    ld = _jsonld_product(soup)
    if ld:
        page.name = clean_spaces(ld.get("name") or "")
        img = ld.get("image")
        page.image = (img[0] if isinstance(img, list) and img else img) or ""
        offers = ld.get("offers") or {}
        if isinstance(offers, dict) and offers.get("@type") == "AggregateOffer":
            page.lowest = _int(offers.get("lowPrice"))
            page.highest = _int(offers.get("highPrice"))
            page.offer_count = _int(offers.get("offerCount"))
            strategies.append("jsonld-aggregate")
            offers = offers.get("offers") or []
        if isinstance(offers, dict):
            offers = [offers]
        ld_offers = []
        for o in offers if isinstance(offers, list) else []:
            if not isinstance(o, dict):
                continue
            seller = o.get("seller") or {}
            mall = seller.get("name") if isinstance(seller, dict) else str(seller)
            price = _int(o.get("price") or o.get("lowPrice"))
            if mall and price:
                ld_offers.append(Offer(normalize_mall(mall), price))
        if ld_offers:
            page.offers = ld_offers
            strategies.append("jsonld-offers")
        cat = ld.get("category")
        if isinstance(cat, str):
            page.category = cat

    if not page.name:
        t = soup.select_one(".prod_tit .title, h3.prod_tit, .prod_tit") or soup.find("meta", property="og:title")
        if t is not None:
            page.name = clean_spaces((t.get("content") if t.name == "meta" else t.get_text(" ", strip=True)) or "")
            page.name = page.name.split(" : 다나와")[0].strip()
    if not page.image:
        og = soup.find("meta", property="og:image")
        page.image = (og.get("content") or "") if og else ""
        if page.image.startswith("//"):
            page.image = "https:" + page.image

    if not page.offers:
        for name, fn in (("list-item", _offers_list_item), ("diff_item", _offers_diff_item), ("generic", _offers_generic)):
            offers = fn(soup)
            if len(offers) >= 1:
                page.offers = offers
                strategies.append(name)
                break

    # 같은 쇼핑몰은 가장 싼 값 하나만
    best: dict = {}
    for o in page.offers:
        if o.price and o.price >= 100 and (o.mall not in best or o.price < best[o.mall].price):
            best[o.mall] = o
    page.offers = sorted(best.values(), key=lambda o: o.price)

    if page.lowest is None:
        tag = soup.select_one(".lowest_price em.prc_c, .lwst_prc em.prc_c, em.prc_c")
        page.lowest = _int(tag.get_text()) if tag else None
    if page.offers:
        page.lowest = min([o.price for o in page.offers] + ([page.lowest] if page.lowest else []))
    page.strategy = "+".join(strategies) or "none"
    return page


def _jsonld_product(soup) -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("Product", ["Product"]):
                return item
    return None


def _price_in(el) -> int | None:
    for sel in ("[class*=price] [class*=num]", "[class*=price] em", "[class*=price]", "em.prc_c", "em.prc_t", "[class*=prc]",
                "strong", "em"):
        for tag in el.select(sel):
            m = _PRICE.search(tag.get_text(" ", strip=True) + "원")
            if m:
                v = _int(m.group(1))
                if v and v >= 100:
                    return v
    m = _PRICE.search(el.get_text(" ", strip=True))
    return _int(m.group(1)) if m else None


def _ship_in(el) -> tuple[str, int | None]:
    for tag in el.select("[class*=deliver], [class*=ship], .stxt"):
        txt = tag.get_text(" ", strip=True)
        if txt:
            return parse_shipping(txt)
    m = re.search(r"(무료배송|배송비\s*[\d,]+\s*원|무료)", el.get_text(" ", strip=True))
    return parse_shipping(m.group(1)) if m else ("", None)


def _mall_in(el) -> str:
    img = el.select_one(".box__logo img, .d_mall img, [class*=logo] img, [class*=mall] img")
    if img is not None and (img.get("alt") or "").strip():
        return normalize_mall(img.get("alt").strip())
    tag = el.select_one(".box__logo, .d_mall, [class*=mall-name], [class*=mall_name], [class*=shop]")
    txt = tag.get_text(" ", strip=True) if tag else ""
    return normalize_mall(txt) if txt else ""


def _offers_from(rows) -> list[Offer]:
    out = []
    for row in rows:
        mall = _mall_in(row)
        price = _price_in(row)
        if not mall or not price:
            continue
        ship, fee = _ship_in(row)
        out.append(Offer(mall, price, ship, fee))
    return out


def _offers_list_item(soup) -> list[Offer]:
    return _offers_from([li for li in soup.select("li.list-item") if li.select_one(".box__logo, [class*=logo]")])


def _offers_diff_item(soup) -> list[Offer]:
    return _offers_from(soup.select(".diff_item"))


def _offers_generic(soup) -> list[Offer]:
    """쇼핑몰 로고(img alt)와 가격이 함께 있는 줄을 판매처로 본다."""
    rows = []
    for img in soup.select("img[alt]"):
        alt = (img.get("alt") or "").strip()
        if not alt or len(alt) > 20 or not re.search(r"[가-힣A-Za-z]", alt):
            continue
        row = img.find_parent("li") or img.find_parent("tr")
        if row is not None and _PRICE.search(row.get_text(" ", strip=True)) and row not in rows:
            rows.append(row)
    offers = _offers_from(rows)
    known = [o for o in offers if o.mall in _KNOWN]
    return offers if len(known) >= 2 else []


_KNOWN = {"쿠팡", "G마켓", "옥션", "11번가", "SSG닷컴", "이마트몰", "롯데ON", "롯데하이마트", "네이버", "오늘의집", "컬리", "무신사",
          "홈플러스", "인터파크", "GS샵", "CJ온스타일", "현대Hmall", "컴퓨존", "토스쇼핑"}
