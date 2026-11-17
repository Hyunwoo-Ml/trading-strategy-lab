from __future__ import annotations

from datetime import date

import pytest

from sw1.market.fedwatch import contract_code, next_meeting, odds_from_prices

DEC = date(2026, 12, 9)  # 9 of 31 days at the old rate


def price_for(r_start, r_end, meeting=DEC, n=31):
    d = meeting.day
    return 100 - ((d / n) * r_start + ((n - d) / n) * r_end)


def test_next_meeting_and_contract_codes():
    assert next_meeting(date(2026, 11, 17)) == DEC
    assert next_meeting(DEC) == DEC  # decision day itself still counts
    assert next_meeting(date(2026, 12, 10)) == date(2027, 1, 27)
    assert contract_code(2026, 12) == "Z26" and contract_code(2027, 1) == "F27"


def test_fully_priced_hold():
    o = odds_from_prices(DEC, price_for(3.88, 3.88), 100 - 3.88, target_upper=4.0)
    assert o.hold == pytest.approx(1.0) and o.cut == 0 and o.hike == 0
    assert o.current_range == "3.75–4.00%"


def test_partly_priced_cut():
    o = odds_from_prices(DEC, price_for(3.88, 3.88 - 0.25 * 0.7), 100 - 3.88)
    assert o.cut == pytest.approx(0.7, abs=1e-3) and o.hold == pytest.approx(0.3, abs=1e-3)
    assert o.move_bp == 25 and o.implied_change_bp == pytest.approx(-17.5, abs=0.1)


def test_more_than_one_step_splits_between_25_and_50bp():
    o = odds_from_prices(DEC, price_for(3.88, 3.88 - 0.25 * 1.4), 100 - 3.88)
    assert o.cut == pytest.approx(1.0) and o.hold == 0 and o.move_bp == 25  # 60% on 25bp, 40% on 50bp
    assert [x["change_bp"] for x in o.outcomes] == [-25, -50]


def test_hike():
    o = odds_from_prices(DEC, price_for(3.88, 3.88 + 0.25 * 0.2), 100 - 3.88)
    assert o.hike == pytest.approx(0.2, abs=1e-3) and o.hold == pytest.approx(0.8, abs=1e-3)


def test_previous_month_with_a_meeting_uses_the_effective_rate():
    jan = date(2027, 1, 27)  # December has a meeting, so Dec's contract can't be the start rate
    o = odds_from_prices(jan, price_for(3.63, 3.63, jan, 31), price_prev_month=99.0, effective_rate=3.63)
    assert o.hold == pytest.approx(1.0, abs=1e-3)
    with pytest.raises(ValueError):
        odds_from_prices(jan, 96.0, price_prev_month=99.0)


def test_next_month_contract_is_used_for_the_end_rate_when_it_has_no_meeting():
    jan = date(2027, 1, 27)  # Feb 2027 has no meeting
    o = odds_from_prices(jan, 96.0, price_prev_month=None, effective_rate=3.63, price_next_month=100 - 3.38)
    assert o.cut == pytest.approx(1.0, abs=1e-3) and o.move_bp == 25


def test_outcomes_split_between_the_two_nearest_moves():
    # what the market priced on 2026-11-17: Nov 96.12, Dec 95.93 -> about +27bp
    o = odds_from_prices(DEC, 95.93, 96.12)
    assert o.hike == pytest.approx(1.0) and o.move_bp == 25
    assert [x["change_bp"] for x in o.outcomes] == [25, 50]
    assert o.outcomes[0]["prob"] == pytest.approx(0.93, abs=0.01)
