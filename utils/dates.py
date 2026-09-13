from datetime import date, datetime
import os

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


def today():
    """Return today's date. If `APP_TIMEZONE` env var is set and zoneinfo
    is available, interpret 'now' in that timezone. Falls back to system
    local date or UTC date if the timezone name is invalid.
    """
    tz_name = os.environ.get("APP_TIMEZONE")
    if tz_name and ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            return datetime.utcnow().date()
    return date.today()
