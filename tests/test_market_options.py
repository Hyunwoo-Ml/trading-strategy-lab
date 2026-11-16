from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from sw1.market.options import ExpiryChain, analyze_chains, bs_gamma, find_gamma_flip, total_gex_at

AS_OF = date(2026, 11, 13)


def side(strikes, oi, iv=0.3, volume=None):
    if not isinstance(oi, (list, tuple)):
        oi = [oi] * len(strikes)
    if volume is None:
        volume = [0] * len(strikes)
    return pd.DataFrame({"strike": strikes, "openInterest": oi, "volume": volume, "impliedVolatility": [iv] * len(strikes)})


STRIKES = list(range(80, 125, 5))  # 80..120


def chain(calls, puts, days=30):
    return ExpiryChain(expiry=AS_OF + timedelta(days=days), calls=calls, puts=puts)


def test_bs_gamma_matches_textbook_value():
    # S=K=100, T=1, sigma=0.2, r=0 -> d1=0.1, gamma = phi(0.1)/(S*sigma*sqrt(T))
    expected = math.exp(-0.5 * 0.1**2) / math.sqrt(2 * math.pi) / (100 * 0.2)
    assert bs_gamma(100.0, [100.0], 1.0, [0.2])[0] == pytest.approx(expected, rel=1e-9)


def test_calls_only_is_positive_gamma_with_no_flip():
    snap = analyze_chains([chain(side(STRIKES, 1000), side(STRIKES, 0))], spot=100.0, as_of=AS_OF)
    assert snap.total_gex > 0
    assert snap.regime == "positive"
    assert snap.gamma_flip is None
    assert snap.call_wall == 100.0  # equal OI -> the at-the-money strike has the most gamma
    assert snap.put_wall is None


def test_puts_only_is_negative_gamma():
    snap = analyze_chains([chain(side(STRIKES, 0), side(STRIKES, 1000))], spot=100.0, as_of=AS_OF)
    assert snap.total_gex < 0
    assert snap.regime == "negative"
    assert snap.put_wall == 100.0


def test_flip_sits_between_put_heavy_strikes_below_and_call_heavy_strikes_above():
    calls = side(STRIKES, [0, 0, 0, 0, 0, 0, 5000, 5000, 5000])  # 110-120
    puts = side(STRIKES, [5000, 5000, 5000, 0, 0, 0, 0, 0, 0])  # 80-90
    chains = [chain(calls, puts)]
    flip = find_gamma_flip(chains, 100.0, AS_OF)
    assert flip is not None and 90 < flip < 110
    # GEX really does change sign across the flip
    assert total_gex_at(chains, flip - 3, AS_OF) < 0 < total_gex_at(chains, flip + 3, AS_OF)


def test_regime_is_flip_when_spot_is_within_one_percent_of_it():
    calls = side(STRIKES, [0, 0, 0, 0, 0, 0, 5000, 5000, 5000])
    puts = side(STRIKES, [5000, 5000, 5000, 0, 0, 0, 0, 0, 0])
    chains = [chain(calls, puts)]
    flip = find_gamma_flip(chains, 100.0, AS_OF)
    snap = analyze_chains(chains, spot=flip, as_of=AS_OF)
    assert snap.regime == "flip"


def test_expected_move_uses_nearest_expiry_atm_iv():
    near = chain(side(STRIKES, 100, iv=0.30), side(STRIKES, 100, iv=0.30), days=3)
    far = chain(side(STRIKES, 100, iv=0.90), side(STRIKES, 100, iv=0.90), days=40)
    snap = analyze_chains([far, near], spot=100.0, as_of=AS_OF)
    assert snap.atm_iv == pytest.approx(0.30)
    assert snap.expected_move_pct == pytest.approx(round(0.30 * math.sqrt(1 / 252) * 100, 2))
    assert [e.days for e in snap.by_expiry] == [3, 40]  # sorted nearest first
    assert sum(e.share for e in snap.by_expiry) == pytest.approx(1.0, abs=1e-3)


def test_put_call_ratios_by_oi_and_volume():
    calls = side(STRIKES, 100, volume=[10] * len(STRIKES))
    puts = side(STRIKES, 200, volume=[30] * len(STRIKES))
    snap = analyze_chains([chain(calls, puts)], spot=100.0, as_of=AS_OF)
    assert snap.pc_ratio_oi == pytest.approx(2.0)
    assert snap.pc_ratio_volume == pytest.approx(3.0)
    assert snap.call_oi == 100 * len(STRIKES)


def test_bad_iv_rows_are_left_out_of_gex_but_still_count_toward_oi():
    calls = side(STRIKES, 1000, iv=0.00001)  # Yahoo's after-hours ~0 IV
    puts = side(STRIKES, 1000, iv=0.3)
    snap = analyze_chains([chain(calls, puts)], spot=100.0, as_of=AS_OF)
    assert snap.total_gex < 0  # only the puts contribute gamma
    assert snap.call_oi == 1000 * len(STRIKES)
    assert snap.expected_move_pct is not None  # puts still give an ATM IV


def test_expired_chains_are_ignored_and_none_left_raises():
    expired = ExpiryChain(expiry=AS_OF - timedelta(days=1), calls=side(STRIKES, 1000), puts=side(STRIKES, 0))
    with pytest.raises(ValueError):
        analyze_chains([expired], spot=100.0, as_of=AS_OF)


def test_big_oi_strikes_list_calls_above_and_puts_below_spot():
    calls = side(STRIKES, [0, 0, 0, 0, 0, 10, 50, 900, 10])  # biggest above spot: 115
    puts = side(STRIKES, [10, 800, 20, 0, 0, 0, 0, 0, 0])  # biggest below: 85
    snap = analyze_chains([chain(calls, puts)], spot=100.0, as_of=AS_OF)
    tops = {(b["side"], b["strike"]) for b in snap.big_oi_strikes}
    assert ("call", 115.0) in tops and ("put", 85.0) in tops
