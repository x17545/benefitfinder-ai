"""Virtual regression tests for exact F12 won-literal extraction."""

from unittest import TestCase

from benefitfinder.ai.policy_amounts import won_mentions


class PolicyAmountTests(TestCase):
    def test_shared_unit_range_keeps_both_bounds_and_exact_span(self):
        text = "가상 안내: 월 73~117만원입니다."

        (mention,) = won_mentions(text)

        self.assertEqual(text[mention.start:mention.end], "73~117만원")
        self.assertEqual(mention.amounts_won, (730_000, 1_170_000))

    def test_explicit_units_and_mixed_units_form_one_range(self):
        text = "73만~117만원, 다음은 5,000원~1.5만원입니다."

        mentions = won_mentions(text)

        self.assertEqual(
            [text[item.start:item.end] for item in mentions],
            ["73만~117만원", "5,000원~1.5만원"],
        )
        self.assertEqual(
            [item.amounts_won for item in mentions],
            [(730_000, 1_170_000), (5_000, 15_000)],
        )

    def test_single_units_commas_and_decimal_units_convert_exactly(self):
        text = "1,000원, 1.5천원, 1.2345만원, 0.0001억원"

        mentions = won_mentions(text)

        self.assertEqual(
            [text[item.start:item.end] for item in mentions],
            ["1,000원", "1.5천원", "1.2345만원", "0.0001억원"],
        )
        self.assertEqual(
            [item.amounts_won for item in mentions],
            [(1_000,), (1_500,), (12_345,), (10_000,)],
        )

    def test_non_integral_won_and_malformed_grouping_are_unresolved(self):
        text = "1.5원, 1.2345천원, 2,50만원"

        self.assertEqual(won_mentions(text), ())

    def test_negative_percent_age_and_date_are_never_won(self):
        text = "-100만원, 19세, 50%, 2026.11.13원"

        self.assertEqual(won_mentions(text), ())

    def test_compound_units_do_not_emit_suffix_amounts(self):
        text = "2억3천만원, 1억 2만원, 1만 5000원"

        self.assertEqual(won_mentions(text), ())

    def test_arithmetic_expression_is_left_uninterpreted(self):
        text = "100만원 + 200만원 = 300만원"

        self.assertEqual(won_mentions(text), ())

    def test_reversed_range_is_not_reordered_or_confirmed(self):
        self.assertEqual(won_mentions("117~73만원"), ())

    def test_occurrences_are_neither_deduplicated_nor_amount_sorted(self):
        text = "100만원, 100만원, 50~75만원"

        mentions = won_mentions(text)

        self.assertEqual(
            [item.amounts_won for item in mentions],
            [(1_000_000,), (1_000_000,), (500_000, 750_000)],
        )
        self.assertEqual(
            [text[item.start:item.end] for item in mentions],
            ["100만원", "100만원", "50~75만원"],
        )
