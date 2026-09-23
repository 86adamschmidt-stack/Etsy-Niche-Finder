# Etsy Niche Finder

A small, personal, **read-only** market-research tool. It helps me decide which personalized products to design and make for a future Etsy shop, by comparing buyer demand with seller competition for a list of search keywords.

This is not a commercial product. It is not offered to other users, and no Etsy data is resold, republished, or shared.

## What it does

For each keyword in a text file, the script:

1. Searches **public active listings** (`findAllListingsActive`) and records how many listings match. This is the **supply**.
2. For the top listings in that search, counts **recent public reviews** (`getReviewsByListing`) to estimate recent sales. This is the **demand**.
3. Scores each keyword by estimated monthly sales per 100 active listings and flags:
   - `GAP`: meaningful demand with few competing listings
   - `promising`, `crowded`, `one incumbent`, `too small`
4. Optionally (`--expand N`) suggests related long-tail keywords, taken from the tags on top listings, and scores those too.

Results are written to a local CSV file for my own review.

## Etsy API usage

- **Endpoints:** public, read-only listing search and listing reviews only. No OAuth, no shop data, no buyer data, and no write operations.
- **Volume:** low. It runs manually, a few times a month, at a few hundred to about 2,000 requests per run.
- **Rate limiting:** capped at 4 requests/second, with exponential back-off on `429` responses.
- **Caching:** responses are cached locally for 24 hours, so repeat runs don't hit the API again.
- **Data handling:** data stays on my machine and is used only for aggregate counts. Nothing is stored beyond the local cache and the output CSV.

## Setup

Requires Python 3.9+ and `requests`.

```bash
pip install requests
export ETSY_KEYSTRING="your_keystring"
export ETSY_SHARED_SECRET="your_shared_secret"
```

Credentials are read from environment variables and are never committed to this repository.

## Usage

```bash
python niche_finder.py seeds.txt                 # score the keywords in seeds.txt
python niche_finder.py seeds.txt --expand 30     # also score 30 suggested long-tail keywords
python niche_finder.py seeds.txt --top 15 --days 60
```

| Option | Default | Meaning |
|---|---|---|
| `--top` | 25 | Top listings sampled per keyword for demand |
| `--days` | 90 | Review lookback window |
| `--review-rate` | 0.15 | Assumed share of orders that leave a review |
| `--min-sales` | 30 | Estimated monthly sales below this are marked `too small` |
| `--expand` | 0 | Number of long-tail keywords to add from listing tags |
| `--out` | `niche_results.csv` | Output file |

## Notes

The sales figures are estimates based on an assumed review rate, so use them to rank keywords against each other rather than as exact sales.

*The term "Etsy" is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.*
