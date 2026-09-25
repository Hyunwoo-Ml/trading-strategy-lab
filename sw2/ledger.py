"""
SW2 -- paper trading ledger: tracks cash, positions, trades and daily
equity per registered model so Daily 수익률 can be computed and compared
across models.

Position sizing here is intentionally simple (fixed dollar amount per buy
tranche, clipped to available cash) -- good enough to get the whole loop
(signal -> trade -> equity -> daily return -> statistical comparison)
working end-to-end. Refine sizing rules once there's real comparison data
to react to.

Transaction costs: every buy/sell notional is charged `transaction_cost_pct`
(default 0.1% = 10bps, a stand-in for combined commission + slippage on a
typical US equities online broker). This is 100% simulated paper-trading
bookkeeping -- no real money ever moves. A buy spends exactly
`dollar_amount` of cash as before, but only `dollar_amount * (1 -
transaction_cost_pct)` actually converts into shares -- the rest is the fee,
recorded on the Trade for transparency. A sell credits
`shares * price * (1 - transaction_cost_pct)` back to cash. Existing
persisted portfolios (data/sw2/portfolios/*.json) predate this field and
have no `transaction_cost_pct` key -- they simply pick up the class default
when reloaded, and old Trade records deserialize with `fee=0.0` since that
field also defaults.
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
    fee: float = 0.0  # transaction cost charged on this trade's notional (commission + slippage stand-in)


@dataclass
class Portfolio:
    """One model's paper-trading book."""

    model_name: str
    starting_cash: float = 100_000.0
    transaction_cost_pct: float = 0.001  # 10bps combined commission+slippage, applied to buy/sell notional
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
        fee = dollar_amount * self.transaction_cost_pct
        shares = (dollar_amount - fee) / price
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
            Trade(date=date, ticker=ticker, action=action, shares=shares, price=price, cash_delta=-dollar_amount, fee=fee)
        )

    def sell_all(self, date: str, ticker: str, price: float, action: str) -> None:
        position = self.positions.pop(ticker, None)
        if position is None or price <= 0:
            return
        gross_proceeds = position.shares * price
        fee = gross_proceeds * self.transaction_cost_pct
        net_proceeds = gross_proceeds - fee
        self.cash += net_proceeds
        self.trades.append(
            Trade(date=date, ticker=ticker, action=action, shares=-position.shares, price=price, cash_delta=net_proceeds, fee=fee)
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

    def current_equity(self) -> float:
        """Most recently marked-to-market portfolio value (cash + open
        positions at their last known price), or starting_cash before the
        first mark_to_market call ever runs for this portfolio.

        2026-09-25 (equity-based sizing): scripts/run_daily_paper_trading.py
        and scripts/run_historical_backtest.py now size new tranches against
        this instead of the fixed starting_cash constant (see
        sw2/sizing.py's module docstring) -- a model that has compounded
        gains commits more dollars to its next tranche, and one nursing a
        drawdown commits less, the same way a fully-invested buy-and-hold
        benchmark's dollar exposure moves with its own equity. Because this
        only ever reads the LAST recorded mark (yesterday's close, not
        today's still-unmarked prices), every trade decided today is sized
        off the same number regardless of trade order within the day --
        it can't see today's own trades yet.
        """
        if self.equity_curve:
            return self.equity_curve[-1]["equity"]
        return self.starting_cash

    # -- reporting -----------------------------------------------------
    def equity_df(self) -> pd.DataFrame:
        df = pd.DataFrame(self.equity_curve)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df["daily_return"] = df["equity"].pct_change()
        return df
