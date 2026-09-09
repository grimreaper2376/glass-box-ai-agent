"""
GlassBox — technical indicators.

Pure functions over real OHLCV arrays. Kept separate from the analysts so they
can be unit-tested against known values, which matters: an off-by-one in an RSI
window is invisible in a dashboard and fatal in a decision.
"""

from __future__ import annotations

import statistics


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], period: int = 14) -> float:
    """Wilder's RSI with proper smoothing, not a simple average of the window."""
    if len(values) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss < 1e-12:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def macd(values: list[float], fast: int = 12, slow: int = 26, sig: int = 9):
    if len(values) < slow + sig:
        return 0.0, 0.0, 0.0
    f, s = ema_series(values, fast), ema_series(values, slow)
    line = [a - b for a, b in zip(f, s)]
    signal = ema_series(line, sig)
    return line[-1], signal[-1], line[-1] - signal[-1]


def bollinger(values: list[float], period: int = 20, mult: float = 2.0):
    if len(values) < period:
        v = values[-1] if values else 0.0
        return v, v, v
    window = values[-period:]
    mid = statistics.fmean(window)
    sd = statistics.pstdev(window)
    return mid + mult * sd, mid, mid - mult * sd


def true_range(highs, lows, closes) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        out.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    return out


def atr_pct(highs, lows, closes, period: int = 14) -> float:
    tr = true_range(highs, lows, closes)
    if len(tr) < period:
        return 0.0
    return statistics.fmean(tr[-period:]) / max(closes[-1], 1e-9) * 100


def adx(highs, lows, closes, period: int = 14) -> float:
    """Trend strength. Below 20 means range; trend signals are unreliable there."""
    if len(closes) < period * 2 + 1:
        return 0.0
    plus_dm, minus_dm = [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    tr = true_range(highs, lows, closes)
    n = min(len(tr), len(plus_dm))
    tr, plus_dm, minus_dm = tr[-n:], plus_dm[-n:], minus_dm[-n:]
    atr = statistics.fmean(tr[-period:]) or 1e-9
    pdi = 100 * statistics.fmean(plus_dm[-period:]) / atr
    mdi = 100 * statistics.fmean(minus_dm[-period:]) / atr
    denom = pdi + mdi
    return 100 * abs(pdi - mdi) / denom if denom > 1e-9 else 0.0


def zscore(values: list[float]) -> float:
    if len(values) < 8:
        return 0.0
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    return (values[-1] - mean) / sd if sd > 1e-12 else 0.0


def volume_profile_skew(closes: list[float], volumes: list[float], lookback: int = 30) -> float:
    """
    Share of recent volume that traded on up bars, centred on zero.

    Price rising on thin volume and price rising on heavy volume are different
    events, and a close-only view cannot tell them apart.
    """
    n = min(lookback, len(closes) - 1, len(volumes) - 1)
    if n < 5:
        return 0.0
    up = down = 0.0
    for i in range(len(closes) - n, len(closes)):
        if closes[i] >= closes[i - 1]:
            up += volumes[i]
        else:
            down += volumes[i]
    total = up + down
    return (up - down) / total if total > 1e-9 else 0.0
