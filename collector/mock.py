"""가짜 웹 (네트워크 없이 전체 흐름을 확인하는 용도).

실제 사이트와 같은 모양의 RSS·HTML·다나와 검색/상품 페이지를 만들어 돌려준다.
  - 테스트와 `python -m collector.collect --mock` 에서 쓴다.
  - `python -m collector.mock demo` 는 며칠치 실행을 흉내 내 앱 미리보기용 예시 데이터를 만든다.
여기 나오는 가격·게시글은 모두 지어낸 예시다.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import random
import shutil
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

from .compare import coverage

MALL_POOL = ["쿠팡", "G마켓", "옥션", "11번가", "SSG닷컴", "롯데ON", "네이버", "이마트몰", "롯데하이마트", "컴퓨존", "인터파크", "GS샵",
             "CJ온스타일", "현대Hmall", "홈플러스", "토스쇼핑"]


@dataclass
class P:
    pcode: str
    name: str
    price: int
    tags: tuple = ()
    malls: int = 9
    group: str = ""
    label: str = ""
    deal: tuple | None = None       # (쇼핑몰, 다른 곳 평균 대비 할인율, 며칠 전부터)
    absurd: tuple | None = None     # (쇼핑몰, 가격) 터무니없이 싼 오등록


CATALOG = [
    P("20480011", "삼성전자 990 PRO M.2 NVMe (1TB)", 159000, ("SSD",), 12, "990pro", "1TB"),
    P("20480012", "삼성전자 990 PRO M.2 NVMe (2TB)", 259000, ("SSD",), 14, "990pro", "2TB"),
    P("20480013", "삼성전자 990 PRO M.2 NVMe (4TB)", 489000, ("SSD",), 10, "990pro", "4TB"),
    P("20480021", "LG전자 울트라기어 27GX790A", 899000, ("모니터",), 11),
    P("20480031", "MSI 지포스 RTX 5070 게이밍 트리오 OC D7 12GB", 1049000, ("그래픽카드",), 10),
    P("20480032", "MSI 지포스 RTX 5070 게이밍 트리오 OC D7 12GB (해외구매)", 899000, ("그래픽카드",), 4),
    P("20480041", "로지텍 MX Master 3S", 135000, ("무선 마우스",), 13, absurd=("옥션", 39000)),
    P("20480051", "레오폴드 FC900R PD 그레이 블루 (갈축)", 139000, ("기계식 키보드",), 8),
    P("20480061", "삼성전자 갤럭시 S25 256GB 자급제 SM-S931N", 1155000, ("갤럭시 S25",), 12),
    P("20480071", "삼성전자 갤럭시탭 S10 FE 128GB Wi-Fi", 649000, ("갤럭시탭",), 10, deal=("쿠팡", 0.31, 3)),
    P("20480081", "APPLE 아이패드 에어 11 M3 Wi-Fi 128GB", 899000, ("아이패드",), 11),
    P("20480091", "샤오미 보조배터리 20000mAh 33W PB2030MI", 32900, ("보조배터리",), 9),
    P("20480101", "삼성전자 갤럭시 버즈3 프로 SM-R630", 249000, ("무선 이어폰",), 14),
    P("20480111", "APPLE 에어팟 프로3 MFHP4KH/A (정품)", 339000, ("무선 이어폰",), 15),
    P("20480121", "소니 WH-1000XM6", 559000, ("노이즈캔슬링 헤드폰",), 11),
    P("20480131", "삼성전자 갤럭시 워치8 44mm 블루투스", 359000, ("스마트워치",), 10, deal=("11번가", 0.32, 0)),
    P("20480141", "다이슨 V15 디텍트 컴플리트 SV47", 1190000, ("무선청소기",), 9),
    P("20480151", "로보락 S9 MaxV Ultra", 1690000, ("로봇청소기",), 8),
    P("20480161", "필립스 에어프라이어 XXL HD9867/90", 389000, ("에어프라이어",), 9, deal=("G마켓", 0.34, 1)),
    P("20480171", "위닉스 뽀송 DXJE163-KWK", 399000, ("제습기",), 10),
    P("20480181", "닌텐도 스위치 2 마리오 카트 월드 세트", 698000, ("닌텐도 스위치 2",), 12),
    P("20480191", "SONY 플레이스테이션5 프로 디지털 에디션", 1098000, ("PS5",), 9),
    P("20480201", "8BitDo 얼티밋 2 블루투스 컨트롤러", 79000, ("게임 컨트롤러",), 8),
    P("20480211", "테팔 티타늄 엑설런스 인덕션 프라이팬 28cm", 62900, ("프라이팬",), 10),
    P("20480221", "락앤락 비스프리 모듈러 밀폐용기 10종 세트", 45900, ("밀폐용기",), 8),
    P("20480231", "드롱기 마그니피카 에보 ECAM290.61.B", 829000, ("커피머신",), 9, deal=("SSG닷컴", 0.36, 2)),
    P("20480241", "제주삼다수 2L (24개)", 25900, ("생수 2L",), 12, "samdasoo", "24개"),
    P("20480242", "제주삼다수 2L (12개)", 13900, ("생수 2L",), 10, "samdasoo", "12개"),
    P("20480251", "CJ제일제당 햇반 210g (36개)", 41900, ("즉석밥",), 11),
    P("20480261", "농심 신라면 120g (40개)", 33900, ("라면",), 12),
    P("20480271", "맥심 카누 미니 마일드 로스트 아메리카노 0.9g (240개)", 46900, ("커피 캡슐",), 10),
    P("20480281", "나이키 에어포스 1 '07 CW2288-111", 139000, ("운동화",), 9),
    P("20480282", "아디다스 삼바 OG B75806", 139000, ("운동화",), 8),
    P("20480291", "노스페이스 1996 에코 눕시 자켓 NJ1DQ75A", 359000, ("패딩",), 9),
    P("20480301", "라운드랩 자작나무 수분 선크림 50ml (2개)", 34900, ("선크림",), 10),
    P("20480311", "헬리녹스 체어원", 149000, ("캠핑의자",), 9),
]
BY_PCODE = {p.pcode: p for p in CATALOG}


@dataclass
class T:
    """게시판 글 틀. rate는 다른 쇼핑몰 평균보다 얼마나 싼지(딜 가격을 정할 때 씀)."""
    source: str
    mall: str
    text: str
    pcode: str | None = None
    rate: float = 0.0
    price: str | None = None        # 직접 정한 가격 표기 (pcode가 없을 때)
    ship: str = "무료"
    hours: float = 2.0
    votes: int = 0
    views: int = 0
    comments: int = 0
    ended: bool = False
    cat: str = ""


POSTS = [
    T("ppomppu", "11번가", "삼성전자 990 PRO 2TB NVMe SSD", "20480012", 0.27, hours=1.2, votes=18, views=5210, comments=34, cat="컴퓨터"),
    T("ppomppu", "쿠팡", "제주삼다수 2L 24병", "20480241", 0.22, hours=3.5, votes=6, views=2210, comments=12, cat="식품/건강"),
    T("ppomppu", "G마켓", "로지텍 MX Master 3S 무선 마우스", "20480041", 0.33, hours=0.6, votes=25, views=6120, comments=41, cat="컴퓨터"),
    T("ppomppu", "네이버", "카누 미니 마일드 아메리카노 120개입 2개", "20480271", 0.21, hours=7, votes=9, views=3300, comments=15, cat="식품/건강"),
    T("ppomppu", "네이버", "컬쳐랜드상품권 5만원", price="46,310원", hours=2.2, votes=3, views=1800, comments=9, cat="상품권"),
    T("ppomppu", "알리익스프레스", "8BitDo 얼티밋 2 컨트롤러", price="$39.99", hours=5, votes=4, views=1500, comments=7, cat="해외"),
    T("ppomppu", "SSG", "드롱기 마그니피카 에보 전자동 커피머신 ECAM290.61", "20480231", 0.38, hours=4.1, votes=31, views=8800, comments=52, cat="가전/가구"),
    T("ppomppu", "옥션", "포근 극세사 차렵이불 퀸", price="19,900원", hours=9, votes=1, views=900, comments=3, cat="생활/주방"),
    T("ppomppu", "쿠팡", "농심 신라면 40개", "20480261", 0.12, hours=10, votes=2, views=1900, comments=6, cat="식품/건강"),
    T("ruliweb", "11번가", "MSI RTX 5070 게이밍 트리오 OC 12GB", "20480031", 0.24, hours=2.8, votes=0, views=3100, comments=28, cat="PC/하드웨어"),
    T("ruliweb", "스팀", "엘든 링 나이트레인", price="25,800원", hours=6, cat="게임 S/W"),
    T("ruliweb", "쿠팡", "닌텐도 스위치 2 마리오카트 월드 세트", "20480181", 0.12, hours=11, comments=19, cat="게임 H/W"),
    T("coolenjoy", "컴퓨존", "LG 울트라기어 27GX790A OLED 모니터", "20480021", 0.18, hours=5.5, comments=11, cat="모니터"),
    T("coolenjoy", "G마켓", "레오폴드 FC900R PD 갈축", "20480051", 0.35, hours=1.9, comments=17, cat="키보드"),
    T("coolenjoy", "11번가", "삼성 990 PRO 2TB", "20480012", 0.27, hours=1.0, comments=8, cat="SSD"),
    T("damoang", "쿠팡", "햇반 210g 36개", "20480251", 0.25, hours=8, comments=14, cat="식품"),
    T("damoang", "이마트몰", "테팔 티타늄 엑설런스 프라이팬 28cm", "20480211", 0.41, hours=3.0, comments=22, cat="생활"),
    T("damoang", "네이버", "라운드랩 자작나무 선크림 50ml 2개", "20480301", 0.30, hours=30, comments=10, cat="뷰티"),
    T("quasarzone", "11번가", "소니 WH-1000XM6 노이즈캔슬링 헤드폰", "20480121", 0.24, hours=2.4, votes=14, views=4100, comments=21, cat="음향기기"),
    T("quasarzone", "쿠팡", "삼성 갤럭시 버즈3 프로", "20480101", 0.36, hours=0.9, votes=40, views=9900, comments=66, cat="음향기기"),
    T("quasarzone", "G마켓", "아이패드 에어 11 M3 128GB Wi-Fi", "20480081", 0.08, hours=14, votes=3, views=2700, comments=9, cat="모바일"),
    T("quasarzone", "롯데하이마트", "로보락 S9 MaxV Ultra", "20480151", 0.33, hours=26, votes=22, views=12400, comments=48, ended=True,
      cat="가전"),
    T("arcalive", "쿠팡", "에어팟 프로3", "20480111", 0.19, hours=1.6, votes=11, views=5400, comments=25, cat="전자제품"),
    T("arcalive", "네이버", "헬리녹스 체어원", "20480311", 0.45, hours=2.0, votes=27, views=7300, comments=39, cat="스포츠/레저"),
    T("arcalive", "SSG닷컴", "다이슨 V15 디텍트", "20480141", 0.52, hours=0.4, votes=58, views=15200, comments=120, cat="전자제품"),
    T("arcalive", "알리익스프레스", "샤오미 보조배터리 20000 33W", price="$17.20", hours=4, votes=5, views=2100, comments=6, cat="해외"),
    T("arcalive", "쿠팡", "갤럭시 S25 256GB 자급제", "20480061", 0.66, hours=0.3, votes=8, views=6400, comments=80, cat="전자제품"),
    T("clien", "쿠팡", "노스페이스 1996 에코 눕시 자켓", "20480291", 0.31, hours=3.2, votes=12, views=3900, comments=0, cat="의류"),
    T("clien", "11번가", "나이키 에어포스1 07 화이트 CW2288-111", "20480281", 0.28, hours=6.5, votes=4, views=2500, cat="의류"),
    T("clien", "롯데ON", "락앤락 비스프리 밀폐용기 10종", "20480221", 0.33, hours=9.5, votes=7, views=2000, cat="생활"),
    T("clien", "쿠팡", "위닉스 뽀송 제습기 16L DXJE163-KWK", "20480171", 0.29, hours=41, votes=5, views=2300, cat="가전"),
    T("clien", "네이버", "노브랜드 양말 10켤레", price="12,900원~19,900원", hours=7, votes=1, views=800, cat="의류"),
]

SOURCE_POST_BASE = {"ppomppu": 640000, "ruliweb": 108000, "coolenjoy": 3910000, "damoang": 52000, "quasarzone": 1790000,
                    "arcalive": 151000000, "clien": 19200000, "fmkorea": 9100000000}


def _rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _round(v: float) -> int:
    step = 100 if v >= 100000 else 10
    return int(round(v / step)) * step


def offers_for(p: P, day: int) -> list[tuple[str, int, str]]:
    """그날의 쇼핑몰별 (쇼핑몰, 가격, 배송) 목록. day = 날짜 서수."""
    r = _rng("malls", p.pcode)
    malls = r.sample(MALL_POOL, min(p.malls, len(MALL_POOL)))
    phase = r.random() * 6.28
    drift = 1 + 0.035 * math.sin(day / 4.0 + phase) - 0.01 * ((day % 9) / 9)
    out = []
    for m in malls:
        base = r.uniform(0.96, 1.14)
        noise = _rng("noise", p.pcode, m, day).uniform(-0.012, 0.012)
        ship = "무료배송" if p.price > 30000 or r.random() < 0.6 else ("배송비 3,000원" if r.random() < 0.5 else "배송비 2,500원")
        out.append([m, _round(p.price * base * drift * (1 + noise)), ship])
    if p.deal:
        mall, rate, since = p.deal
        if day >= _anchor().date().toordinal() - since:
            others = [x[1] for x in out if x[0] != mall]
            avg = sum(others) / len(others)
            for x in out:
                if x[0] == mall:
                    x[1] = _round(avg * (1 - rate))
                    x[2] = "무료배송"
    if p.absurd:
        for x in out:
            if x[0] == p.absurd[0]:
                x[1] = p.absurd[1]
    return [tuple(x) for x in out]


ANCHOR: datetime | None = None   # 예시 데이터의 '마지막 실행' 시각. 게시글·자체 발굴 딜이 이 시각을 기준으로 생긴다


def _anchor(now: datetime | None = None) -> datetime:
    return ANCHOR or now or datetime.now().astimezone()


def _others_average(p: P, day: int, mall: str) -> float:
    from .textnorm import normalize_mall
    rows = [pr for m, pr, _ in offers_for(p, day) if m != normalize_mall(mall)]
    if not rows:
        return float(p.price)
    rows.sort()
    med = rows[len(rows) // 2]
    kept = [x for x in rows if 0.5 * med <= x <= 1.8 * med]
    return sum(kept) / len(kept)


# ── 페이지 만들기 ───────────────────────────────────────────

def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _post_price(t: T, day: int) -> int | None:
    if t.pcode:
        return _round(_others_average(BY_PCODE[t.pcode], day, t.mall) * (1 - t.rate))
    return None


def _posts(source: str, now: datetime):
    """그 시각까지 올라온 글 (게시판 첫 페이지라 최근 3일치만)."""
    anchor = _anchor(now)
    out = []
    for i, t in enumerate(POSTS):
        if t.source != source:
            continue
        at = anchor - timedelta(hours=t.hours)
        if at > now or now - at > timedelta(days=3):
            continue
        price = _post_price(t, at.date().toordinal())
        ptxt = f"{price:,}원" if price else (t.price or "")
        pid = SOURCE_POST_BASE[source] + i * 7 + 3
        out.append((pid, t, ptxt, at))
    out.sort(key=lambda x: x[3], reverse=True)
    return out


def _rss(channel: str, items: list[str]) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<channel><title>{_e(channel)}</title>" + "".join(items) + "</channel></rss>")


def ppomppu_rss(now: datetime) -> str:
    items = []
    for pid, t, ptxt, at in _posts("ppomppu", now):
        title = f"[{t.mall}] {t.text} ({ptxt}/{t.ship})"
        if t.ended:
            title = "[품절] " + title
        items.append(f"<item><title>{_e(title)}</title><link>https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&amp;no={pid}</link>"
                     f"<author>뽐뿌회원{pid % 97}</author><pubDate>{format_datetime(at)}</pubDate><category>{_e(t.cat)}</category>"
                     f"<hits>[{t.comments}|{t.views}|{t.votes}|0]</hits></item>")
    return _rss("뽐뿌 - 뽐뿌게시판", items)


def generic_rss(source: str, now: datetime, link_fmt: str, channel: str, cat_prefix: bool = False) -> str:
    items = []
    for pid, t, ptxt, at in _posts(source, now):
        title = f"[{t.mall}] {t.text} ({ptxt}/{t.ship})"
        if cat_prefix:
            title = f"[{t.cat}]" + title
        items.append(f"<item><title>{_e(title)}</title><link>{link_fmt.format(pid)}</link><dc:creator>회원{pid % 89}</dc:creator>"
                     f"<pubDate>{format_datetime(at)}</pubDate>" + ("" if cat_prefix else f"<category>{_e(t.cat)}</category>") + "</item>")
    return _rss(channel, items)


def quasarzone_html(now: datetime) -> str:
    rows = []
    for pid, t, ptxt, at in _posts("quasarzone", now):
        price = f"￦ {ptxt.replace('원', '')} (KRW)" if ptxt.endswith("원") else ptxt
        rows.append(
            f'<div class="v2-list-row v2-list-row--hotdeal{" is-done" if t.ended else ""}">'
            f'<div class="v2-list-row__thumb">{"<span class=v2-thumb-done>종료</span>" if t.ended else ""}</div>'
            f'<div class="v2-list-row__main"><span class="v2-badge">{_e(t.cat)}</span>'
            f'<a class="subject-link" href="/bbs/qb_saleinfo/views/{pid}"><span class="ellipsis-with-reply-cnt">[{_e(t.mall)}] {_e(t.text)}</span></a>'
            f'<span class="ctn-count">{t.comments}</span>'
            f'<div class="v2-list-row__info"><span class="v2-list-row__price">{_e(price)}</span>'
            f'<span class="v2-list-row__ship">배송비 {_e(t.ship)}</span>'
            f'<span class="qc-count-good">{t.votes}</span><span class="qc-count-hit">{t.views:,}</span></div></div></div>')
    return "<html><body><div class='v2-list'>" + "".join(rows) + "</div></body></html>"


def arcalive_html(now: datetime) -> str:
    rows = ['<a class="vrow notice" href="/b/hotdeal/100"><span class="title">공지</span></a>']
    for pid, t, ptxt, at in _posts("arcalive", now):
        rows.append(
            f'<div class="vrow hybrid"><div class="vrow-inner"><div class="vrow-top deal">'
            f'<span class="badge">{_e(t.cat)}</span>'
            f'<a class="title hybrid-title" href="/b/hotdeal/{pid}?p=1">{_e(t.text)}<span class="info"><span class="comment-count">[{t.comments}]</span></span></a>'
            f'</div><div class="vrow-bottom deal"><span class="deal-store">{_e(t.mall)}</span>'
            f'<span class="deal-price">{_e(ptxt)}</span><span class="deal-delivery">{_e(t.ship)}</span>'
            + ('<span class="deal-close">종료</span>' if t.ended else "")
            + f'<span class="col-rate">{t.votes}</span><span class="col-view">{t.views}</span>'
            f'<time datetime="{at.astimezone().isoformat()}">{at:%H:%M}</time></div></div></div>')
    return "<html><body><div class='list-table hybrid'>" + "".join(rows) + "</div></body></html>"


def clien_html(now: datetime) -> str:
    rows = []
    for pid, t, ptxt, at in _posts("clien", now):
        title = f"[{t.mall}] {t.text} ({ptxt}/{t.ship})"
        rows.append(
            f'<div class="list_item symph_row jirum{" sold_out" if t.ended else ""}" data-board-sn="{pid}">'
            f'<div class="list_title"><span class="icon_keyword">{_e(t.cat)}</span>'
            f'<span class="list_subject" title="{_e(title)}"><a href="/service/board/jirum/{pid}">{_e(title)}</a></span></div>'
            f'<div class="list_votes">{t.votes}</div><div class="list_hit"><span class="hit">{t.views:,}</span></div>'
            f'<div class="list_time"><span class="timestamp">{at:%Y-%m-%d %H:%M:%S}</span></div></div>')
    return "<html><body><div class='contents_jirum'>" + "".join(rows) + "</div></body></html>"


def fmkorea_html(now: datetime) -> str:
    rows = []
    for pid, t, ptxt, at in _posts("fmkorea", now):
        rows.append(f'<li><h3 class="title"><a href="/{pid}"><span class="ellipsis-target">{_e(t.text)}</span>'
                    f'<span class="comment_count">[{t.comments}]</span></a></h3><div class="hotdeal_info">'
                    f'<span>쇼핑몰: <a>{_e(t.mall)}</a></span><span>가격: <a>{_e(ptxt)}</a></span><span>배송: <a>{_e(t.ship)}</a></span></div>'
                    f'<span class="category"><a>{_e(t.cat)}</a></span></li>')
    return "<html><body><div id='content'><div class='fm_best_widget'><ul>" + "".join(rows) + "</ul></div></div></body></html>"


def _search_hits(query: str) -> list[P]:
    q = query.strip().lower()
    scored = []
    for p in CATALOG:
        if any(q == t.lower() for t in p.tags):
            scored.append((1.0, p))
            continue
        c = coverage(query, p.name)
        if c >= 0.5:
            scored.append((c, p))
    scored.sort(key=lambda x: (-x[0], x[1].pcode))
    return [p for _, p in scored]


def danawa_search_html(query: str, day: int) -> str:
    hits = _search_hits(query)
    groups: dict[str, list[P]] = {}
    for p in hits:
        groups.setdefault(p.group or p.pcode, []).append(p)
    legacy = int(hashlib.md5(query.encode()).hexdigest(), 16) % 2 == 0
    items = []
    for key, ps in list(groups.items())[:10]:
        ps = sorted(ps, key=lambda x: x.price)
        head = ps[0] if not ps[0].group else max(ps, key=lambda x: len(offers_for(x, day)))
        rows = []
        for p in ps:
            offs = offers_for(p, day)
            low = min(o[1] for o in offs)
            if legacy:
                rows.append(f'<li class="opt"><p class="memory_sect"><span class="text">{_e(p.label)}</span></p>'
                            f'<p class="price_sect"><a href="https://prod.danawa.com/info/?pcode={p.pcode}"><strong>{low:,}</strong>원</a></p>'
                            f'<span class="cnt">{len(offs)}몰</span></li>' if p.group else
                            f'<li><p class="price_sect"><a href="https://prod.danawa.com/info/?pcode={p.pcode}"><strong>{low:,}</strong>원</a></p>'
                            f'<span class="cnt">{len(offs)}몰</span></li>')
            else:
                rows.append(f'<li><div class="flex"><span class="truncate text-px12">{_e(p.label)}</span>'
                            f'<a class="flex" href="https://prod.danawa.com/info/?pcode={p.pcode}&amp;cate=1"><span class="font-bold">{low:,}</span>'
                            f'<span>원</span></a><span class="text-px12">{len(offs)}<!-- -->몰</span></div></li>')
        name = head.name if not head.group else head.name.rsplit(" (", 1)[0]
        hlow = min(o[1] for o in offers_for(head, day))
        if legacy:
            items.append(f'<li class="prod_item prod_layer" id="productItem{head.pcode}">'
                         f'<div class="thumb_image"><a href="https://prod.danawa.com/info/?pcode={head.pcode}"><img src="data:image/gif;base64,R0lGOD" alt=""></a></div>'
                         f'<div class="prod_info"><p class="prod_name"><a href="https://prod.danawa.com/info/?pcode={head.pcode}&amp;keyword=x" name="productName">{_e(name)}</a></p></div>'
                         f'<div class="prod_pricelist"><ul>{"".join(rows)}</ul></div>'
                         f'<input type="hidden" id="min_price_{head.pcode}" value="{hlow}"></li>')
        else:
            items.append(f'<div class="dnw-product-list-item flex"><div class="dnw-product-info"><div data-testid="ProductListTitle">'
                         f'<a href="https://prod.danawa.com/info/?pcode={head.pcode}&amp;cate=1" class="font-bold">{_e(name)}</a></div></div>'
                         f'<div class="dnw-product-price" data-testid="ProductListPriceCompare"><div><ul>{"".join(rows)}</ul></div></div></div>')
    if legacy:
        body = '<div class="main_prodlist"><ul class="product_list">' + "".join(items) + "</ul></div>"
        if not items:
            body = '<div class="nosearch_area"><p>검색결과가 없습니다.</p></div>'
    else:
        body = '<section id="productListArea">' + "".join(items) + "</section>"
        if not items:
            body = '<section id="productListArea"><p>검색 결과가 없습니다</p></section>'
    return f"<html><head><title>{_e(query)} : 다나와 통합검색</title></head><body>{body}</body></html>"


def danawa_product_html(pcode: str, day: int) -> str | None:
    p = BY_PCODE.get(pcode)
    if p is None:
        return None
    offs = sorted(offers_for(p, day), key=lambda o: o[1])
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p.name, "image": "",
          "offers": {"@type": "AggregateOffer", "priceCurrency": "KRW", "lowPrice": offs[0][1], "highPrice": offs[-1][1],
                     "offerCount": len(offs) * 3}}
    if int(pcode) % 2 == 0:
        rows = "".join(f'<li class="list-item"><div class="box__logo"><img alt="{_e(m)}" src="data:,"></div>'
                       f'<div class="box__price"><span class="text__num">{pr:,}</span><span class="text__unit">원</span></div>'
                       f'<div class="box__delivery">{_e(sh)}</div><a class="link__buy" href="#">구매하기</a></li>' for m, pr, sh in offs)
        mall_list = f'<div class="box__mall-price"><ul class="list__mall-price">{rows}</ul></div>'
    else:
        rows = "".join(f'<tr class="diff_item"><td class="d_mall"><a><img alt="{_e(m)}" src="data:,"></a></td>'
                       f'<td class="price"><a><span class="txt_prc"><em class="prc_c">{pr:,}</em>원</span></a></td>'
                       f'<td class="ship"><span class="stxt deleveryBaseSection">{_e(sh)}</span></td></tr>' for m, pr, sh in offs)
        mall_list = f'<table class="lwst_tbl"><tbody class="high_list">{rows}</tbody></table>'
    return (f'<html><head><title>{_e(p.name)} : 다나와 가격비교</title><meta property="og:title" content="{_e(p.name)}">'
            f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></head><body>'
            f'<div class="prod_tit"><span class="title">{_e(p.name)}</span></div>'
            f'<div class="lowest_area"><em class="prc_c">{offs[0][1]:,}</em>원</div>{mall_list}</body></html>')


ROBOTS = {
    "www.ppomppu.co.kr": "User-agent: *\nDisallow: /zboard/member_\nAllow: /\n",
    "bbs.ruliweb.com": "User-agent: *\nDisallow: /member/\n",
    "coolenjoy.net": "User-agent: *\nDisallow: /adm/\n",
    "damoang.net": "User-agent: *\nDisallow: /adm/\nDisallow: /plugin/\n",
    "quasarzone.com": "User-agent: *\nDisallow: /login\n",
    "arca.live": "User-agent: *\nDisallow: /u/\nDisallow: /api/\n",
    "www.clien.net": "User-agent: *\nDisallow: /service/auth\n",
    "www.fmkorea.com": "User-agent: *\nDisallow: /index.php?act=\n",
    "search.danawa.com": "User-agent: *\nCrawl-delay: 10\nAllow: /dsearch.php\nDisallow: /ajax/\n",
    "prod.danawa.com": "User-agent: *\nCrawl-delay: 10\nDisallow: /info/ajax/\nDisallow: /list/ajax/\nAllow: /info/\n",
}


@dataclass
class MockWeb:
    now: datetime
    blocked: set = field(default_factory=lambda: {"www.fmkorea.com"})
    broken: set = field(default_factory=set)       # 구조가 바뀐 척 (빈 페이지)
    down: set = field(default_factory=set)         # 접속 실패
    t: float = 0.0
    log: list = field(default_factory=list)

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += max(0.0, s)

    def transport(self, url: str, headers: dict, timeout: float):
        self.t += 0.4
        parts = urllib.parse.urlsplit(url)
        host, path = parts.netloc, parts.path
        q = urllib.parse.parse_qs(parts.query)
        self.log.append((round(self.t, 1), url))
        if host in self.down:
            raise ConnectionError("mock down")
        if path == "/robots.txt":
            body = ROBOTS.get(host)
            return (200, {"content-type": "text/plain"}, body.encode()) + (url,) if body else (404, {}, b"", url)
        if host in self.blocked:
            return 430, {"content-type": "text/html"}, b"<html>blocked</html>", url
        if host in self.broken:
            return 200, {"content-type": "text/html"}, b"<html><body><div class='new-layout'></div></body></html>", url
        day = self.now.date().toordinal()
        body = None
        if host == "www.ppomppu.co.kr" and path == "/rss.php":
            body = ppomppu_rss(self.now) if q.get("id") == ["ppomppu"] else _rss("뽐뿌 - 해외뽐뿌", [])
        elif host == "bbs.ruliweb.com" and path.endswith("/rss"):
            body = generic_rss("ruliweb", self.now, "https://bbs.ruliweb.com/market/board/1020/read/{}", "루리웹 핫딜", True)
        elif host == "coolenjoy.net" and path.endswith("rss.php"):
            body = generic_rss("coolenjoy", self.now, "https://coolenjoy.net/bbs/jirum/{}", "쿨앤조이 지름/알뜰정보")
        elif host == "damoang.net" and path.endswith("rss.php"):
            body = generic_rss("damoang", self.now, "https://damoang.net/economy/{}", "다모앙 알뜰구매")
        elif host == "quasarzone.com":
            body = quasarzone_html(self.now)
        elif host == "arca.live":
            body = arcalive_html(self.now)
        elif host == "www.clien.net":
            body = clien_html(self.now)
        elif host == "www.fmkorea.com":
            body = fmkorea_html(self.now)
        elif host == "search.danawa.com" and path == "/dsearch.php":
            body = danawa_search_html(q.get("query", [""])[0], day)
        elif host == "prod.danawa.com" and path.startswith("/info/"):
            body = danawa_product_html(q.get("pcode", [""])[0], day)
        if body is None:
            return 404, {"content-type": "text/html"}, b"not found", url
        ctype = "application/rss+xml; charset=utf-8" if body.lstrip().startswith("<?xml") else "text/html; charset=utf-8"
        return 200, {"content-type": ctype}, body.encode("utf-8"), url


# ── 예시 데이터 만들기 (앱 미리보기용) ─────────────────────────

def simulate(root: Path, now: datetime, days: int = 21, log=lambda *_: None) -> dict:
    """며칠 동안 하루 몇 번씩 수집한 것처럼 실행해 data/ 와 docs/data/deals.json을 만든다."""
    from . import build, store
    from .collect import Run, make_client

    global ANCHOR
    ANCHOR = now
    cfg = store.load_config(root)
    cfg["danawa"]["max_verifications_per_run"] = 60
    cfg["danawa"]["keywords_per_run"] = 60
    cfg["danawa"]["max_product_pages_per_run"] = 60
    cfg["run"]["budget_minutes"] = 600
    summary = {}
    for d in range(days, -1, -1):
        for hour in ((9, 21) if d else ()):
            at = (now - timedelta(days=d)).replace(hour=hour, minute=7, second=0, microsecond=0)
            web = MockWeb(at)
            Run(root, cfg, at, make_client(cfg, transport=web.transport, sleep=web.sleep, clock=web.clock), log=log).run()
    for minutes in (95, 35, 0):
        at = now - timedelta(minutes=minutes)
        web = MockWeb(at)
        summary = Run(root, cfg, at, make_client(cfg, transport=web.transport, sleep=web.sleep, clock=web.clock), log=log).run()
    build.build(root, now=now)
    return summary


def make_demo(days: int = 21) -> dict:
    """임시 폴더에서 며칠치 실행을 흉내 내 예시 deals.json 내용을 돌려준다."""
    import os
    import tempfile

    from . import store
    src = store.repo_root(None)
    tmp = Path(tempfile.mkdtemp(prefix="hotdeal-demo-"))
    try:
        (tmp / "docs" / "data").mkdir(parents=True)
        shutil.copy(src / "config.json", tmp / "config.json")
        os.environ["HOTDEAL_DEMO"] = "1"
        simulate(tmp, store.now_kst().replace(second=0, microsecond=0), days)
        return json.loads((tmp / "docs" / "data" / "deals.json").read_text(encoding="utf-8"))
    finally:
        os.environ.pop("HOTDEAL_DEMO", None)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    """
      python -m collector.mock demo              # docs/data/demo.json 다시 만들기 (수집 전 앱이 보여 주는 예시)
      python -m collector.mock preview --out x.html   # 예시 데이터를 넣은 앱 파일 하나 (미리보기용)
    """
    from . import store
    ap = argparse.ArgumentParser(description="예시 데이터 만들기")
    ap.add_argument("command", choices=["demo", "preview"])
    ap.add_argument("--out", help="demo: 저장할 json 경로 / preview: 저장할 html 경로")
    ap.add_argument("--days", type=int, default=21)
    args = ap.parse_args(argv)
    root = store.repo_root(None)
    data = make_demo(args.days)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if args.command == "demo":
        out = Path(args.out) if args.out else root / "docs" / "data" / "demo.json"
        out.write_text(text, encoding="utf-8")
    else:
        out = Path(args.out or "preview.html")
        page = (root / "docs" / "index.html").read_text(encoding="utf-8")
        tag = '<script type="application/json" id="demo-json">' + text.replace("</", "<\\/") + "</script>\n"
        out.write_text(page.replace("<script>\n(() => {", tag + "<script>\n(() => {", 1), encoding="utf-8")
    print(f"{out} ({out.stat().st_size // 1024}KB) · 딜 {data['stats']['deals']}개 · 글 {data['stats']['posts']}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
