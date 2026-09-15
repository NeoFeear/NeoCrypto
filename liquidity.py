from config import LiquidityConfig
from market_data.types import BookTicker, Ticker24h


def passes_liquidity_filter(
    ticker_24h: Ticker24h, book_ticker: BookTicker, config: LiquidityConfig
) -> tuple[bool, str | None]:
    """Spec section 5: volume 24h (quote) must be strictly > threshold, spread
    (bps) must be strictly < threshold. Volume is checked first — if both fail,
    the reported reason is 'volume insuffisant'."""
    if ticker_24h.quote_volume <= config.min_quote_volume_24h:
        return False, "volume insuffisant"
    if book_ticker.spread_bps >= config.max_spread_bps:
        return False, "spread trop large"
    return True, None
