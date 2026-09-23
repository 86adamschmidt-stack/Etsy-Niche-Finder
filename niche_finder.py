#!/usr/bin/env python3
"""
niche_finder.py — find Etsy niches where demand outruns supply.

For each keyword it measures:
  SUPPLY  = total active listings matching the keyword (Etsy's search count)
  DEMAND  = reviews left in the last N days on the top listings, scaled up by an
            assumed review rate to estimate monthly sales
  SCORE   = estimated monthly sales per 100 active listings (demand ÷ supply)

It also reports how concentrated demand is (one incumbent vs. spread out),
the median price, and how many top listings are personalizable. With --expand
it mines the tags of top listings for long-tail keywords and scores those too.

Usage:
  export ETSY_KEYSTRING=...   ETSY_SHARED_SECRET=...
  python niche_finder.py seeds.txt                 # score the seed keywords
  python niche_finder.py seeds.txt --expand 30     # + 30 mined long-tail keywords
  python niche_finder.py seeds.txt --top 15 --days 60

Output: niche_results.csv (sorted by score) and a ranked table in the terminal.
Responses are cached in .etsy_cache/ so re-runs are fast and cheap.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import requests

API = "https://openapi.etsy.com/v3/application"
CACHE_DIR = Path(".etsy_cache")
CACHE_TTL_HOURS = 24


class EtsyClient:
    def __init__(self, keystring, shared_secret, qps=4.0):
        self.session = requests.Session()
        # Since Feb 9, 2026 Etsy requires "keystring:shared_secret" in this header.
        self.session.headers["x-api-key"] = f"{keystring}:{shared_secret}"
        self.min_interval = 1.0 / qps
        self._last = 0.0
        self.calls = 0
        CACHE_DIR.mkdir(exist_ok=True)

    def get(self, path, **params):
        key = hashlib.sha1(f"{path}?{sorted(params.items())}".encode()).hexdigest()
        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_TTL_HOURS * 3600:
            return json.loads(cache_file.read_text())

        for attempt in range(5):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            r = self.session.get(f"{API}{path}", params=params, timeout=30)
            self.calls += 1
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 403 and "secret" in r.text.lower():
                sys.exit("403: Etsy wants keystring:shared_secret — check ETSY_SHARED_SECRET.")
            r.raise_for_status()
            data = r.json()
            cache_file.write_text(json.dumps(data))
            return data
        raise RuntimeError(f"Gave up on {path} after retries")

    def search(self, keywords, limit=100):
        return self.get("/listings/active", keywords=keywords, limit=limit,
                        sort_on="score", sort_order="desc")

    def recent_review_count(self, listing_id, since_ts):
        data = self.get(f"/listings/{listing_id}/reviews", limit=1, min_created=since_ts)
        return data.get("count", 0)


def price_of(listing):
    p = listing.get("price") or {}
    try:
        return p["amount"] / p["divisor"]
    except (KeyError, ZeroDivisionError, TypeError):
        return None


def analyze_keyword(client, keyword, top_n, days, review_rate):
    data = client.search(keyword)
    supply = data.get("count", 0)
    listings = data.get("results", [])
    top = listings[:top_n]

    since = int(time.time()) - days * 86400
    per_listing = []
    for l in top:
        n = client.recent_review_count(l["listing_id"], since)
        per_listing.append(n)

    recent_reviews = sum(per_listing)
    est_monthly_sales = recent_reviews / review_rate * (30 / days)

    # Demand concentration: share of recent reviews held by the top 3 listings.
    ranked = sorted(per_listing, reverse=True)
    top3_share = (sum(ranked[:3]) / recent_reviews) if recent_reviews else 0.0
    sellers_with_sales = sum(1 for n in per_listing if n > 0)

    prices = [p for p in (price_of(l) for l in top) if p]
    new_entrants = sum(1 for l in listings
                       if l.get("original_creation_timestamp", 0) >= since)
    personalizable = sum(1 for l in top if l.get("is_personalizable"))

    score = est_monthly_sales / max(supply, 1) * 100

    return {
        "keyword": keyword,
        "active_listings": supply,
        "recent_reviews_topN": recent_reviews,
        "est_monthly_sales_topN": round(est_monthly_sales, 1),
        "sales_per_100_listings": round(score, 2),
        "top3_demand_share": round(top3_share, 2),
        "topN_listings_selling": f"{sellers_with_sales}/{len(top)}",
        "median_price": round(statistics.median(prices), 2) if prices else "",
        "new_listings_in_window_top100": new_entrants,
        "personalizable_topN": f"{personalizable}/{len(top)}",
        "_tags": [t.lower() for l in listings for t in (l.get("tags") or [])],
    }


def mine_long_tail(results, existing, how_many):
    """Pull frequent multi-word tags from top listings as new candidate keywords."""
    counts = Counter()
    for r in results:
        counts.update(t for t in set(r["_tags"]) if len(t.split()) >= 2)
    seen = {k.lower() for k in existing}
    return [t for t, _ in counts.most_common() if t not in seen][:how_many]


def verdict(r, min_sales):
    if r["est_monthly_sales_topN"] < min_sales:
        return "too small"
    if r["top3_demand_share"] > 0.7:
        return "one incumbent"
    if r["active_listings"] < 1500 and r["sales_per_100_listings"] >= 5:
        return "GAP"
    if r["sales_per_100_listings"] >= 2:
        return "promising"
    return "crowded"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", help="text file, one keyword per line (# for comments)")
    ap.add_argument("--top", type=int, default=25, help="top listings to sample for demand (default 25)")
    ap.add_argument("--days", type=int, default=90, help="review lookback window in days (default 90)")
    ap.add_argument("--review-rate", type=float, default=0.15,
                    help="assumed share of orders that leave a review (default 0.15)")
    ap.add_argument("--min-sales", type=float, default=30,
                    help="est. monthly sales below this = 'too small' (default 30)")
    ap.add_argument("--expand", type=int, default=0, help="also score N long-tail keywords mined from tags")
    ap.add_argument("--out", default="niche_results.csv")
    args = ap.parse_args()

    key, secret = os.environ.get("ETSY_KEYSTRING"), os.environ.get("ETSY_SHARED_SECRET")
    if not key or not secret:
        sys.exit("Set ETSY_KEYSTRING and ETSY_SHARED_SECRET (from etsy.com/developers/your-apps).")

    seeds = [s.strip() for s in Path(args.seeds).read_text().splitlines()
             if s.strip() and not s.strip().startswith("#")]
    total_kw = len(seeds) + args.expand
    print(f"Scoring {len(seeds)} seeds (+{args.expand} mined). "
          f"Up to ~{total_kw * (args.top + 1)} API calls; cached calls are free.\n")

    client = EtsyClient(key, secret)
    results = []

    def run(keywords):
        for i, kw in enumerate(keywords, 1):
            try:
                r = analyze_keyword(client, kw, args.top, args.days, args.review_rate)
                results.append(r)
                print(f"  [{i}/{len(keywords)}] {kw}: {r['active_listings']} listings, "
                      f"~{r['est_monthly_sales_topN']}/mo -> {r['sales_per_100_listings']}")
            except Exception as e:
                print(f"  [{i}/{len(keywords)}] {kw}: ERROR {e}")

    run(seeds)
    if args.expand:
        mined = mine_long_tail(results, seeds, args.expand)
        print(f"\nMined long-tail keywords: {', '.join(mined)}\n")
        run(mined)

    for r in results:
        r["verdict"] = verdict(r, args.min_sales)
        r.pop("_tags", None)
    results.sort(key=lambda r: (r["verdict"] == "too small", -r["sales_per_100_listings"]))

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    print(f"\n{'keyword':<40}{'listings':>9}{'sales/mo':>10}{'score':>8}{'top3':>6}  verdict")
    print("-" * 85)
    for r in results[:30]:
        print(f"{r['keyword'][:39]:<40}{r['active_listings']:>9}{r['est_monthly_sales_topN']:>10}"
              f"{r['sales_per_100_listings']:>8}{r['top3_demand_share']:>6}  {r['verdict']}")
    print(f"\nSaved {len(results)} rows to {args.out} ({client.calls} live API calls).")


if __name__ == "__main__":
    main()
