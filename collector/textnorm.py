"""상품명·쇼핑몰명·가격 표기를 다루는 도구.

커뮤니티 핫딜 제목은 대개 이런 꼴이다.
  [쇼핑몰] 상품명 (가격/배송)            예) [네이버] 포크밸리 한돈 뒷다리살 (7,900원/네멤무료)
  [쇼핑몰] 상품명 / 가격                 예) [하이마트] 플레이스테이션 VR2 PC어댑터 / 61,000원
  [쇼핑몰] 상품명 가격                   예) [쿠팡] 밀키스 190ml 30개 7,550원
여기서 쇼핑몰·상품명·가격·배송비를 뽑고, 다나와에서 같은 제품을 찾을 수 있게 상품명을 다듬는다.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# ── 쇼핑몰 ─────────────────────────────────────────────────

_MALLS = {
    "쿠팡": ["쿠팡", "coupang", "로켓배송", "쿠팡로켓", "쿠팡와우"],
    "G마켓": ["g마켓", "지마켓", "gmarket", "지마", "g마켓글로벌"],
    "옥션": ["옥션", "auction"],
    "11번가": ["11번가", "11st", "십일번가", "11마존", "11번가아마존"],
    "SSG닷컴": ["ssg", "ssg.com", "ssg닷컴", "쓱", "쓱닷컴", "신세계몰", "신세계"],
    "이마트몰": ["이마트몰", "이마트", "emart", "emart몰", "이마트에브리데이"],
    "롯데ON": ["롯데on", "롯데온", "lotteon", "롯데닷컴", "롯데홈쇼핑", "롯데백화점"],
    "롯데하이마트": ["하이마트", "롯데하이마트", "himart"],
    "네이버": ["네이버", "네이버쇼핑", "네이버페이", "스마트스토어", "네이버스토어", "naver", "네이버플러스스토어",
             "브랜드스토어", "네이버브랜드스토어", "네이버쇼핑라이브", "쇼핑라이브", "네쇼", "네이버라이브"],
    "오늘의집": ["오늘의집", "오집", "ohou", "오늘의집특가"],
    "컬리": ["컬리", "마켓컬리", "kurly", "뷰티컬리"],
    "무신사": ["무신사", "musinsa", "무신사스토어"],
    "홈플러스": ["홈플러스", "homeplus"],
    "인터파크": ["인터파크", "interpark"],
    "토스쇼핑": ["토스", "토스쇼핑", "토스페이"],
    "카카오": ["카카오", "카카오톡", "톡딜", "선물하기", "카카오톡선물하기", "카카오쇼핑", "톡스토어"],
    "GS샵": ["gs샵", "gsshop", "gs홈쇼핑", "gs fresh", "gs프레시몰"],
    "CJ온스타일": ["cj온스타일", "cjonstyle", "cj몰"],
    "현대Hmall": ["hmall", "현대hmall", "현대홈쇼핑", "더현대"],
    "올리브영": ["올리브영", "oliveyoung"],
    "다이소": ["다이소", "다이소몰"],
    "코스트코": ["코스트코", "costco"],
    "컴퓨존": ["컴퓨존", "compuzone"],
    "다나와": ["다나와", "샵다나와"],
    "29CM": ["29cm"],
    "W컨셉": ["w컨셉", "wconcept"],
    "에이블리": ["에이블리"],
    "지그재그": ["지그재그"],
    "알리익스프레스": ["알리", "알리익스프레스", "aliexpress", "알리익스", "알리초이스"],
    "아마존": ["아마존", "amazon", "아마존미국", "아마존일본", "amazon.com"],
    "테무": ["테무", "temu"],
    "큐텐": ["큐텐", "qoo10"],
    "이베이": ["이베이", "ebay"],
    "스팀": ["스팀", "steam"],
    "에픽게임즈": ["에픽", "에픽게임즈", "epic", "epicgames", "에픽스토어"],
    "MS스토어": ["ms스토어", "microsoft", "마이크로소프트", "xbox", "엑스박스"],
    "PS스토어": ["ps스토어", "플레이스테이션스토어", "psn", "playstation"],
    "닌텐도": ["닌텐도", "한국닌텐도", "e숍", "닌텐도e숍", "eshop"],
    "구글플레이": ["구글플레이", "googleplay", "구글"],
    "앱스토어": ["앱스토어", "appstore", "애플"],
}
_ALIAS = {}
for _canon, _names in _MALLS.items():
    for _n in _names + [_canon]:
        _ALIAS[re.sub(r"[\s·.]", "", _n.lower())] = _canon

OVERSEAS_MALLS = {"알리익스프레스", "아마존", "테무", "큐텐", "이베이"}
DIGITAL_MALLS = {"스팀", "에픽게임즈", "MS스토어", "PS스토어", "닌텐도", "구글플레이", "앱스토어"}


def known_mall(raw: str | None) -> bool:
    if not raw:
        return False
    key = re.sub(r"[\s·.]", "", html.unescape(str(raw)).strip().lower())
    return key in _ALIAS or any(len(a) >= 2 and key.startswith(a) for a in _ALIAS)


def normalize_mall(raw: str | None) -> str:
    if not raw:
        return ""
    s = html.unescape(str(raw)).strip()
    key = re.sub(r"[\s·.]", "", s.lower())
    if key in _ALIAS:
        return _ALIAS[key]
    for alias, canon in _ALIAS.items():
        if len(alias) >= 2 and key.startswith(alias):
            return canon
    return s[:20]


# ── 가격·배송비 ────────────────────────────────────────────

_NUM = r"\d{1,3}(?:,\d{3})+|\d+"
_KRW = re.compile(rf"(?:[₩￦]\s*({_NUM}))|(?:({_NUM})\s*(?:원|₩|￦))")
_MAN = re.compile(r"(\d+(?:\.\d+)?)\s*만\s*(?:원|₩)")
_USD = re.compile(r"(?:\$|US\$|USD)\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:달러|USD|불)\b")
_FREE_SHIP = ("무료", "무배", "무료배송", "로켓", "와우", "네멤", "멤버십무료", "0원")


def parse_price_text(text: str | None) -> tuple[int | None, str | None, bool]:
    """가격 문자열 → (금액, 통화, 여러 가격이 섞였는지)."""
    if not text:
        return None, None, False
    t = text.replace(" ", " ")
    prices = []
    for m in _KRW.finditer(t):
        v = int((m.group(1) or m.group(2)).replace(",", ""))
        if v > 0 or "0원" in m.group(0):
            prices.append((m.start(), v))
    for m in _MAN.finditer(t):
        prices.append((m.start(), int(float(m.group(1)) * 10000)))
    prices.sort()
    if prices:
        values = [v for _, v in prices]
        # '최종가'·'결제가' 뒤에 나온 값을 우선
        key = re.search(r"(최종가|최종|결제가|실구매가|혜택가)", t)
        if key:
            after = [v for pos, v in prices if pos >= key.start()]
            if after:
                return after[0], "KRW", len(set(values)) > 1
        return values[0], "KRW", len(set(values)) > 1
    m = _USD.search(t)
    if m:
        return int(round(float(m.group(1) or m.group(2)) * 100)), "USD_CENT", False
    m = re.fullmatch(r"\s*([\d,]{3,})\s*", t)
    if m:
        return int(m.group(1).replace(",", "")), "KRW", False
    return None, None, False


def parse_shipping(text: str | None) -> tuple[str, int | None]:
    """배송 문자열 → (보기 좋은 표기, 배송비 원 단위; 모르면 None)."""
    if not text:
        return "", None
    t = re.sub(r"\s+", " ", text).strip()
    compact = t.replace(" ", "")
    free_word = any(k in compact for k in _FREE_SHIP if k != "0원") or re.search(r"(?<![\d,])0원", compact)
    if free_word and re.search(r"이상|조건", compact):
        return t[:24], 0  # 조건부 무료배송 (조건을 넘기는 딜이 대부분이라 무료로 본다)
    if free_word and not re.search(r"(?<![\d,])[1-9][\d,]*원", compact):
        return "무료배송" if compact in ("무료", "무배", "무료배송", "0원", "배송비0원") else t, 0
    m = re.search(rf"({_NUM})\s*원?", t)
    if m:
        v = int(m.group(1).replace(",", ""))
        if v == 0:
            return "무료배송", 0
        if v < 100000:
            return f"배송비 {v:,}원", v
    if any(k in compact for k in ("유료", "착불", "유배")):
        return "배송비 있음", None
    return t[:20], None


# ── 제목 해석 ───────────────────────────────────────────────

_LEAD_BRACKET = re.compile(r"^\s*[\[【〔]\s*([^\]】〕]{1,24}?)\s*[\]】〕]\s*")
_LEAD_PAREN = re.compile(r"^\s*([가-힣A-Za-z0-9&+ .]{2,12})\)\s*")
_TAIL_GROUP = re.compile(r"\(([^()]*?(?:원|₩|￦|\$|달러|무료|무배|배송|다양|변동)[^()]*)\)\s*$")
_STATUS_WORDS = ("종료", "품절", "마감", "매진", "완판", "끝", "재고없음")
NON_PRODUCT_WORDS = ("상품권", "기프티콘", "기프트카드", "기프트 카드", "금액권", "모바일교환권", "교환권", "포인트", "적립",
                     "페이백", "캐시백", "응모", "래플", "이벤트", "출석", "룰렛", "쿠폰팩", "할인코드", "프로모션코드",
                     "멤버십 가입", "라방", "라이브예고", "클릭", "무료배포", "무료 배포", "무료쿠폰", "구독권", "이용권")


@dataclass
class TitleParts:
    raw: str
    mall: str = ""
    name: str = ""
    price: int | None = None
    currency: str | None = None
    multi_price: bool = False
    shipping: str = ""
    shipping_fee: int | None = None
    ended: bool = False
    flags: list = field(default_factory=list)  # overseas, digital, non_product, free, foreign_currency


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_title(title: str, *, mall: str | None = None, price: str | None = None, shipping: str | None = None) -> TitleParts:
    """제목(과 게시판이 따로 주는 쇼핑몰·가격·배송 칸)을 해석한다."""
    raw = clean_spaces(html.unescape(title or ""))
    tp = TitleParts(raw=raw)
    t = raw
    if any(w in t[:6] for w in _STATUS_WORDS) and re.match(r"^\s*[\[(]?(종료|품절|마감|매진)", t):
        tp.ended = True
        t = re.sub(r"^\s*[\[(]?(종료|품절|마감|매진)[\])]?\s*", "", t)

    lead_mall = ""
    m = _LEAD_BRACKET.match(t)
    if m:
        lead_mall = m.group(1)
        t = t[m.end():]
        m2 = _LEAD_BRACKET.match(t)
        if m2 and not known_mall(lead_mall):  # [분류][쇼핑몰] 꼴
            lead_mall = m2.group(1)
            t = t[m2.end():]
    else:
        m = _LEAD_PAREN.match(t)
        if m and len(m.group(1)) <= 12:
            lead_mall = m.group(1)
            t = t[m.end():]

    t = re.sub(r"\s*(\.\.\.|…)\s*$", "", t)  # 목록에서 잘린 제목
    group_text = ""
    m = _TAIL_GROUP.search(t)
    if m:
        group_text = m.group(1)
        t = t[:m.start()]
    else:
        m = re.search(r"\(([\d,]{3,})\s*(원|₩)?[^()]*$", t)  # 잘린 '(14,240' 꼴
        if m:
            group_text = m.group(1) + "원"
            t = t[:m.start()]

    # 쇼핑몰
    tp.mall = normalize_mall(mall) if mall else normalize_mall(lead_mall)

    # 가격·배송: 게시판 칸 → 제목 끝 괄호 → 제목 본문
    if price:
        tp.price, tp.currency, tp.multi_price = parse_price_text(price)
    ship_src = shipping
    if group_text:
        parts = [p.strip() for p in re.split(r"[/|]", group_text) if p.strip()]
        if tp.price is None and parts:
            tp.price, tp.currency, tp.multi_price = parse_price_text(parts[0])
            if tp.price is None:
                tp.price, tp.currency, tp.multi_price = parse_price_text(group_text)
        if ship_src is None and len(parts) > 1:
            ship_src = parts[-1]
    if tp.price is None:
        tp.price, tp.currency, tp.multi_price = parse_price_text(t)
    if ship_src:
        tp.shipping, tp.shipping_fee = parse_shipping(ship_src)

    # 상품명: 본문에서 가격 표기·구분자 제거
    name = t
    name = re.sub(r"\(?\s*(최종가|최종|결제가|혜택가)\s*[:：]?\s*[\d,]+\s*(원|₩|￦)?\s*\)?", " ", name)
    name = re.sub(r"[₩￦]\s*[\d,]+", " ", name)
    name = re.sub(rf"(?<![\w.])(?:{_NUM})\s*(?:원|₩|￦)(?!\s*권)", " ", name)
    name = re.sub(r"\s*[/|]\s*$", "", name.strip())
    name = re.sub(r"\s*[/|]\s*(?=\s|$)", " ", name)
    tp.name = clean_spaces(name.strip(" -~/|:"))

    low = raw.lower()
    if tp.mall in OVERSEAS_MALLS or tp.currency == "USD_CENT" or "해외직구" in raw or "직배" in raw:
        tp.flags.append("overseas")
    if tp.mall in DIGITAL_MALLS:
        tp.flags.append("digital")
    if any(w.replace(" ", "") in low.replace(" ", "") for w in NON_PRODUCT_WORDS):
        tp.flags.append("non_product")
    if tp.price == 0:
        tp.flags.append("free")
    if any(w in raw for w in ("품절", "종료", "마감", "매진")) and re.search(r"(품절|종료|마감|매진)\s*[\])]?\s*$", raw):
        tp.ended = True
    return tp


# ── 상품명 정규화와 비교 ─────────────────────────────────────

PROMO_WORDS = ("특가", "역대가", "역대급", "최저가", "쿠폰", "카드", "할인", "적용", "무료배송", "무배", "로켓배송", "로켓",
               "와우", "정품", "국내정품", "정식발매", "새상품", "공식", "공식판매", "당일발송", "당일출고", "빠른배송",
               "타임딜", "오늘만", "한정", "단독", "기획", "증정", "사은품", "추가할인", "중복할인", "멤버십", "네멤",
               "선착순", "최종가", "구매시", "이상", "광고", "핫딜", "초특가", "대박", "혜자", "가성비")
_UNIT_RE = re.compile(
    r"(?<![A-Za-z\d.])(\d+(?:\.\d+)?)\s*(ml|mL|ML|l|L|리터|g|kg|KG|Kg|gb|GB|tb|TB|mb|MB|mm|cm|인치|형|개입|개|입|병|펫|캔|봉|팩|매|구|롤|정|포|"
    r"박스|box|BOX|세트|set|ea|EA|p|P|w|W|mah|mAh|hz|Hz)(?![A-Za-z가-힣])")
_COUNT_UNITS = {"개입", "개", "입", "병", "펫", "캔", "봉", "팩", "매", "구", "롤", "정", "포", "박스", "box", "세트", "set", "ea", "p"}
_MULT = re.compile(r"[x×*X]\s*(\d{1,3})(?!\d)")
_MODEL = re.compile(r"[a-z0-9][a-z0-9\-+/]*", re.I)
_MODEL_UNIT = re.compile(r"^\d+(\.\d+)?(gb|tb|mb|mm|cm|m|ml|l|kg|g|w|mah|hz|v|p|ea|kcal|cc|inch|in|k|개|입)$", re.I)
_WORD = re.compile(r"[0-9a-z가-힣+]+", re.I)
STOPWORDS = {"정품", "국내정품", "새상품", "공식", "무료배송", "당일발송", "특가", "최신형", "신제품", "신형", "본사", "증정", "사은품",
             "세트", "기획", "패키지", "에디션", "모델", "블랙", "화이트", "그레이", "실버"}


def quantities(text: str) -> dict:
    """'2L × 24병' → {'ml': 2000, 'count': 24}. 같은 종류가 여러 번 나오면 첫 값."""
    out: dict = {}
    t = text or ""
    counts: list[float] = []
    for m in _UNIT_RE.finditer(t):
        v, unit = float(m.group(1)), m.group(2).lower()
        if unit == "ml":
            out.setdefault("ml", v)
        elif unit in ("l", "리터"):
            out.setdefault("ml", v * 1000)
        elif unit == "g":
            out.setdefault("g", v)
        elif unit == "kg":
            out.setdefault("g", v * 1000)
        elif unit == "gb":
            out.setdefault("gb", v)
        elif unit == "tb":
            out.setdefault("gb", v * 1024)
        elif unit in ("인치", "형"):
            out.setdefault("inch", v)
        elif unit == "mah":
            out.setdefault("mah", v)
        elif unit in _COUNT_UNITS and v > 0:
            counts.append(v)
    if re.search(r"[가-힣A-Za-z]\s*\+\s*[가-힣A-Za-z]", t):
        return out  # 서로 다른 상품을 묶은 구성은 수량 비교를 하지 않는다
    mults = [float(m.group(1)) for m in _MULT.finditer(t)]
    total = None
    for c in counts:
        total = c if total is None else total * c
    for m in mults:
        if m not in counts and m > 0:
            total = m if total is None else total * m
    pm = re.search(r"(?<![\d.])(\d)\s*\+\s*(\d)(?![\d.])", t)
    if pm:
        a, b = int(pm.group(1)), int(pm.group(2))
        total = float(a + b) if total is None else total * (a + b) / max(a, 1)
    if total is not None:
        out["count"] = total
    return out


def model_tokens(text: str) -> set[str]:
    out = set()
    src = re.sub(r"[\[\]()]", " ", text or "")
    src = re.sub(r"\b([A-Za-z]{2,5})\s+(\d{2,5})\b", r"\1\2", src)
    for tok in _MODEL.findall(src):
        tok = tok.strip("-/+").lower()
        if len(tok) < 3 or _MODEL_UNIT.match(tok):
            continue
        if re.search(r"\d", tok) and re.search(r"[a-z]", tok):
            out.add(tok.replace("/", ""))
    return out


def words(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall((text or "").lower()):
        if len(w) >= 2 and w not in STOPWORDS and not re.fullmatch(r"\d+", w):
            out.add(w)
    return out


def search_query(name: str, max_words: int = 7) -> str:
    """다나와 검색용 상품명: 홍보 문구·괄호 속 잡문구를 빼고 앞쪽 핵심 단어만."""
    s = html.unescape(name or "")
    s = re.sub(r"[\[【(（][^\]】)）]*?(" + "|".join(PROMO_WORDS) + r")[^\]】)）]*[\]】)）]", " ", s)
    s = re.sub(r"[\[\]【】()（）{}<>\"'“”‘’!?~*#@]", " ", s)
    for w in sorted(PROMO_WORDS, key=len, reverse=True):
        s = s.replace(w, " ")
    s = re.sub(r"\d+\s*%", " ", s)
    s = re.sub(r"[,/|+]", " ", s)
    toks = [t for t in s.split() if t and not re.fullmatch(r"[-–—·.]+", t)]
    return " ".join(toks[:max_words])


def is_variant_marker(text: str) -> set[str]:
    """해외구매·중고·리퍼·벌크 같은 '다른 구성' 표시."""
    t = text or ""
    out = set()
    for key, pats in {"overseas": ("해외구매", "해외직구", "직구", "병행수입", "병행"), "used": ("중고", "리퍼", "전시", "반품", "B급"),
                      "bulk": ("벌크",)}.items():
        if any(p in t for p in pats):
            out.add(key)
    return out
