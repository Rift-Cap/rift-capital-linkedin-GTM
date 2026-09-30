from datetime import datetime, timedelta


def select_eligible(posts: list[dict], now: datetime, window_days: int, min_hours: int,
                    ignore_filters: bool = False, cap: int | None = None) -> list[dict]:
    """posts: [{collected_at: datetime|None, last_scraped: datetime|None, ...}].

    Eligible = collected within `window_days` AND not scraped within `min_hours`.
    Never-scraped posts first, then least recently scraped. `ignore_filters` = --all.
    """
    def ok(p: dict) -> bool:
        if ignore_filters:
            return True
        c, s = p.get("collected_at"), p.get("last_scraped")
        if c is None or now - c > timedelta(days=window_days):
            return False
        return s is None or now - s >= timedelta(hours=min_hours)

    picked = [p for p in posts if ok(p)]
    epoch = datetime.min.replace(tzinfo=now.tzinfo)
    picked.sort(key=lambda p: p.get("last_scraped") or epoch)
    return picked[:cap] if cap else picked
