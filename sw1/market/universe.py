"""
SW1 -- the Fin-tip_lab watchlist universe: US index ETFs plus large,
heavily traded stocks grouped by theme.

Names are stored here rather than fetched so the daily run doesn't need a
slow per-ticker profile lookup. Adding a ticker is one line; a ticker that
Yahoo can't return is skipped by the pipeline and listed under failures.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Member:
    ticker: str
    name: str
    group: str


GROUPS: dict[str, list[tuple[str, str]]] = {
    "지수·ETF": [
        ("SPY", "S&P 500 ETF"),
        ("QQQ", "Nasdaq-100 ETF"),
        ("DIA", "Dow Jones ETF"),
        ("IWM", "Russell 2000 ETF"),
        ("SMH", "Semiconductor ETF"),
        ("TLT", "20Y+ Treasury ETF"),
        ("GLD", "Gold ETF"),
    ],
    "빅테크": [
        ("AAPL", "Apple"),
        ("MSFT", "Microsoft"),
        ("NVDA", "NVIDIA"),
        ("GOOGL", "Alphabet"),
        ("AMZN", "Amazon"),
        ("META", "Meta Platforms"),
        ("TSLA", "Tesla"),
    ],
    "반도체": [
        ("AVGO", "Broadcom"),
        ("TSM", "TSMC"),
        ("AMD", "AMD"),
        ("MU", "Micron"),
        ("QCOM", "Qualcomm"),
        ("MRVL", "Marvell"),
        ("INTC", "Intel"),
        ("AMAT", "Applied Materials"),
        ("LRCX", "Lam Research"),
        ("ASML", "ASML"),
        ("ARM", "Arm Holdings"),
        ("SNDK", "Sandisk"),
        ("ON", "ON Semiconductor"),
        ("STX", "Seagate"),
    ],
    "AI 인프라·클라우드": [
        ("ORCL", "Oracle"),
        ("DELL", "Dell"),
        ("ANET", "Arista Networks"),
        ("IREN", "IREN"),
        ("CRWV", "CoreWeave"),
        ("NBIS", "Nebius"),
        ("LITE", "Lumentum"),
        ("COHR", "Coherent"),
        ("AAOI", "Applied Optoelectronics"),
    ],
    "전력·에너지": [
        ("VRT", "Vertiv"),
        ("GEV", "GE Vernova"),
        ("VST", "Vistra"),
        ("CEG", "Constellation Energy"),
        ("OKLO", "Oklo"),
        ("BE", "Bloom Energy"),
        ("EOSE", "Eos Energy"),
    ],
    "소프트웨어·보안": [
        ("PLTR", "Palantir"),
        ("CRM", "Salesforce"),
        ("NOW", "ServiceNow"),
        ("CRWD", "CrowdStrike"),
        ("PANW", "Palo Alto Networks"),
        ("APP", "AppLovin"),
        ("NFLX", "Netflix"),
        ("IBM", "IBM"),
        ("TEM", "Tempus AI"),
    ],
    "금융·헬스케어": [
        ("JPM", "JPMorgan Chase"),
        ("BAC", "Bank of America"),
        ("MS", "Morgan Stanley"),
        ("UNH", "UnitedHealth"),
        ("LLY", "Eli Lilly"),
    ],
    "핀테크·크립토": [
        ("HOOD", "Robinhood"),
        ("COIN", "Coinbase"),
        ("SOFI", "SoFi"),
        ("CRCL", "Circle"),
    ],
    "우주·양자": [
        ("RKLB", "Rocket Lab"),
        ("IONQ", "IonQ"),
    ],
}

INDEX_GROUP = "지수·ETF"


def members() -> list[Member]:
    return [Member(t, n, g) for g, rows in GROUPS.items() for t, n in rows]


def stock_members() -> list[Member]:
    """Everything except the index/ETF group -- used for breadth stats."""
    return [m for m in members() if m.group != INDEX_GROUP]
