"""
SW2 -- paper trading ledger: tracks cash, positions, trades and daily
equity per registered model so Daily 수익률 can be computed and compared
across models.

Position sizing here is intentionally simple (fixed dollar amount per buy
tranche, clipped to available cash) -- good enough to get the whole loop
(signal -> trade -> equity -> daily return -> statistical comparison)
working end-to-end. Refine sizing rules once there's real comparison data
to react to.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Position:
    ticker: str
    shares: float
    entry_price: float
    entry_date: str  # ISO date string
    tranche_count: int = 1  # how many buy tranches have gone into this position


@dataclass
class Trade:
    date: str
    ticker: str
    action: str  # "buy_1" | "buy_2" | "stop_loss" | "take_profit" | ...
    shares: float
    price: float
    cash_delta: float


@dataclass
class Portfolio:
    """One model's paper-trading book."""

    model_name: str
    starting_cash: float = 100_000.0
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)  # [{"date":..., "equity":...}]

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    # -- trading -----------------------------------------------------
    def buy(self, date: str, ticker: str, price: float, dollar_amount: float, action: str) -> None:
        if dollar_amount > self.cash:
            dollar_amount = self.cash  # don't go negative -- clip to available cash
        if dollar_amount <= 0 or price <= 0:
            return
        shares = dollar_amount / price
        existing = self.positions.get(ticker)
        if existing:
            total_shares = existing.shares + shares
            existing.entry_price = (existing.entry_price * existing.shares + price * shares) / total_shares
            existing.shares = total_shares
            existing.tranche_count += 1
        else:
            self.positions[ticker] = Position(ticker=ticker, shares=shares, entry_price=price, entry_date=date, tranche_count=1)
        self.cash -= dollar_amount
        self.trades.append(
            Trade(date=date, ticker=ticker, action=action, shares=shares, price=price, cash_delta=-dollar_amount)
        )

    def sell_all(self, date: str, ticker: str, price: float, action: str) -> None:
        position = self.positions.pop(ticker, None)
        if position is None or price <= 0:
            return
        proceeds = position.shares * price
        self.cash += proceeds
        self.trades.append(
            Trade(date=date, ticker=ticker, action=action, shares=-position.shares, price=price, cash_delta=proceeds)
        )

    # -- valuation -----------------------------------------------------
    def mark_to_market(self, date: str, prices: dict[str, float]) -> float:
        equity = self.cash
        for ticker, position in self.positions.items():
            price = prices.get(ticker, position.entry_price)
            equity += position.shares * price
        self.equity_curve.append({"date": date, "equity": equity})
        return equity

    def price_return_from_entry(self, ticker: str, current_price: float) -> float | None:
        position = self.positions.get(ticker)
        if position is None or position.entry_price <= 0:
            return None
        return (current_price - position.entry_price) / position.entry_price

    # -- reporting -----------------------------------------------------
    def equity_df(self) -> pd.DataFrame:
        df = pd.DataFrame(self.equity_curve)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df["daily_return"] = df["equity"].pct_change()
        return df
