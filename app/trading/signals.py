from typing import List, Optional

from app.trading.bybit import Candle


def detect_basic_volume_spike(candles: List[Candle]) -> Optional[dict]:
    """T3 lightweight detector. T4 will replace this with full strategy engine."""
    if len(candles) < 25:
        return None
    closed = candles[:-1] if len(candles) > 1 else candles
    last = closed[-1]
    prev = closed[-21:-1]
    avg_vol = sum(c.volume for c in prev) / max(len(prev), 1)
    if avg_vol <= 0:
        return None
    ratio = last.volume / avg_vol
    body = abs(last.close - last.open)
    rng = max(last.high - last.low, 1e-12)
    body_ratio = body / rng
    if ratio >= 2.5 and body_ratio >= 0.45:
        direction = "long-watch" if last.close > last.open else "short-watch"
        return {
            "strategy": "volume_spike",
            "direction": direction,
            "confidence": "low-medium",
            "reason": f"Volume spike ratio {ratio:.2f}x avg20, body/range {body_ratio:.2f}",
            "last_close": last.close,
            "invalidation": last.low if direction.startswith("long") else last.high,
        }
    return None


def detect_simple_liquidity_sweep(candles: List[Candle]) -> Optional[dict]:
    if len(candles) < 30:
        return None
    closed = candles[:-1] if len(candles) > 1 else candles
    last = closed[-1]
    prev = closed[-21:-1]
    prev_low = min(c.low for c in prev)
    prev_high = max(c.high for c in prev)
    avg_vol = sum(c.volume for c in prev) / max(len(prev), 1)
    vol_ratio = last.volume / avg_vol if avg_vol else 0

    if last.low < prev_low and last.close > prev_low and vol_ratio >= 1.4:
        return {
            "strategy": "liquidity_sweep",
            "direction": "long-watch",
            "confidence": "medium",
            "reason": f"Swept local low {prev_low:.6g} and closed back above; volume {vol_ratio:.2f}x avg20",
            "last_close": last.close,
            "invalidation": last.low,
        }
    if last.high > prev_high and last.close < prev_high and vol_ratio >= 1.4:
        return {
            "strategy": "liquidity_sweep",
            "direction": "short-watch",
            "confidence": "medium",
            "reason": f"Swept local high {prev_high:.6g} and closed back below; volume {vol_ratio:.2f}x avg20",
            "last_close": last.close,
            "invalidation": last.high,
        }
    return None


def detect_t3_signals(candles: List[Candle]) -> List[dict]:
    out = []
    for detector in (detect_simple_liquidity_sweep, detect_basic_volume_spike):
        sig = detector(candles)
        if sig:
            out.append(sig)
    return out
