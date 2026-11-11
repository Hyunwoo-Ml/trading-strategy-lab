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
    # ticker -> last valid (non-NaN, >0) price this portfolio has actually
    # seen in mark_to_market(). Added 2026-11-11 -- see mark_to_market()'s
    # docstring for why this exists. Old persisted portfolios predate this
    # field and simply pick up the empty-dict default when reloaded, same
    # backward-compat pattern as transaction_cost_pct above.
    last_known_prices: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cash = self.starting_cash

    # -- trading -----------------------------------------------------
    def buy(self, date: str, ticker: str, price: float, dollar_amount: float, action: str) -> None:
        if dollar_amount > self.cash:
            dollar_amount = self.cash  # don't go negative -- clip to available cash
        if dollar_amount <= 0 or not (price > 0):
            # `not (price > 0)` rather than `price <= 0`: NaN compares False
            # to every ordering operator, so the old `price <= 0` check
            # silently let a NaN price through. A NaN-priced buy creates a
            # Position with NaN shares/entry_price that can never be marked
            # to market again -- 2026-11-11 postmortem: exactly this (a
            # single NaN close from a SW1 data-collection gap) permanently
            # broke a live model's entire equity curve for weeks. `price` can
            # be NaN here because callers read it straight from upstream
            # signals/indicator data, which this ledger has no control over.
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
        if position is None:
            return
        if not (price > 0):
            # Same NaN/non-positive guard as buy() above. Put the position
            # back rather than liquidating it at a garbage price -- the
            # caller gets a no-op today, and the position is still there to
            # sell properly once a real price is available again.
            self.positions[ticker] = position
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
        """Marks every open position to today's price and appends one point
        to equity_curve.

        2026-11-11: a ticker's entry in `prices` can be missing entirely OR
        present but NaN (SW1's data collection occasionally drops a single
        ticker's close for a day -- an upstream provider gap, not a
        trading-model bug; see sw1/scoring's graceful-degradation pattern
        elsewhere). Falling straight back to `position.entry_price` on every
        such gap would silently use a possibly very stale number for a
        long-held position, and previously a NaN actually present in
        `prices` wasn't caught at all and poisoned `equity` (and therefore
        this whole curve, forever, since nothing downstream ever recovers
        from one NaN equity point) -- see the postmortem in buy()'s comment.
        This now prefers the last price this portfolio has actually seen for
        that ticker (`last_known_prices`), and only falls back to
        entry_price if it has never seen a valid one (e.g. a position opened
        on a day that was itself a gap day for some other reason). A valid
        price updates last_known_prices so later gaps keep carrying forward
        the most recent real observation instead of drifting back to the
        original entry price.
        """
        equity = self.cash
        for ticker, position in self.positions.items():
            price = prices.get(ticker)
            if price is None or pd.isna(price):
                price = self.last_known_prices.get(ticker, position.entry_price)
            else:
                self.last_known_prices[ticker] = price
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
