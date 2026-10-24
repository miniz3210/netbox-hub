"""Per-endpoint merge rules for tiered NetBox backups (Full / Minimal / CSV). Pure functions, no I/O."""
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

_FRAC = re.compile(r"(\.\d{1,9})")


def parse_ts(value: Any, naive_tz: Optional[timezone] = None) -> Optional[datetime]:
    """Parse a timestamp string into a UTC datetime.

    Handles ISO-8601 with fractional seconds, trailing 'Z' or ' UTC', and bare
    naive strings (assumed to be in the server's local timezone when naive_tz is
    None).
    """
    if not value:
        return None
    s = str(value).strip().replace(" UTC", "").replace("Z", "+00:00")
    s = _FRAC.sub(lambda m: (m.group(1) + "000000")[:7], s, count=1)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        if naive_tz is not None:
            d = d.replace(tzinfo=naive_tz)
        else:
            d = d.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return d.astimezone(timezone.utc)


def format_ts(dt: datetime) -> str:
    """Format a datetime as 'YYYY-MM-DD HH:MM:SS UTC'."""
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def plan_endpoint_updates(
    incoming: Dict[str, Dict[str, Any]],
    incoming_exported_at: Any,
    stored: Dict[str, Dict[str, Any]],
    uploaded_at: Any = None,
) -> Dict[str, Tuple[str, str]]:
    """Decide per-endpoint whether to replace or skip.

    incoming: {path: {"records": [...], "verified": bool|None,
                      "downloaded_count": int|None, "expected_count": int|None}}
    stored:   {path: {"exported_at": str, "count": int}}
    Returns:  {path: (action, reason)}; action in replace | skip_unverified |
              skip_suspicious_empty.
    """
    inc_ts = parse_ts(incoming_exported_at) or parse_ts(uploaded_at)
    plan: Dict[str, Tuple[str, str]] = {}
    for path, ep in incoming.items():
        n = len(ep.get("records") or [])
        dl = ep.get("downloaded_count")
        ex = ep.get("expected_count")
        ver = ep.get("verified")
        if ver is False or (dl is not None and ex is not None and dl < ex):
            plan[path] = (
                "skip_unverified",
                f"partial pull ({dl}/{ex}); replacing would delete real records",
            )
            continue
        old = stored.get(path)
        if old:
            old_ts = parse_ts(old.get("exported_at"))
            if inc_ts and old_ts and inc_ts < old_ts:
                plan[path] = (
                    "replace",
                    "older export",
                )
                continue
            if n == 0 and (old.get("count") or 0) > 0 and ex != 0:
                plan[path] = (
                    "skip_suspicious_empty",
                    f"0 records but {old.get('count')} stored and expected_count={ex}",
                )
                continue
        plan[path] = ("replace", "ok")
    return plan
