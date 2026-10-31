"""게시글 제목 해석과 상품명 정규화 (실제 게시판 제목 형식 기준)."""
import unittest

from collector.textnorm import (is_variant_marker, model_tokens, normalize_mall, parse_price_text, parse_shipping, parse_title,
                                quantities, search_query)


class TitleTest(unittest.TestCase):
    def check(self, title, **expect):
        tp = parse_title(title)
        for k, v in expect.items():
            self.assertEqual(getattr(tp, k), v, f"{title!r} → {k}")
        return tp

    def test_common_formats(self):
        self.check("[G마켓] 펩시제로 라임 1.5L 12펫 / (최종가 13,760₩)", mall="G마켓", price=13760, name="펩시제로 라임 1.5L 12펫")
        self.check("[네이버] 카누 미니 마일드 아메리카노 120개입 2개 (36,900원/무료)", mall="네이버", price=36900, shipping_fee=0)
        self.check("[쿠팡] 삼성 990 PRO 2TB (189,000원/2,500원)", price=189000, shipping_fee=2500, name="삼성 990 PRO 2TB")
        self.check("[11번가] 메가커피 아이스 아메리카노 (980원/무료)", price=980)
        self.check("[네이버] 메가MGC커피 아메리카노(ICED)_10/31 1차 (네멤 1,410원/무료)", price=1410)
        self.check("[코스트코] 네스카페 돌체구스토 아메리카노 캡슐커피 60개 (27,490원/무배)", price=27490, shipping_fee=0)

    def test_category_then_mall(self):
        self.check("[게임][스팀] 엘든 링 나이트레인 (25,800원/무료)", mall="스팀", name="엘든 링 나이트레인")

    def test_truncated_and_status(self):
        tp = self.check("[품절] [쿠팡] 삼성 990 PRO 2TB (189,000원/무료)", ended=True, mall="쿠팡", price=189000)
        self.assertNotIn("품절", tp.name)
        self.check("[11번가] 삼성전자 990 PRO 2TB NVMe SSD (201,200원/무료...", price=201200)

    def test_flags(self):
        self.assertIn("non_product", parse_title("[네이버] 컬쳐랜드상품권 5만원 (46,310원/무료)").flags)
        self.assertIn("non_product", parse_title("[카카오톡] 서브웨이 2만원 금액권 (17,000원/무료)").flags)
        self.assertIn("overseas", parse_title("[알리익스프레스] 8BitDo 얼티밋 2 컨트롤러 ($39.99/무료)").flags)
        self.assertIn("digital", parse_title("[스팀] 엘든 링 (25,800원/무료)").flags)
        self.assertTrue(parse_title("[네이버] 노브랜드 양말 (12,900원~19,900원/무료)").multi_price)

    def test_board_fields_take_priority(self):
        tp = parse_title("다이슨 V15 디텍트", mall="SSG닷컴", price="614,600원", shipping="무료")
        self.assertEqual((tp.mall, tp.price, tp.shipping_fee, tp.name), ("SSG닷컴", 614600, 0, "다이슨 V15 디텍트"))


class PriceShippingTest(unittest.TestCase):
    def test_prices(self):
        self.assertEqual(parse_price_text("￦ 189,000 (KRW)"), (189000, "KRW", False))
        self.assertEqual(parse_price_text("1.2만원")[0], 12000)
        self.assertEqual(parse_price_text("$17.20"), (1720, "USD_CENT", False))
        self.assertEqual(parse_price_text("다양"), (None, None, False))
        self.assertEqual(parse_price_text("카드 12,900원 → 최종가 10,900원")[0], 10900)

    def test_shipping(self):
        self.assertEqual(parse_shipping("무료"), ("무료배송", 0))
        self.assertEqual(parse_shipping("무배")[1], 0)
        self.assertEqual(parse_shipping("배송비 2,500원")[1], 2500)
        self.assertEqual(parse_shipping("2,500원")[1], 2500)   # '500원' 속 '0원'을 무료로 착각하지 않기
        self.assertEqual(parse_shipping("3000원")[1], 3000)
        self.assertEqual(parse_shipping("0원")[1], 0)
        self.assertEqual(parse_shipping("10,000원 이상 무료")[1], 0)
        self.assertEqual(parse_shipping("착불")[1], None)


class NameTest(unittest.TestCase):
    def test_quantities(self):
        self.assertEqual(quantities("펩시제로 라임 1.5L 12펫"), {"ml": 1500, "count": 12})
        self.assertEqual(quantities("카누 미니 120개입 2개")["count"], 240)
        self.assertEqual(quantities("삼다수 2L x 24")["count"], 24)
        self.assertEqual(quantities("990 PRO 2TB")["gb"], 2048)
        self.assertEqual(quantities("햇반 1+1")["count"], 2)
        self.assertNotIn("count", quantities("라면 5개 + 김치 1개"))  # 서로 다른 상품 묶음
        self.assertEqual(quantities("메타 퀘스트 VR2"), {})

    def test_model_tokens(self):
        self.assertEqual(model_tokens("MSI 지포스 RTX 5080 게이밍"), {"rtx5080"})
        self.assertIn("wh-1000xm6", model_tokens("소니 WH-1000XM6"))
        self.assertEqual(model_tokens("삼성 990 PRO 2TB"), set())

    def test_search_query_drops_promo(self):
        q = search_query("[역대가] 로지텍 MX Master 3S 무선 마우스 쿠폰 적용 특가")
        self.assertEqual(q, "로지텍 MX Master 3S 무선 마우스")

    def test_malls_and_variants(self):
        self.assertEqual(normalize_mall("SSG.COM"), "SSG닷컴")
        self.assertEqual(normalize_mall("지마켓"), "G마켓")
        self.assertEqual(normalize_mall("하이마트"), "롯데하이마트")
        self.assertEqual(is_variant_marker("RTX 5070 (해외구매)"), {"overseas"})
        self.assertEqual(is_variant_marker("벌크 병행"), {"bulk", "overseas"})


if __name__ == "__main__":
    unittest.main()
