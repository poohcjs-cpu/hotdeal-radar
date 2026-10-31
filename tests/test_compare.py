"""같은 제품 찾기와 '쇼핑몰 평균가' 계산."""
import unittest

from collector.compare import market_deal, match_deal, offer_stats, verify_deal
from collector.danawa import Candidate, Offer, Option, ProductPage


def cand(pcode, name, price, options=(), variant=()):
    return Candidate(pcode, name, price, None, "", list(options), set(variant))


def page(offers, name="상품", pcode="1", lowest=None):
    offs = sorted((Offer(m, p, s, f) for m, p, s, f in offers), key=lambda o: o.price)
    return ProductPage(pcode, name, lowest or (offs[0].price if offs else None), None, None, "", "", offs)


class MatchTest(unittest.TestCase):
    def test_picks_same_model(self):
        cands = [cand("1", "MSI 지포스 RTX 5080 게이밍 트리오 OC", 1890000), cand("2", "MSI 지포스 RTX 5070 게이밍 트리오 OC", 1049000)]
        m = match_deal("MSI RTX 5070 게이밍 트리오 OC 12GB", 859000, cands)
        self.assertEqual(m.pcode, "2")

    def test_rejects_different_model_and_variant(self):
        self.assertIsNone(match_deal("소니 WH-1000XM5", 300000, [cand("1", "소니 WH-1000XM6", 559000)]))
        self.assertIsNone(match_deal("RTX 5070 게이밍 트리오", 900000, [cand("1", "MSI RTX 5070 게이밍 트리오 (해외구매)", 899000,
                                                                         variant={"overseas"})]))

    def test_option_by_capacity(self):
        c = cand("12", "삼성전자 990 PRO M.2 NVMe", 149000, [Option("11", "1TB", 149000), Option("12", "2TB", 249000),
                                                              Option("13", "4TB", 469000)])
        self.assertEqual(match_deal("삼성 990 PRO 2TB", 189000, [c]).pcode, "12")
        self.assertIsNone(match_deal("삼성 990 PRO 8TB", 900000, [c]))  # 맞는 용량이 없음

    def test_skips_bulk_option_for_retail_deal(self):
        c = cand("101", "삼성전자 갤럭시 버즈3 프로 SM-R630", 199000, [Option("101", "자급제", 239000), Option("102", "벌크 병행", 199000)])
        m = match_deal("삼성 갤럭시 버즈3 프로", 173600, [c])
        self.assertEqual((m.pcode, m.option.label), ("101", "자급제"))

    def test_unit_price_when_counts_differ(self):
        c = cand("241", "제주삼다수 2L (12개)", 13900)
        m = match_deal("제주삼다수 2L 24병", 21000, [c])
        self.assertAlmostEqual(m.unit_ratio, 2.0)

    def test_deal_name_with_extra_words(self):
        m = match_deal("로지텍 MX Master 3S 무선 마우스", 95940, [cand("41", "로지텍 MX Master 3S", 39000)])
        self.assertIsNotNone(m)  # 다나와 최저가가 오등록(39,000원)이어도 이름이 같으면 찾는다

    def test_rejects_accessory_price(self):
        self.assertIsNone(match_deal("갤럭시 S25 256GB 자급제 케이스", 9900, [cand("1", "삼성전자 갤럭시 S25 256GB 자급제", 1155000)]))


class StatsTest(unittest.TestCase):
    def test_outliers_and_exclusion(self):
        offers = [Offer("옥션", 39000), Offer("네이버", 129000), Offer("쿠팡", 132000), Offer("G마켓", 139000), Offer("11번가", 141000)]
        s = offer_stats(offers)
        self.assertEqual((s["count"], s["dropped"], s["lowest"]), (4, 1, 129000))
        self.assertEqual(s["average"], 135250 // 10 * 10)
        s2 = offer_stats(offers, exclude_mall="지마켓")
        self.assertEqual(s2["count"], 3)

    def test_verify_against_other_malls_average(self):
        p = page([("쿠팡", 100000, "무료배송", 0), ("G마켓", 110000, "무료배송", 0), ("11번가", 120000, "무료배송", 0),
                  ("SSG닷컴", 130000, "무료배송", 0)])
        m = match_deal("테스트 상품", 70000, [cand("1", "테스트 상품", 100000)])
        v = verify_deal(70000, 0, "쿠팡", p, m)
        self.assertEqual((v["status"], v["reference"], v["referencePrice"], v["malls"], v["basis"]), ("ok", "average", 120000, 3, "total"))
        self.assertAlmostEqual(v["rate"], 41.7, places=1)

    def test_verify_price_basis_when_shipping_unknown(self):
        p = page([("쿠팡", 100000, "", None), ("G마켓", 110000, "", None), ("11번가", 120000, "", None)])
        m = match_deal("테스트 상품", 70000, [cand("1", "테스트 상품", 100000)])
        v = verify_deal(70000, 3000, "네이버", p, m)
        self.assertEqual((v["basis"], v["dealTotal"]), ("price", 70000))

    def test_verify_falls_back_to_lowest(self):
        p = page([("쿠팡", 100000, "", None), ("G마켓", 110000, "", None)])
        m = match_deal("테스트 상품", 80000, [cand("1", "테스트 상품", 100000)])
        v = verify_deal(80000, None, "옥션", p, m)
        self.assertEqual((v["reference"], v["referencePrice"], v["rate"]), ("lowest", 100000, 20.0))

    def test_verify_marks_suspicious_and_rejects_far_off(self):
        p = page([("쿠팡", 100000, "", None), ("G마켓", 100000, "", None), ("11번가", 100000, "", None)])
        m = match_deal("테스트 상품", 30000, [cand("1", "테스트 상품", 100000)])
        self.assertTrue(verify_deal(30000, None, "", p, m)["suspicious"])
        self.assertEqual(verify_deal(250000, None, "", p, m)["status"], "nomatch")

    def test_market_deal_skips_absurd_lowest(self):
        p = page([("옥션", 39000, "무료배송", 0), ("네이버", 90000, "무료배송", 0), ("쿠팡", 132000, "무료배송", 0),
                  ("G마켓", 139000, "무료배송", 0), ("11번가", 141000, "무료배송", 0)])
        md = market_deal(p)
        self.assertEqual((md["bestMall"], md["skippedOutliers"]), ("네이버", 1))
        self.assertEqual(md["othersAverage"], 137330)
        self.assertAlmostEqual(md["rate"], 34.5, places=1)

    def test_market_deal_needs_enough_malls(self):
        self.assertIsNone(market_deal(page([("쿠팡", 1000, "", None), ("G마켓", 1100, "", None)])))


if __name__ == "__main__":
    unittest.main()
