"""
SW1 -- option-chain levels: dealer gamma exposure (GEX), call/put walls,
gamma flip, expected move and put/call ratios.

Pure functions over option-chain tables, so everything here is testable
without network access. scripts/build_market_dashboard.py fetches the
chains (yfinance) and passes them in.

Convention (the common public one): dealers are assumed long the calls
and short the puts that customers hold, so call gamma counts positive and
put gamma negative. GEX is expressed in dollars per 1% move of the
underlying:

    GEX = gamma * open_interest * 100 * spot**2 * 0.01

Positive total GEX means dealer hedging tends to dampen moves; negative
means it tends to amplify them. The gamma flip is the spot level at which
the total crosses zero.

These are estimates from end-of-day open interest and implied
volatilities. Nobody outside the dealers knows their actual positioning.

Yahoo data quality: outside US trading hours Yahoo often reports a
placeholder IV (about 0.0156) and zero open interest. IVs at or below
MIN_IV are therefore recomputed from the option's own price (bid/ask mid,
else last trade) with Black-Scholes, and `chain_quality()` lets the caller
reject a snapshot whose open interest is essentially empty instead of
publishing numbers built on zeros.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy.special import ndtr

CONTRACT_MULTIPLIER = 100
TRADING_DAYS_PER_YEAR = 252
MIN_IV = 0.03  # at or below this, Yahoo's IV is a placeholder; recompute it from price
MIN_OPTION_PRICE = 0.01  # one cent: below this an IV can't be read from the price
MIN_TOTAL_OI = 500  # fewer contracts than this across all chains = no real OI data
MIN_OI_COVERAGE = 0.10  # and at least this share of listed contracts must have OI
NEAR_MONEY_PCT = 0.15  # "near the money" = strikes within this fraction of spot
MIN_NEAR_MONEY_OI = 200  # contracts needed near the money; after a split Yahoo can list only old far strikes
MAX_IV = 5.0
MIN_T_YEARS = 0.5 / 365  # floor so same-day expiries don't divide by ~0
FLIP_NEAR_PCT = 0.01  # within 1% of the flip counts as "at the flip"


@dataclass
class ExpiryChain:
    """One expiry's calls and puts. Each frame needs strike, openInterest,
    volume and impliedVolatility columns (yfinance's option_chain shape)."""

    expiry: date
    calls: pd.DataFrame
    puts: pd.DataFrame


@dataclass
class ExpirySummary:
    expiry: str
    days: int
    gex: float
    share: float  # this expiry's gross (call + put) gamma as a share of all expiries'


@dataclass
class OptionsSnapshot:
    spot: float
    as_of: str
    total_gex: float
    regime: str  # "positive" | "negative" | "flip"
    gamma_flip: float | None
    call_wall: float | None
    put_wall: float | None
    day1_call_wall: float | None
    day1_put_wall: float | None
    day1_flip: float | None
    expected_move_pct: float | None  # 1-day, from nearest-expiry ATM IV
    atm_iv: float | None
    pc_ratio_oi: float | None
    pc_ratio_volume: float | None
    call_oi: int
    put_oi: int
    by_expiry: list[ExpirySummary] = field(default_factory=list)
    gex_by_strike: list[dict] = field(default_factory=list)  # [{strike, call_gex, put_gex, call_oi, put_oi}]
    big_oi_strikes: list[dict] = field(default_factory=list)  # [{strike, side, oi}]


def bs_gamma(spot: float | np.ndarray, strike: np.ndarray, t_years: float, iv: np.ndarray, rate: float = 0.0) -> np.ndarray:
    """Black-Scholes gamma per share. Vectorised over strikes/IVs."""
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    iv = np.asarray(iv, dtype=float)
    t = max(t_years, MIN_T_YEARS)
    sqrt_t = math.sqrt(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / strike) + (rate + 0.5 * iv**2) * t) / (iv * sqrt_t)
        gamma = np.exp(-0.5 * d1**2) / (math.sqrt(2 * math.pi) * spot * iv * sqrt_t)
    return np.nan_to_num(gamma, nan=0.0, posinf=0.0, neginf=0.0)


def bs_price(spot: float, strike: np.ndarray, t_years: float, iv: np.ndarray, is_call: bool, rate: float = 0.0) -> np.ndarray:
    """Black-Scholes price per share. Vectorised over strikes/IVs."""
    strike = np.asarray(strike, dtype=float)
    iv = np.asarray(iv, dtype=float)
    t = max(t_years, MIN_T_YEARS)
    sqrt_t = math.sqrt(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / strike) + (rate + 0.5 * iv**2) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    disc = math.exp(-rate * t)
    if is_call:
        return spot * ndtr(d1) - strike * disc * ndtr(d2)
    return strike * disc * ndtr(-d2) - spot * ndtr(-d1)


def implied_vol(price: np.ndarray, spot: float, strike: np.ndarray, t_years: float, is_call: bool,
                lo: float = MIN_IV, hi: float = MAX_IV, iterations: int = 60) -> np.ndarray:
    """IV by bisection, vectorised. NaN where the price is missing, under a
    cent, or not inside the range Black-Scholes can produce between `lo`
    and `hi`."""
    price = np.asarray(price, dtype=float)
    strike = np.asarray(strike, dtype=float)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    p_lo = bs_price(spot, strike, t_years, a, is_call)
    p_hi = bs_price(spot, strike, t_years, b, is_call)
    solvable = np.isfinite(price) & (price >= MIN_OPTION_PRICE) & (price >= p_lo) & (price <= p_hi)
    for _ in range(iterations):
        mid = (a + b) / 2
        too_low = bs_price(spot, strike, t_years, mid, is_call) < price
        a = np.where(too_low, mid, a)
        b = np.where(too_low, b, mid)
    return np.where(solvable, (a + b) / 2, np.nan)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["strike", "openInterest", "volume", "impliedVolatility"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols + ["price"])
    out = df[cols].copy()
    out["openInterest"] = pd.to_numeric(out["openInterest"], errors="coerce").fillna(0).clip(lower=0)
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).clip(lower=0)
    out["impliedVolatility"] = pd.to_numeric(out["impliedVolatility"], errors="coerce")
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    bid = pd.to_numeric(df["bid"], errors="coerce") if "bid" in df else pd.Series(np.nan, index=df.index)
    ask = pd.to_numeric(df["ask"], errors="coerce") if "ask" in df else pd.Series(np.nan, index=df.index)
    last = pd.to_numeric(df["lastPrice"], errors="coerce") if "lastPrice" in df else pd.Series(np.nan, index=df.index)
    mid = (bid + ask) / 2
    out["price"] = np.where((bid > 0) & (ask > 0), mid, last)
    return out.dropna(subset=["strike"])


def fill_placeholder_iv(df: pd.DataFrame, spot: float, t_years: float, is_call: bool) -> pd.DataFrame:
    """Replaces Yahoo IVs at or below MIN_IV (placeholders) with IV implied
    by the contract's own price; rows with no usable price keep NaN."""
    out = df.copy()
    bad = ~(out["impliedVolatility"] > MIN_IV)
    if bad.any():
        out.loc[bad, "impliedVolatility"] = implied_vol(
            out.loc[bad, "price"].to_numpy(), spot, out.loc[bad, "strike"].to_numpy(), t_years, is_call
        )
    return out


def chain_quality(chains: list["ExpiryChain"], spot: float | None = None) -> tuple[bool, str]:
    """Whether the snapshot has real open interest. Off-hours Yahoo data
    often has OI zeroed out across the board, and right after a split the
    chain can hold OI only on old, far-away strikes. With a spot price the
    check looks only at strikes near the money."""
    total, listed, with_oi = 0.0, 0, 0
    for ch in chains:
        for df in (ch.calls, ch.puts):
            if df is None or len(df) == 0:
                continue
            oi = pd.to_numeric(df["openInterest"], errors="coerce").fillna(0)
            if spot is not None:
                strikes = pd.to_numeric(df["strike"], errors="coerce")
                oi = oi[(strikes - spot).abs() <= NEAR_MONEY_PCT * spot]
            total += float(oi.sum())
            listed += len(oi)
            with_oi += int((oi > 0).sum())
    where = "near the money" if spot is not None else "listed"
    if listed == 0:
        return False, f"no contracts {where}"
    coverage = with_oi / listed
    floor = MIN_NEAR_MONEY_OI if spot is not None else MIN_TOTAL_OI
    if total < floor or coverage < MIN_OI_COVERAGE:
        return False, f"open interest looks empty {where} (total {int(total)}, {coverage:.0%} of contracts have any)"
    return True, "ok"


def _usable_iv(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["impliedVolatility"] >= MIN_IV) & (df["impliedVolatility"] <= MAX_IV)]


def _years_to(expiry: date, as_of: date) -> float:
    # Options stop trading at the close on expiry day; count the expiry day itself.
    return max((expiry - as_of).days + 1, 0) / 365.0


def _gex_rows(df: pd.DataFrame, spot: float, t_years: float, sign: int) -> np.ndarray:
    df = _usable_iv(df)
    if len(df) == 0:
        return np.zeros(0)
    g = bs_gamma(spot, df["strike"].to_numpy(), t_years, df["impliedVolatility"].to_numpy())
    return sign * g * df["openInterest"].to_numpy() * CONTRACT_MULTIPLIER * spot**2 * 0.01


def _prepared(chain: ExpiryChain, side: str, as_of: date) -> pd.DataFrame:
    """Cleaned side of a chain with placeholder IVs recomputed. IVs are
    priced off `chain.spot_for_iv` (set by analyze_chains) and cached on the
    chain itself, so the grid search in find_gamma_flip doesn't re-solve
    them at every grid point."""
    spot = getattr(chain, "spot_for_iv", None)
    cache = chain.__dict__.setdefault("_prepared_cache", {})
    key = (side, spot, as_of)
    if key not in cache:
        df = _clean(chain.calls if side == "call" else chain.puts)
        if spot:
            df = fill_placeholder_iv(df, spot, max(_years_to(chain.expiry, as_of), MIN_T_YEARS), side == "call")
        cache[key] = df
    return cache[key]


def _contracts(chains: list[ExpiryChain], as_of: date) -> tuple[np.ndarray, ...]:
    """Flattens every usable contract into parallel arrays
    (strike, open interest, iv, years to expiry, +1 call / -1 put)."""
    parts = []
    for ch in chains:
        t = _years_to(ch.expiry, as_of)
        for df, sign in ((_prepared(ch, "call", as_of), +1), (_prepared(ch, "put", as_of), -1)):
            df = _usable_iv(df)
            if len(df):
                n = len(df)
                parts.append((df["strike"].to_numpy(float), df["openInterest"].to_numpy(float),
                              df["impliedVolatility"].to_numpy(float), np.full(n, max(t, MIN_T_YEARS)), np.full(n, sign, float)))
    if not parts:
        return tuple(np.zeros(0) for _ in range(5))
    return tuple(np.concatenate([p[i] for p in parts]) for i in range(5))


def _gex_curve(chains: list[ExpiryChain], spots: np.ndarray, as_of: date, gross: bool = False) -> np.ndarray:
    """Total GEX for each hypothetical spot in `spots` (vectorised)."""
    strike, oi, iv, t, sign = _contracts(chains, as_of)
    spots = np.atleast_1d(np.asarray(spots, dtype=float))
    if len(strike) == 0:
        return np.zeros(len(spots))
    s = spots[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(s / strike) + 0.5 * iv**2 * t) / (iv * np.sqrt(t))
        gamma = np.exp(-0.5 * d1**2) / (math.sqrt(2 * math.pi) * s * iv * np.sqrt(t))
    gamma = np.nan_to_num(gamma, nan=0.0, posinf=0.0, neginf=0.0)
    weight = np.ones_like(sign) if gross else sign
    return (gamma * oi * weight).sum(axis=1) * CONTRACT_MULTIPLIER * spots**2 * 0.01


def total_gex_at(chains: list[ExpiryChain], spot: float, as_of: date) -> float:
    """Total dealer GEX if the underlying were at `spot`, holding IV and OI fixed."""
    return float(_gex_curve(chains, np.array([spot]), as_of)[0])


def find_gamma_flip(chains: list[ExpiryChain], spot: float, as_of: date, span: float = 0.2, steps: int = 161) -> float | None:
    """Spot level nearest the current spot where total GEX changes sign,
    searched over spot*(1-span)..spot*(1+span). None if it never crosses."""
    if spot <= 0 or not chains:
        return None
    grid = np.linspace(spot * (1 - span), spot * (1 + span), steps)
    values = _gex_curve(chains, grid, as_of)
    crossings = []
    for i in range(len(grid) - 1):
        a, b = values[i], values[i + 1]
        if a == 0:
            crossings.append(grid[i])
        elif a * b < 0:
            # linear interpolation between the two grid points
            crossings.append(grid[i] + (grid[i + 1] - grid[i]) * (abs(a) / (abs(a) + abs(b))))
    if not crossings:
        return None
    return float(min(crossings, key=lambda x: abs(x - spot)))


def _by_strike(chains: list[ExpiryChain], spot: float, as_of: date) -> pd.DataFrame:
    rows = []
    for ch in chains:
        t = _years_to(ch.expiry, as_of)
        for side, df, sign in (("call", _prepared(ch, "call", as_of), +1), ("put", _prepared(ch, "put", as_of), -1)):
            usable = _usable_iv(df)
            gex = _gex_rows(df, spot, t, sign)
            for strike, g in zip(usable["strike"].to_numpy(), gex):
                rows.append({"strike": float(strike), f"{side}_gex": float(g)})
            for strike, oi, vol in zip(df["strike"].to_numpy(), df["openInterest"].to_numpy(), df["volume"].to_numpy()):
                rows.append({"strike": float(strike), f"{side}_oi": float(oi), f"{side}_vol": float(vol)})
    if not rows:
        return pd.DataFrame(columns=["strike", "call_gex", "put_gex", "call_oi", "put_oi", "call_vol", "put_vol"])
    out = pd.DataFrame(rows).groupby("strike", as_index=False).sum(numeric_only=True)
    for col in ("call_gex", "put_gex", "call_oi", "put_oi", "call_vol", "put_vol"):
        if col not in out:
            out[col] = 0.0
    return out.fillna(0.0).sort_values("strike").reset_index(drop=True)


def _walls(strikes: pd.DataFrame) -> tuple[float | None, float | None]:
    call_wall = put_wall = None
    if len(strikes) and strikes["call_gex"].max() > 0:
        call_wall = float(strikes.loc[strikes["call_gex"].idxmax(), "strike"])
    if len(strikes) and strikes["put_gex"].min() < 0:
        put_wall = float(strikes.loc[strikes["put_gex"].idxmin(), "strike"])
    return call_wall, put_wall


def _atm_iv(chain: ExpiryChain, spot: float, as_of: date) -> float | None:
    ivs = []
    for df in (_usable_iv(_prepared(chain, "call", as_of)), _usable_iv(_prepared(chain, "put", as_of))):
        if len(df):
            nearest = df.iloc[(df["strike"] - spot).abs().argsort()[:1]]
            ivs.append(float(nearest["impliedVolatility"].iloc[0]))
    return float(np.mean(ivs)) if ivs else None


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 3) if den > 0 else None


def analyze_chains(chains: list[ExpiryChain], spot: float, as_of: date) -> OptionsSnapshot:
    """Builds the full snapshot. `chains` should hold only expiries after
    `as_of` (already-expired contracts carry stale OI)."""
    chains = sorted([c for c in chains if c.expiry >= as_of], key=lambda c: c.expiry)
    if spot <= 0 or not chains:
        raise ValueError("need a positive spot and at least one unexpired chain")
    for ch in chains:
        ch.spot_for_iv = spot

    strikes = _by_strike(chains, spot, as_of)
    total = float(strikes["call_gex"].sum() + strikes["put_gex"].sum())
    flip = find_gamma_flip(chains, spot, as_of)
    call_wall, put_wall = _walls(strikes)

    nearest = chains[0]
    day1_strikes = _by_strike([nearest], spot, as_of)
    day1_call_wall, day1_put_wall = _walls(day1_strikes)
    day1_flip = find_gamma_flip([nearest], spot, as_of)

    if flip is not None and abs(spot / flip - 1) <= FLIP_NEAR_PCT:
        regime = "flip"
    else:
        regime = "positive" if total > 0 else "negative"

    atm_iv = _atm_iv(nearest, spot, as_of)
    expected_move_pct = round(atm_iv * math.sqrt(1 / TRADING_DAYS_PER_YEAR) * 100, 2) if atm_iv else None

    call_oi = int(strikes["call_oi"].sum())
    put_oi = int(strikes["put_oi"].sum())

    per_expiry = [(ch, total_gex_at([ch], spot, as_of), float(_gex_curve([ch], np.array([spot]), as_of, gross=True)[0])) for ch in chains]
    gross_sum = sum(gross for _, _, gross in per_expiry) or 1.0
    by_expiry = [
        ExpirySummary(
            expiry=ch.expiry.isoformat(),
            days=(ch.expiry - as_of).days,
            gex=round(net, 0),
            share=round(gross / gross_sum, 3),
        )
        for ch, net, gross in per_expiry
    ]

    above = strikes[strikes["strike"] >= spot].nlargest(2, "call_oi")
    below = strikes[strikes["strike"] <= spot].nlargest(2, "put_oi")
    big_oi = [{"strike": float(r.strike), "side": "call", "oi": int(r.call_oi)} for r in above.itertuples() if r.call_oi > 0]
    big_oi += [{"strike": float(r.strike), "side": "put", "oi": int(r.put_oi)} for r in below.itertuples() if r.put_oi > 0]

    window = strikes[(strikes["strike"] >= spot * 0.8) & (strikes["strike"] <= spot * 1.2)]
    gex_by_strike = [
        {
            "strike": float(r.strike),
            "call_gex": round(float(r.call_gex), 0),
            "put_gex": round(float(r.put_gex), 0),
            "call_oi": int(r.call_oi),
            "put_oi": int(r.put_oi),
        }
        for r in window.itertuples()
    ]

    return OptionsSnapshot(
        spot=round(spot, 4),
        as_of=as_of.isoformat(),
        total_gex=round(total, 0),
        regime=regime,
        gamma_flip=round(flip, 2) if flip is not None else None,
        call_wall=call_wall,
        put_wall=put_wall,
        day1_call_wall=day1_call_wall,
        day1_put_wall=day1_put_wall,
        day1_flip=round(day1_flip, 2) if day1_flip is not None else None,
        expected_move_pct=expected_move_pct,
        atm_iv=round(atm_iv, 4) if atm_iv else None,
        pc_ratio_oi=_ratio(put_oi, call_oi),
        pc_ratio_volume=_ratio(float(strikes["put_vol"].sum()), float(strikes["call_vol"].sum())),
        call_oi=call_oi,
        put_oi=put_oi,
        by_expiry=by_expiry,
        gex_by_strike=gex_by_strike,
        big_oi_strikes=big_oi,
    )
