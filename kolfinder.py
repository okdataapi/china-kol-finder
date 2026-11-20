#!/usr/bin/env python3
"""Find Chinese influencers (KOLs) on Douyin and Xiaohongshu (RedNote), with their official rates.

Searches the two official creator marketplaces, Douyin's Xingtu (星图) and Xiaohongshu's PGY (蒲公英), for
creators who post about a topic, and writes one CSV shortlist: followers, content categories, median views and
engagements, every quoted rate in yuan, and what 1,000 median views cost at the cheapest rate. The terminal shows
the best value first.

Data comes from the okdata API (https://okdata.online/?src=gh-china-kol-finder): a page of about 20 creators is
$0.004 on Douyin and $0.04 on Xiaohongshu. Python 3.9+, standard library only. The key goes in OKDATA_API_KEY.

    python3 kolfinder.py --query 露营 --followers 100k-1m
    python3 kolfinder.py --query "skincare 护肤" --platform xiaohongshu --pages 2 --cny-per-usd 7.1
"""
from __future__ import annotations

import argparse
import csv
import http.client
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = "0.1.0"
SOURCE = "gh-china-kol-finder"
SIGN_UP = f"https://okdata.online/signup/?src={SOURCE}"
CONSOLE = f"https://okdata.online/console/?src={SOURCE}"
API = os.environ.get("OKDATA_BASE_URL", "https://api.okdata.online").rstrip("/")
PLATFORMS = {
    # platform: (endpoint, value of search_by for "content", fallback price per page in USD)
    "douyin": ("douyin.xingtu.creators.search", "content", Decimal("0.004")),
    "xiaohongshu": ("xiaohongshu.pgy.creators.search", "notes", Decimal("0.04")),
}
RATE_NAMES = {"integrated_video": "integrated video", "custom_video": "custom video",
              "image_note": "image note", "video_note": "video note"}
FIELDS = ["platform", "creator_id", "name", "followers", "categories", "location", "gender", "median_views",
          "median_engagements", "engagements_per_view", "engagement_rate_30d", "cheapest_rate", "cheapest_rate_cny",
          "cny_per_1k_views", "cheapest_rate_usd", "usd_per_1k_views", "rates", "marketplace_url"]
NAME_LIMIT = 60
QUERY_LIMIT = 50
STOP_CODES = {"unauthorized", "insufficient_balance", "key_budget_exceeded", "cost_cap_exceeded", "key_scope_denied",
              "account_suspended", "budget_reached"}
RETRY_STATUSES = {429, 500, 502, 503, 504}
WHITESPACE = re.compile("[\t\n\v\f\r    　]+")
COUNT = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([km]?)$", re.I)
AMOUNT = re.compile(r"[0-9]+(\.[0-9]+)?")


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, charged: Optional[bool] = False, retry: str = "no",
                 retry_after: Optional[float] = None, amount: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.charged, self.retry, self.retry_after = code, charged, retry, retry_after
        self.amount = amount


class Okdata:
    """The two okdata searches this script needs, with safe retries and a hard spending cap."""

    def __init__(self, key: Optional[str], budget: Optional[Decimal] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.key = key
        self.budget = budget
        self.sleep = sleep
        self.spent = Decimal("0")
        self.caps = {endpoint: price for endpoint, _, price in PLATFORMS.values()}

    def _headers(self, idempotency_key: Optional[str]) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": f"{SOURCE}/{VERSION} (python)"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def prices(self) -> Dict[str, Decimal]:
        """Current prices from the free catalog (no key needed); the built-in table if it cannot be read."""
        fallback = {endpoint: price for endpoint, _, price in PLATFORMS.values()}
        try:
            data = request_json(f"{API}/v1/catalog", self._headers(None), self.sleep)["data"]
            live = {e["id"]: dollars(e.get("maxCharge")) for e in data["endpoints"]}
            self.caps = {k: live.get(k) or v for k, v in fallback.items()}
        except (ApiError, KeyError, TypeError, AttributeError):
            self.caps = fallback
        return self.caps

    def call(self, endpoint: str, **params: Any) -> Dict[str, Any]:
        cap = self.caps.get(endpoint, Decimal("0"))
        if self.budget is not None and self.spent + cap > self.budget:
            raise ApiError("budget_reached", f"the next call could cost ${usd(cap)} and pass the --budget of "
                                             f"${usd(self.budget)}")
        query = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{API}/v1/{endpoint.replace('.', '/')}?{query}"
        try:
            body = request_json(url, self._headers(str(uuid.uuid4())), self.sleep)
        except ApiError as err:
            if err.charged is True:
                self.spent += dollars(err.amount) or Decimal("0")
            raise
        self.spent += dollars(((body.get("meta") or {}).get("billing") or {}).get("amount")) or Decimal("0")
        return body


def _http_error(exc: HTTPError) -> ApiError:
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (ValueError, OSError, http.client.HTTPException):
        body = None
    if isinstance(body, dict) and "code" in body:
        return ApiError(str(body["code"]), str(body.get("message")), charged=body.get("charged", False),
                        retry=str(body.get("retry", "no")), retry_after=body.get("retry_after_seconds"),
                        amount=body.get("amount"))
    return ApiError(f"http_{exc.code}", str(exc.reason), charged=None,
                    retry="yes" if exc.code in RETRY_STATUSES else "no")


def request_json(url: str, headers: Dict[str, str], sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """GET with up to 2 retries that reuse the same Idempotency-Key, so a lost answer is replayed instead of run
    twice. After a lost answer the call may still be running on the server, so a rate_limited reply is waited out."""
    retries, waits, ambiguous = 0, 0, False
    while True:
        try:
            with urlopen(Request(url, headers=headers), timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if isinstance(body, dict) and body.get("code") == "ok":
                return body
            err = (ApiError(str(body.get("code")), str(body.get("message"))) if isinstance(body, dict)
                   else ApiError("bad_response", "unexpected answer", charged=None, retry="same_key"))
        except HTTPError as exc:
            err = _http_error(exc)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            err = ApiError("connection_error", str(exc), charged=None, retry="same_key")
        ambiguous = ambiguous or err.charged is None
        if ambiguous and err.code == "rate_limited" and waits < 5:
            waits += 1
            sleep(_wait(err.retry_after, 1.0))
            continue
        if err.retry not in ("yes", "same_key") or retries == 2:
            if ambiguous and err.charged is False:
                err.charged = None
            raise err
        sleep(_wait(err.retry_after, 2.0 ** retries))
        retries += 1


def _wait(value: Any, default: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(seconds, 0.0), 30.0)


def use_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace" if stream is sys.stderr else "strict")


def dollars(value: Any) -> Optional[Decimal]:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def usd(amount: Decimal) -> str:
    return format(amount.normalize(), "f")


def charged(err: ApiError) -> str:
    return {True: "yes", False: "no"}.get(err.charged, "unknown")  # type: ignore[arg-type]


def cell(value: Any) -> Any:
    """Text that starts like a spreadsheet formula gets a leading ' so Excel shows it instead of running it."""
    return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def clip(text: Any, limit: int) -> str:
    line = WHITESPACE.sub(" ", str(text or "")).strip(" ")
    return line if len(line) <= limit else line[: limit - 1].rstrip(" ") + "…"


def width(text: str) -> int:
    """Columns the text takes in a terminal: Chinese characters take two."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def fit(text: str, columns: int) -> str:
    """Cut to at most `columns` terminal columns and pad to exactly that many."""
    out = ""
    for ch in text:
        if width(out + ch) > columns - (1 if width(text) > columns else 0):
            out += "…"
            break
        out += ch
    return out + " " * (columns - width(out))


# --- creators ----------------------------------------------------------------------------------------------------

def money_cny(value: Any) -> Optional[Decimal]:
    amount = dollars(value)  # same checks: a finite, non-negative number
    return amount if amount is not None and amount > 0 else None


def round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def whole(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return int(value)


def to_row(platform: str, item: Dict[str, Any], cny_per_usd: Optional[Decimal]) -> Dict[str, Any]:
    rates = [r for r in item.get("rates") or [] if isinstance(r, dict) and money_cny(r.get("price"))]
    offered = [r for r in rates if r.get("available") is not False]
    cheapest = min(offered, key=lambda r: money_cny(r["price"])) if offered else None
    price = money_cny(cheapest["price"]) if cheapest else None
    views = whole(item.get("median_views"))
    engagements = whole(item.get("median_engagements"))
    per_1k = round2(price * 1000 / views) if price and views else None
    rate_30d = item.get("engagement_rate")
    return {
        "platform": platform,
        "creator_id": str(item.get("id") or ""),
        "name": clip(item.get("name"), NAME_LIMIT),
        "followers": whole(item.get("followers")),
        "categories": " / ".join(clip(c, 20) for c in item.get("categories") or []),
        "location": clip(item.get("location"), 40),
        "gender": item.get("gender") or "",
        "median_views": views,
        "median_engagements": engagements,
        "engagements_per_view": (f"{engagements / views:.4f}" if engagements is not None and views else ""),
        "engagement_rate_30d": (f"{rate_30d:.4f}" if isinstance(rate_30d, (int, float)) else ""),
        "cheapest_rate": RATE_NAMES.get(str(cheapest.get("type")), str(cheapest.get("type"))) if cheapest else "",
        "cheapest_rate_cny": price,
        "cny_per_1k_views": per_1k,
        "cheapest_rate_usd": round2(price / cny_per_usd) if price and cny_per_usd else None,
        "usd_per_1k_views": round2(per_1k / cny_per_usd) if per_1k and cny_per_usd else None,
        "rates": "; ".join(f"{RATE_NAMES.get(str(r.get('type')), r.get('type'))} {money_cny(r['price'])}"
                           + ("" if r.get("available") is not False else " (not taking orders)") for r in rates),
        "marketplace_url": item.get("url") or "",
    }


def search(ok: Okdata, platform: str, query: str, args: argparse.Namespace) -> Iterator[Dict[str, Any]]:
    endpoint, content, _ = PLATFORMS[platform]
    cursor = None
    for _ in range(args.pages):
        body = ok.call(endpoint, query=query, search_by="name" if args.by == "name" else content,
                       followers_min=args.followers[0], followers_max=args.followers[1], cursor=cursor,
                       view="compact")
        yield from (body.get("data") or {}).get("items") or []
        cursor = (body.get("page") or {}).get("next_cursor")
        if not cursor:
            return


def best_value_first(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per platform (Douyin counts video plays, Xiaohongshu note reads, so their costs per 1k views are not
    comparable), cheapest 1,000 median views first; creators without a rate or views last, biggest first."""
    order = list(PLATFORMS)
    return sorted(rows, key=lambda r: (order.index(r["platform"]), r["cny_per_1k_views"] is None,
                                       r["cny_per_1k_views"] or 0, -(r["followers"] or 0), r["creator_id"]))


def short_count(value: Optional[int]) -> str:
    if value is None:
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


MARKETPLACES = {"douyin": "Douyin · Xingtu (views = video plays)", "xiaohongshu": "Xiaohongshu · PGY (views = note reads)"}


def table(rows: List[Dict[str, Any]], top: int, cny_per_usd: Optional[Decimal]) -> str:
    """One block per platform, `top` rows each."""
    blocks = []
    for platform in PLATFORMS:
        mine = [r for r in rows if r["platform"] == platform]
        if mine:
            blocks.append(MARKETPLACES[platform] + "\n" + platform_table(mine, top, cny_per_usd))
    return "\n".join(blocks)


def platform_table(rows: List[Dict[str, Any]], top: int, cny_per_usd: Optional[Decimal]) -> str:
    price_head = "rate ¥ (US$)" if cny_per_usd else "rate ¥"
    per_head = "¥ / 1k views (US$)" if cny_per_usd else "¥ / 1k views"
    head = (f"  #  {fit('creator', 18)}  {'followers':>9}  {'median views':>12}  "
            f"{price_head:>16}  {per_head:>18}  categories")
    lines = [head]
    for n, r in enumerate(rows[:top], 1):
        price = "" if r["cheapest_rate_cny"] is None else f"{r['cheapest_rate_cny']:,}"
        per = "" if r["cny_per_1k_views"] is None else f"{r['cny_per_1k_views']:,}"
        if cny_per_usd and r["cheapest_rate_usd"] is not None:
            price += f" ({r['cheapest_rate_usd']:,.0f})"
        if cny_per_usd and r["usd_per_1k_views"] is not None:
            per += f" ({r['usd_per_1k_views']:,})"
        cats = " / ".join(r["categories"].split(" / ")[:2])
        lines.append(f"{n:>3}  {fit(r['name'], 18)}  {short_count(r['followers']):>9}  "
                     f"{short_count(r['median_views']):>12}  {price:>16}  {per:>18}  {cats}")
    if len(rows) > top:
        lines.append(f"  ...and {len(rows) - top} more in the CSV")
    return "\n".join(lines) + "\n"


# --- the command -------------------------------------------------------------------------------------------------

def hint(code: str) -> str:
    return {
        "unauthorized": f"Check OKDATA_API_KEY, or get a key at {SIGN_UP}",
        "insufficient_balance": f"Top up at {CONSOLE} and run again.",
        "key_budget_exceeded": f"This key reached the spending limit set for it at {CONSOLE}",
        "budget_reached": "Stopped at --budget.",
        "cost_cap_exceeded": "The account reached its daily spending allowance; it resets at the next UTC day.",
    }.get(code, "")


def default_out(queries: List[str]) -> Path:
    stem = re.sub(r"[^\w一-鿿-]+", "-", "-".join(queries)).strip("-")[:40] or "shortlist"
    return Path("shortlists") / f"{stem}-{datetime.now(timezone.utc):%Y-%m-%d}.csv"


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # -sig: Excel opens Chinese text correctly
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: cell("" if v is None else v) for k, v in row.items()} for row in rows)


def run(args: argparse.Namespace, ok: Okdata) -> int:
    plan = [(p, q) for q in args.query for p in args.platform]
    caps = ok.prices()
    most = sum((caps[PLATFORMS[p][0]] * args.pages for p, _ in plan), Decimal("0"))
    out = Path(args.out) if args.out else default_out(args.query)
    low, high = args.followers
    span = f"{low or 0:,} to {high:,} followers" if high else f"at least {low or 0:,} followers"
    print(f"plan: {len(plan)} search(es) x up to {args.pages} page(s), {span}; at most ${usd(most)}", file=sys.stderr)
    for platform, query in plan:
        endpoint = PLATFORMS[platform][0]
        print(f"  {platform}: {query}  ({endpoint}, ${usd(caps[endpoint])} per page)", file=sys.stderr)
    print(f"  writes {out}", file=sys.stderr)
    if args.dry_run:
        return 0
    if most > args.budget:
        print(f"over the --budget of ${usd(args.budget)}; raise it or use fewer --pages", file=sys.stderr)
        return 3
    if not ok.key:
        print(f"OKDATA_API_KEY is not set. Get a key at {SIGN_UP}", file=sys.stderr)
        return 2

    found: Dict[Tuple[str, str], Dict[str, Any]] = {}
    failed, stopped = 0, False
    for platform, query in plan:
        before = len(found)
        try:
            for item in search(ok, platform, query, args):
                row = to_row(platform, item, args.cny_per_usd)
                found.setdefault((platform, row["creator_id"]), row)
            print(f"{platform}: {query}: {len(found) - before} new creators", file=sys.stderr)
        except ApiError as err:
            failed += 1
            print(f"{platform}: {query}: {err} (charged: {charged(err)}). {hint(err.code)}".rstrip(), file=sys.stderr)
            if err.code in STOP_CODES:
                stopped = True
                break
    rows = best_value_first(list(found.values()))
    if rows:
        write_csv(out, rows)
        print(f"wrote {len(rows)} creators to {out}", file=sys.stderr)
        sys.stdout.write(table(rows, args.top, args.cny_per_usd))
    print(f"spent ${usd(ok.spent)}", file=sys.stderr)
    if stopped or failed == len(plan):
        return 1
    return 4 if failed else 0


def query_text(value: str) -> str:
    text = WHITESPACE.sub(" ", value).strip(" ")
    if not text or len(text) > QUERY_LIMIT:
        raise argparse.ArgumentTypeError(f"not a search query (1 to {QUERY_LIMIT} characters): {value!r}")
    return text


def count(text: str) -> int:
    found = COUNT.match(text.strip().replace(",", ""))
    if not found:
        raise argparse.ArgumentTypeError(f"not a follower count: {text!r} (examples: 50000, 50k, 1.2m)")
    number, unit = Decimal(found.group(1)), found.group(2).lower()
    return int(number * {"": 1, "k": 1_000, "m": 1_000_000}[unit])


def followers(value: str) -> Tuple[Optional[int], Optional[int]]:
    """"100k-1m", "100k-" or "-500k"."""
    low, sep, high = value.partition("-")
    if not sep:
        raise argparse.ArgumentTypeError(f"give a range such as 100k-1m, 100k- or -500k: {value!r}")
    span = (count(low) if low.strip() else None, count(high) if high.strip() else None)
    if span[0] is not None and span[1] is not None and span[0] > span[1]:
        raise argparse.ArgumentTypeError(f"the lower bound is above the upper one: {value!r}")
    return span


def money(value: str) -> Decimal:
    if not AMOUNT.fullmatch(value.strip()):
        raise argparse.ArgumentTypeError(f"not an amount: {value!r}")
    return Decimal(value.strip())


def rate(value: str) -> Decimal:
    amount = money(value)
    if amount <= 0:
        raise argparse.ArgumentTypeError("must be above 0")
    return amount


def positive(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value.strip()) or int(value) < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return int(value)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Find Douyin and Xiaohongshu creators on the official creator marketplaces and write a CSV "
                    "shortlist with their rates and the cost of 1,000 median views (okdata API).")
    p.add_argument("--query", action="append", type=query_text, required=True, metavar="TEXT",
                   help="what the creators post about, in Chinese for best results (repeat for more)")
    p.add_argument("--platform", action="append", choices=tuple(PLATFORMS),
                   help="douyin or xiaohongshu (repeat for both; default both)")
    p.add_argument("--followers", type=followers, default=(None, None), metavar="RANGE",
                   help="follower range such as 100k-1m, 100k- or -500k (default any)")
    p.add_argument("--by", choices=("content", "name"), default="content",
                   help="match the query against what creators post (default) or against their names")
    p.add_argument("--pages", type=positive, default=1, metavar="N",
                   help="pages per search, about 20 creators each (default 1)")
    p.add_argument("--cny-per-usd", type=rate, metavar="RATE",
                   help="yuan per US dollar, to add US$ columns (e.g. 7.1); none by default")
    p.add_argument("--out", metavar="FILE", help="CSV to write (default shortlists/<query>-<date>.csv)")
    p.add_argument("--top", type=positive, default=10, metavar="N", help="rows to print per platform (default 10)")
    p.add_argument("--budget", type=money, default=Decimal("0.5"), metavar="USD",
                   help="refuse runs that could cost more (USD, default 0.5)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and the most it can cost, then stop")
    args = p.parse_args(argv)
    args.platform = list(dict.fromkeys(args.platform or PLATFORMS))
    args.query = list(dict.fromkeys(args.query))
    return args


def main(argv: Optional[List[str]] = None) -> int:
    use_utf8()
    args = parse_args(argv)
    ok = Okdata(os.environ.get("OKDATA_API_KEY", "").strip() or None, budget=args.budget)
    try:
        return run(args, ok)
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
