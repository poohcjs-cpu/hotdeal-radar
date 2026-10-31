"""핫딜 커뮤니티 게시판 읽기.

공식 RSS가 있는 곳은 RSS를 쓴다 (뽐뿌·루리웹·쿨앤조이·다모앙).
RSS가 없는 곳(퀘이사존·아카라이브·클리앙·에펨코리아)은 게시판 첫 페이지 한 장만 읽는다.
어느 쪽이든 PoliteClient가 robots.txt를 확인하고, 막혀 있거나 봇 확인 페이지가 나오면 그 사이트는 건너뛴다.

게시판 화면은 바뀔 수 있으므로, 파서가 0건을 돌려주면 '구조가 바뀐 것 같아요'로 상태에 표시된다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .textnorm import clean_spaces

KST = timezone(timedelta(hours=9))


@dataclass
class RawPost:
    source: str
    id: str
    url: str
    title: str
    author: str = ""
    posted_at: str | None = None  # ISO 8601 (KST)
    category: str = ""
    mall: str | None = None
    price: str | None = None
    shipping: str | None = None
    votes: int | None = None
    views: int | None = None
    comments: int | None = None
    ended: bool = False


@dataclass
class Source:
    id: str
    name: str
    board: str
    url: str
    kind: str  # rss | html
    parser: object = field(repr=False, default=None)
    home: str = ""


def _num(text) -> int | None:
    if text is None:
        return None
    t = str(text).strip().lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(천|만|k)?", t)
    if not m:
        return None
    v = float(m.group(1))
    mult = {"천": 1000, "k": 1000, "만": 10000}.get(m.group(2) or "", 1)
    return int(v * mult)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST).replace(microsecond=0).isoformat()


def _rss_time(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return _iso(parsedate_to_datetime(text.strip()))
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return _iso(datetime.fromisoformat(text.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


_ENDED_WORDS = ("품절", "종료", "매진", "마감", "완판")


def _ended_by_title(title: str) -> bool:
    return bool(re.search(r"(^\s*[\[(]?\s*(품절|종료|매진|마감|완판))|((품절|종료|매진|마감|완판)\s*[\])]?\s*$)", title or ""))


# ── RSS ────────────────────────────────────────────────────

_NS = {"dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}


def parse_rss(text: str) -> tuple[str, list[dict]]:
    """RSS 2.0 → (채널 제목, [{title, link, author, pubDate, category, description, hits}])."""
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("﻿"), count=1)
    root = ET.fromstring(text)
    channel = root.find("channel")
    if channel is None:
        return "", []
    items = []
    for it in channel.findall("item"):
        def g(tag):
            el = it.find(tag, _NS) if ":" in tag else it.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        items.append({
            "title": g("title"), "link": g("link") or g("guid"), "author": g("author") or g("dc:creator"),
            "pubDate": g("pubDate") or g("dc:date"), "category": g("category"), "description": g("description"),
            "hits": g("hits"), "comments": g("comments"),
        })
    title = channel.findtext("title") or ""
    return title.strip(), items


def _last_number(url: str) -> str | None:
    m = re.findall(r"(\d{3,})", url or "")
    return m[-1] if m else None


def parse_ppomppu_rss(text: str, source: str = "ppomppu") -> list[RawPost]:
    _, items = parse_rss(text)
    out = []
    for it in items:
        m = re.search(r"id=(\w+)&(?:amp;)?no=(\d+)", it["link"])
        if not m or not it["title"]:
            continue
        comments = views = votes = None
        if it["hits"]:
            parts = it["hits"].strip().strip("[]").split("|")
            if len(parts) >= 3:
                comments, views, votes = _num(parts[0]), _num(parts[1]), _num(parts[2])
        out.append(RawPost(source, m.group(2), f"https://www.ppomppu.co.kr/zboard/view.php?id={m.group(1)}&no={m.group(2)}",
                           it["title"], it["author"], _rss_time(it["pubDate"]), it["category"],
                           votes=votes, views=views, comments=comments, ended=_ended_by_title(it["title"])))
    return out


def parse_generic_rss(text: str, source: str) -> list[RawPost]:
    _, items = parse_rss(text)
    out = []
    for it in items:
        pid = _last_number(it["link"])
        if not pid or not it["title"] or it["title"] in ("삭제된 글", "삭제된 게시물입니다."):
            continue
        cat = it["category"]
        title = it["title"]
        m = re.match(r"^\s*\[([^\]]{1,12})\]\s*(?=\[)", title)  # 루리웹: [음식][쿠팡] … 처럼 분류가 앞에 붙는 경우
        if m and not cat:
            cat, title = m.group(1), title[m.end():]
        out.append(RawPost(source, pid, it["link"].replace(":443", ""), title, it["author"], _rss_time(it["pubDate"]), cat,
                           comments=_num(it["comments"]) if it["comments"] and it["comments"].isdigit() else None,
                           ended=_ended_by_title(title)))
    return out


# ── HTML ───────────────────────────────────────────────────

def parse_quasarzone(html: str, source: str = "quasarzone") -> list[RawPost]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    rows = soup.select(".v2-list-row--hotdeal:not(.v2-partner-row)") or soup.select(".market-info-type-list tr")
    for row in rows:
        link = row.select_one(".subject-link")
        if link is None or row.select_one(".fa-lock"):
            continue
        m = re.search(r"/bbs/(qb_saleinfo)/views/(\d+)", link.get("href") or "")
        if not m:
            continue
        tt = link.select_one(".ellipsis-with-reply-cnt") or link
        title = clean_spaces(tt.get_text(" ", strip=True))
        if not title:
            continue
        cat = row.select_one(".v2-badge, .category")
        price = row.select_one(".v2-list-row__price, .market-info-sub .text-orange")
        if price is None:
            for sp in row.select(".market-info-sub span"):
                if sp.find(string=True, recursive=False) and "가격" in sp.get_text():
                    price = sp.find("span") or sp
        ship = None
        for sp in row.select(".v2-list-row__ship, .market-info-sub span"):
            t = sp.get_text(" ", strip=True)
            if t.startswith("배송비"):
                ship = t.replace("배송비", "").strip()
        votes = row.select_one(".qc-count-good, td .num")
        views = row.select_one(".qc-count-hit, .count")
        comments = row.select_one(".ctn-count, .board-list-comment")
        ended = "is-done" in (row.get("class") or []) or any(
            (lab.get_text(strip=True) == "종료") for lab in row.select(".v2-thumb-done, .label"))
        out.append(RawPost(source, m.group(2), f"https://quasarzone.com/bbs/{m.group(1)}/views/{m.group(2)}", title,
                           category=clean_spaces(cat.get_text(" ", strip=True)) if cat else "",
                           price=price.get_text(" ", strip=True) if price else None, shipping=ship,
                           votes=_num(votes.get_text()) if votes else None, views=_num(views.get_text()) if views else None,
                           comments=_num(comments.get_text()) if comments else None, ended=ended))
    return out


def parse_arcalive(html: str, source: str = "arcalive") -> list[RawPost]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select(".list-table .vrow.hybrid, .list-table a.vrow"):
        t = row.select_one("a.title, .title")
        if t is None:
            continue
        href = t.get("href") or row.get("href") or ""
        m = re.match(r"/b/([\w]+)/(\d+)", href)
        if not m or "notice" in " ".join(row.get("class", [])):
            continue
        title = clean_spaces("".join(t.find_all(string=True, recursive=False))) or clean_spaces(t.get_text(" ", strip=True))
        store = row.select_one(".deal-store")
        if store is None:
            continue
        cat = row.select_one(".badge")
        price = row.select_one(".deal-price")
        ship = row.select_one(".deal-delivery")
        votes = row.select_one(".col-rate")
        views = row.select_one(".col-view")
        comments = row.select_one(".comment-count, .info .comment-count")
        tm = row.select_one("time[datetime]")
        posted = None
        if tm is not None:
            try:
                posted = _iso(datetime.fromisoformat(tm["datetime"].replace("Z", "+00:00")))
            except ValueError:
                posted = None
        out.append(RawPost(source, m.group(2), f"https://arca.live/b/{m.group(1)}/{m.group(2)}", title,
                           posted_at=posted, category=cat.get_text(" ", strip=True) if cat else "",
                           mall=store.get_text(" ", strip=True), price=price.get_text(" ", strip=True) if price else None,
                           shipping=ship.get_text(" ", strip=True) if ship else None,
                           votes=_num(votes.get_text()) if votes else None, views=_num(views.get_text()) if views else None,
                           comments=_num(comments.get_text()) if comments else None,
                           ended=row.select_one(".deal-close") is not None))
    return out


def parse_clien(html: str, source: str = "clien") -> list[RawPost]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select(".list_item.jirum, .contents_jirum .list_item"):
        sn = row.get("data-board-sn")
        subj = row.select_one(".list_subject")
        if not sn or subj is None:
            continue
        title = clean_spaces(subj.get("title") or subj.get_text(" ", strip=True))
        if not title or "●▅" in title:
            continue
        cat = row.select_one(".icon_keyword")
        votes = row.select_one(".list_votes")
        views = row.select_one(".list_hit .hit, .list_hit")
        ts = row.select_one(".list_time .timestamp, .timestamp")
        posted = None
        if ts is not None:
            try:
                posted = _iso(datetime.strptime(ts.get_text(strip=True), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                posted = None
        out.append(RawPost(source, sn, f"https://www.clien.net/service/board/jirum/{sn}", title, posted_at=posted,
                           category=cat.get_text(" ", strip=True) if cat else "",
                           votes=_num(votes.get_text()) if votes else None, views=_num(views.get_text()) if views else None,
                           ended="sold_out" in (row.get("class") or [])))
    return out


def parse_fmkorea(html: str, source: str = "fmkorea") -> list[RawPost]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for row in soup.select("#content .fm_best_widget ul li, .fm_best_widget li"):
        a = row.select_one(".title a")
        if a is None:
            continue
        pid = (a.get("href") or "").strip("/").split("?")[0]
        if not pid.isdigit():
            pid = _last_number(a.get("href") or "") or ""
        if not pid:
            continue
        tt = a.select_one(".ellipsis-target")
        title = clean_spaces(tt.get_text(" ", strip=True) if tt else (a.find(string=True, recursive=False) or a.get_text(" ", strip=True)))
        info = {}
        for sp in row.select(".hotdeal_info span"):
            txt = sp.get_text(" ", strip=True)
            val = sp.find("a")
            v = val.get_text(" ", strip=True) if val else txt.split(":", 1)[-1].strip()
            if "쇼핑몰" in txt:
                info["mall"] = v
            elif "가격" in txt:
                info["price"] = v
            elif "배송" in txt:
                info["shipping"] = v
        cat = row.select_one(".category a, .category")
        votes = row.select_one(".pc_voted_count .count, .count")
        comments = row.select_one(".comment_count")
        out.append(RawPost(source, pid, f"https://www.fmkorea.com/{pid}", title,
                           category=cat.get_text(" ", strip=True) if cat else "", mall=info.get("mall"),
                           price=info.get("price"), shipping=info.get("shipping"),
                           votes=_num(votes.get_text()) if votes else None,
                           comments=_num(comments.get_text(" ", strip=True).strip("[]")) if comments else None,
                           ended=row.select_one(".hotdeal_var8Y") is not None))
    return out


# ── 게시판 목록 ─────────────────────────────────────────────

SOURCES = [
    Source("ppomppu", "뽐뿌", "뽐뿌게시판", "https://www.ppomppu.co.kr/rss.php?id=ppomppu", "rss", parse_ppomppu_rss,
           "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu"),
    Source("ppomppu_overseas", "뽐뿌", "해외뽐뿌", "https://www.ppomppu.co.kr/rss.php?id=ppomppu4", "rss",
           lambda t, s="ppomppu_overseas": parse_ppomppu_rss(t, s), "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu4"),
    Source("ruliweb", "루리웹", "유저 예판 핫딜", "https://bbs.ruliweb.com/market/board/1020/rss", "rss",
           lambda t, s="ruliweb": parse_generic_rss(t, s), "https://bbs.ruliweb.com/market/board/1020"),
    Source("coolenjoy", "쿨앤조이", "지름/알뜰정보", "https://coolenjoy.net/bbs/rss.php?bo_table=jirum", "rss",
           lambda t, s="coolenjoy": parse_generic_rss(t, s), "https://coolenjoy.net/bbs/jirum"),
    Source("damoang", "다모앙", "알뜰구매", "https://damoang.net/bbs/rss.php?bo_table=economy", "rss",
           lambda t, s="damoang": parse_generic_rss(t, s), "https://damoang.net/economy"),
    Source("quasarzone", "퀘이사존", "지름/할인정보", "https://quasarzone.com/bbs/qb_saleinfo", "html", parse_quasarzone,
           "https://quasarzone.com/bbs/qb_saleinfo"),
    Source("arcalive", "아카라이브", "핫딜 채널", "https://arca.live/b/hotdeal", "html", parse_arcalive, "https://arca.live/b/hotdeal"),
    Source("clien", "클리앙", "알뜰구매", "https://www.clien.net/service/board/jirum", "html", parse_clien,
           "https://www.clien.net/service/board/jirum"),
    Source("fmkorea", "에펨코리아", "핫딜", "https://www.fmkorea.com/hotdeal", "html", parse_fmkorea, "https://www.fmkorea.com/hotdeal"),
]
SOURCE_BY_ID = {s.id: s for s in SOURCES}
