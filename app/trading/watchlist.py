import json
import time
from dataclasses import dataclass
from typing import List

WATCHLIST_TTL = 60 * 60 * 24 * 30


@dataclass
class WatchItem:
    symbol: str
    timeframe: str
    added_at: float
    enabled: bool = True


def _key(chat_id: int) -> str:
    return f"trading_watchlist:{chat_id}"


def _item_id(symbol: str, timeframe: str) -> str:
    return f"{symbol.upper()}:{timeframe.lower()}"


async def add_watch(redis, chat_id: int, symbol: str, timeframe: str) -> WatchItem:
    item = WatchItem(symbol=symbol.upper(), timeframe=timeframe.lower(), added_at=time.time(), enabled=True)
    await redis.hset(_key(chat_id), _item_id(item.symbol, item.timeframe), json.dumps(item.__dict__, ensure_ascii=False))
    await redis.expire(_key(chat_id), WATCHLIST_TTL)
    return item


async def remove_watch(redis, chat_id: int, symbol: str, timeframe: str) -> bool:
    deleted = await redis.hdel(_key(chat_id), _item_id(symbol, timeframe))
    return bool(deleted)


async def list_watch(redis, chat_id: int) -> List[WatchItem]:
    raw = await redis.hgetall(_key(chat_id))
    items: List[WatchItem] = []
    for _k, v in raw.items():
        try:
            d = json.loads(v.decode() if isinstance(v, bytes) else v)
            items.append(WatchItem(**d))
        except Exception:
            continue
    items.sort(key=lambda x: (x.symbol, x.timeframe))
    return items


async def all_watchlists(redis) -> dict[int, List[WatchItem]]:
    result: dict[int, List[WatchItem]] = {}
    async for key in redis.scan_iter("trading_watchlist:*"):
        try:
            cid = int((key.decode() if isinstance(key, bytes) else key).split(":", 1)[1])
            result[cid] = await list_watch(redis, cid)
        except Exception:
            continue
    return result

