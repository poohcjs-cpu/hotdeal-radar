"""게시판·다나와 페이지 파서 (실제 화면 구조를 본뜬 고정 파일 사용)."""
import unittest
from pathlib import Path

from collector import community, danawa

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class CommunityTest(unittest.TestCase):
    def test_ppomppu_rss(self):
        posts = community.parse_ppomppu_rss(read("ppomppu.xml"))
        self.assertEqual([p.id for p in posts], ["744510", "744501", "744490"])  # 제목 없는 글은 건너뜀
        first = posts[0]
        self.assertEqual((first.comments, first.views, first.votes), (12, 3021, 5))
        self.assertEqual(first.url, "https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=744510")
        self.assertEqual(first.posted_at, "2026-10-31T11:10:00+09:00")
        self.assertTrue(posts[2].ended)
        self.assertEqual(posts[2].views, 12345)

    def test_ruliweb_rss_category_prefix(self):
        posts = community.parse_generic_rss(read("ruliweb.xml"), "ruliweb")
        self.assertEqual(len(posts), 2)  # '삭제된 글' 제외
        self.assertEqual(posts[0].category, "PC/하드웨어")
        self.assertTrue(posts[0].title.startswith("[11번가]"))
        self.assertNotIn(":443", posts[0].url)

    def test_quasarzone(self):
        posts = community.parse_quasarzone(read("quasarzone.html"))
        self.assertEqual([p.id for p in posts], ["1790123", "1790100"])  # 파트너 광고·잠긴 글 제외
        p = posts[0]
        self.assertEqual((p.price, p.shipping, p.votes, p.views, p.comments), ("￦189,000 (KRW)", "무료", 14, 4100, 21))
        self.assertEqual(p.category, "PC/하드웨어")
        self.assertTrue(posts[1].ended)

    def test_arcalive(self):
        posts = community.parse_arcalive(read("arcalive.html"))
        self.assertEqual([p.id for p in posts], ["151000003", "151000001"])  # 공지·가격 칸 없는 글 제외
        p = posts[0]
        self.assertEqual((p.title, p.mall, p.price, p.shipping), ("다이슨 V15 디텍트", "SSG닷컴", "614,600원", "무료"))
        self.assertEqual((p.votes, p.views, p.comments), (58, 15000, 120))
        self.assertEqual(p.posted_at, "2026-10-31T20:40:00+09:00")
        self.assertTrue(posts[1].ended)

    def test_clien(self):
        posts = community.parse_clien(read("clien.html"))
        self.assertEqual([p.id for p in posts], ["19210001", "19210000"])
        self.assertEqual(posts[0].title, "[G마켓] 로지텍 MX Master 3S 무선 마우스 (95,940원/무료)")
        self.assertEqual((posts[0].votes, posts[0].views), (25, 6120))
        self.assertTrue(posts[1].ended)

    def test_fmkorea(self):
        posts = community.parse_fmkorea(read("fmkorea.html"))
        self.assertEqual(posts[0].title, "닌텐도 스위치 2 마리오카트 월드 세트")
        self.assertEqual((posts[0].mall, posts[0].price, posts[0].comments), ("쿠팡", "616,400원", 19))
        self.assertTrue(posts[1].ended)

    def test_every_source_has_parser(self):
        for s in community.SOURCES:
            self.assertTrue(callable(s.parser), s.id)
            self.assertTrue(s.url.startswith("https://"), s.id)


class DanawaTest(unittest.TestCase):
    def test_search_legacy_with_options(self):
        cands, strategy = danawa.parse_search(read("danawa_search_legacy.html"))
        self.assertEqual(strategy, "legacy")
        self.assertEqual([c.pcode for c in cands], ["20480012", "20480099"])  # 광고 제외
        c = cands[0]
        self.assertEqual([(o.pcode, o.label, o.price, o.malls) for o in c.options],
                         [("20480011", "1TB", 149000, 32), ("20480012", "2TB", 249000, 41), ("20480013", "4TB", 469000, 18)])
        self.assertTrue(c.image.startswith("https://img.danawa.com/"))  # 지연 로딩 이미지 주소
        self.assertEqual(cands[1].variant, {"overseas"})

    def test_search_new_layout(self):
        cands, strategy = danawa.parse_search(read("danawa_search_dnw.html"))
        self.assertEqual(strategy, "dnw")
        self.assertEqual(cands[0].name, "삼성전자 갤럭시 버즈3 프로 SM-R630")
        self.assertEqual([(o.pcode, o.label, o.price, o.malls) for o in cands[0].options],
                         [("20480101", "자급제", 239000, 88), ("20480102", "벌크 병행", 199000, 4)])

    def test_search_nothing(self):
        self.assertEqual(danawa.parse_search("<html><body><p>검색결과가 없습니다.</p></body></html>"), ([], "none"))

    def test_product_list_item(self):
        page = danawa.parse_product(read("danawa_product_listitem.html"), "20480241")
        self.assertEqual(page.strategy, "jsonld-aggregate+list-item")
        self.assertEqual((page.name, page.lowest, page.highest, page.offer_count), ("제주삼다수 2L (24개)", 23900, 31500, 57))
        malls = [o.mall for o in page.offers]
        self.assertEqual(malls, ["쿠팡", "G마켓", "SSG닷컴", "11번가", "롯데ON"])  # G마켓은 더 싼 값 하나만
        self.assertEqual(page.offers[2].shipping_fee, 3000)

    def test_product_diff_item(self):
        page = danawa.parse_product(read("danawa_product_diff.html"), "20480041")
        self.assertEqual(page.strategy, "diff_item")
        self.assertEqual(page.name, "로지텍 MX Master 3S")
        self.assertEqual(len(page.offers), 5)
        self.assertEqual(page.offers[-1].shipping_fee, 2500)

    def test_product_jsonld_offers(self):
        page = danawa.parse_product(read("danawa_product_jsonld_offers.html"), "20480311")
        self.assertEqual(page.strategy, "jsonld-offers")
        self.assertEqual([(o.mall, o.price) for o in page.offers], [("네이버", 145000), ("쿠팡", 149000), ("G마켓", 152000)])

    def test_product_without_mall_list(self):
        html = ('<html><head><title>APPLE 에어팟 프로3 : 다나와 가격비교</title></head><body>'
                '<h3 class="prod_tit"><span class="title">APPLE 에어팟 프로3</span></h3>'
                '<div class="lowest_area"><em class="prc_c">291,200</em>원</div></body></html>')
        page = danawa.parse_product(html, "1")
        self.assertEqual((page.name, page.lowest, page.offers, page.strategy), ("APPLE 에어팟 프로3", 291200, [], "none"))


if __name__ == "__main__":
    unittest.main()
