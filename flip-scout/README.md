# Obenauf Flip Scout

An agent that watches **Obenauf Auction Service (Round Lake, IL)** auctions around the clock and reports
lots that look like **$10k–$20k flips**: lake harvesters, military trucks, municipal and heavy
equipment, boats, and anything else with real margin.

## What it does (every 2 hours)

1. **Crawls** Obenauf's current auctions on `obenaufauctionsonline.com` and `bid.obenaufauctionsonline.com`
   (plus their AuctionZip page) with a headless browser, including every catalog page.
2. **Screens every lot** with a fast, cheap Claude model, which flags anything that could clear your
   profit target or is in a category you've already flipped.
3. **Appraises the shortlist** with Claude Opus. It searches the web for sold and asking comps, then
   estimates where bidding will end, your all-in cost (buyer's premium, tax, transport, repairs, selling
   fees), resale range, profit, and a **walk-away max bid**.
4. **Saves each deal** that clears the bar to `reports/finds/` in this repo, with the listing link, max bid,
   comps, risks and an inspection checklist. It saves a second **"closing soon"** entry about a day before
   each flagged lot closes. `reports/last_scan.md` always shows what the latest scan covered.
5. **Claude posts the finds in your Claude Code chat.** A scheduled check reads `reports/finds/` and posts
   any new listings in your Claude Code session, so you can email the listings yourself.

Each lot is analyzed and reported once. Lots it has seen are saved in `state/seen.json`.

## One-time setup

It runs free on GitHub Actions. In this repo on GitHub, go to **Settings → Secrets and variables →
Actions → New repository secret** and add:

| Secret | What to put |
|---|---|
| `ANTHROPIC_API_KEY` | An API key from https://console.anthropic.com (add a few dollars of credit) |

Then go to **Actions → Obenauf Flip Scout → Run workflow** to run it right away. Check the **dry run**
box if you want to see the results in the log without saving anything.

> GitHub only runs scheduled workflows from the repository's **default branch**, so this has to be
> merged there to run automatically.

### Optional: automatic email or Discord/Slack alerts

Off by default. To have the scanner send alerts itself, add `SMTP_USER` (a Gmail address), `SMTP_PASSWORD`
(a Gmail App Password), and `ALERT_EMAILS` (comma-separated), and/or `WEBHOOK_URL` (a Discord or Slack
incoming webhook). For a non-Gmail server, also set `SMTP_HOST` and `SMTP_PORT`.

## Tuning

Everything lives in [`config.yaml`](config.yaml):

- `target_profit_min` / `target_profit_max` / `alert_profit_floor` set what counts as a deal.
- `max_all_in_budget` is the most you'll spend on one item.
- `track_record` and `interests` describe what you've flipped and what you like, and steer the analyst.
- `buyer_premium_pct`, `sales_tax_pct`, and the other cost settings should match your real costs. Set
  sales tax to 0 if you buy with a resale certificate.
- `max_deep_dives_per_run` caps API spend per run.

To change how often it runs, edit the `cron` line in `.github/workflows/flip-scout.yml`.

## Cost

Screening a catalog page with Haiku costs about a cent. Each Opus appraisal with web search costs
roughly $0.20–$1. A typical week of Obenauf auctions should come to a few dollars to maybe $20.

## If it stops finding lots

The scanner reads pages generically, so site changes usually don't break it. If a run finds 0 auctions
or 0 lots, download the **crawled-pages** artifact from that run. It contains the text and links of every
page the scanner saw. Adjust `start_urls`, `auction_url_patterns`, or `item_url_patterns` in
`config.yaml` to match the site's current links.

## Run locally

```bash
cd flip-scout
pip install -r requirements.txt && python -m playwright install chromium
export ANTHROPIC_API_KEY=...
python scout.py --dry-run --dump pages
```
