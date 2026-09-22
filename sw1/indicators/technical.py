"""
SW1 -- indicator module

Expected input: pandas.DataFrame sorted ascending by date with columns
Open, High, Low, Close, Volume.

Each function returns a new DataFrame with indicator column(s) added,
leaving the original untouched. Kept decoupled from the data layer
(sw1/data) so it works with any source (yfinance / Toss / KIS) as long
as the column shape matches.
"""
from __future__ import annotations

import pandas as pd


REQUIRED_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}


def _validate(df: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    if len(df) == 0:
        raise ValueError("input DataFrame is empty")


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """RSI (Relative Strength Index), 0-100. >=70 overbought, <=30 oversold."""
    _validate(df)
    out = df.copy()
    delta = out["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out["RSI"] = 100 - (100 / (1 + rs))
    out.loc[avg_loss == 0, "RSI"] = 100.0
    return out


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    std_window: int = 60,
    std_min_periods: int = 20,
) -> pd.DataFrame:
    """Adds MACD line, signal line, and histogram (MACD - Signal).

    Also adds MACD_HIST_STD_{std_window} -- a rolling standard deviation of
    the histogram, added 2026-09-22 so sw1.scoring.integrate can convert the
    histogram to a [-1, 1] sub-score using a scale that means something for
    THIS ticker, instead of a fixed constant. A $5 histogram swing is huge
    for a $50 stock and tiny for a $500 one; a fixed scale=1.0 treated every
    ticker as if it moved like a ~$1 stock, so high-priced tickers'
    sub-scores saturated at +-1 almost every day while low-priced tickers'
    barely moved off 0 -- neither reflected genuine momentum. NaN until
    std_min_periods histogram values exist (same warm-up pattern as the
    other rolling indicators here); sw1.scoring.integrate falls back to the
    old fixed scale=1.0 when this is NaN, same graceful-degradation pattern
    used everywhere else in this module.
    """
    _validate(df)
    out = df.copy()
    ema_fast = out["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["Close"].ewm(span=slow, adjust=False).mean()

    out["MACD"] = ema_fast - ema_slow
    out["MACD_SIGNAL"] = out["MACD"].ewm(span=signal, adjust=False).mean()
    out["MACD_HIST"] = out["MACD"] - out["MACD_SIGNAL"]
    out[f"MACD_HIST_STD_{std_window}"] = out["MACD_HIST"].rolling(
        window=std_window, min_periods=std_min_periods
    ).std()
    return out


def add_bollinger_bands(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Adds Bollinger upper/mid(SMA)/lower bands and %B (0=lower band, 1=upper band)."""
    _validate(df)
    out = df.copy()
    mid = out["Close"].rolling(window=period, min_periods=period).mean()
    std = out["Close"].rolling(window=period, min_periods=period).std()

    out["BB_MID"] = mid
    out["BB_UPPER"] = mid + num_std * std
    out["BB_LOWER"] = mid - num_std * std

    band_width = out["BB_UPPER"] - out["BB_LOWER"]
    out["BB_PCT_B"] = (out["Close"] - out["BB_LOWER"]) / band_width.replace(0, pd.NA)
    return out


def add_moving_average_cross(
    df: pd.DataFrame,
    short_window: int = 50,
    long_window: int = 200,
) -> pd.DataFrame:
    """
    Moving average golden/dead cross.
    MA_CROSS column: 'golden' (short crosses above long), 'dead' (below), or None.
    """
    _validate(df)
    if short_window >= long_window:
        raise ValueError("short_window must be smaller than long_window")

    out = df.copy()
    out[f"MA_{short_window}"] = out["Close"].rolling(window=short_window, min_periods=short_window).mean()
    out[f"MA_{long_window}"] = out["Close"].rolling(window=long_window, min_periods=long_window).mean()

    diff = out[f"MA_{short_window}"] - out[f"MA_{long_window}"]
    prev_diff = diff.shift(1)

    cross = pd.Series(index=out.index, dtype="object")
    cross[(prev_diff <= 0) & (diff > 0)] = "golden"
    cross[(prev_diff >= 0) & (diff < 0)] = "dead"
    out["MA_CROSS"] = cross
    return out


def add_volume_profile(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Adds volume moving average and VOL_RATIO (today's volume / average)."""
    _validate(df)
    out = df.copy()
    out[f"VOL_MA_{period}"] = out["Volume"].rolling(window=period, min_periods=period).mean()
    out["VOL_RATIO"] = out["Volume"] / out[f"VOL_MA_{period}"].replace(0, pd.NA)
    return out


def compute_all_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes RSI, MACD, Bollinger Bands, MA cross, and volume profile in one call.

    PER/PBR are not included here since they come from the data source's
    fundamentals fields, not the price history (attached separately in sw1/data).
    """
    out = add_rsi(df)
    out = add_macd(out)
    out = add_bollinger_bands(out)
    out = add_moving_average_cross(out)
    out = add_volume_profile(out)
    return out
