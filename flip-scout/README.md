# Obenauf Flip Scout

An agent that watches **Obenauf Auction Service (Round Lake, IL)** auctions around the clock and emails
you lots that look like **$10k–$20k flips**: lake harvesters, military trucks, municipal and heavy
equipment, boats, and anything else with real margin.

## What it does (every 2 hours)

1. **Crawls** Obenauf's current auctions on `obenaufauctionsonline.com` and `bid.obenaufauctionsonline.com`
   (plus their AuctionZip page) with a headless browser, including every catalog page.
2. **Screens every lot** with a fast, cheap Claude model, which flags anything that could clear your
   profit target or is in a category you've already flipped.
3. **Appraises the shortlist** with Claude Opus. It searches the web for sold and asking comps, then
   estimates where bidding will end, your all-in cost (buyer's premium, tax, transport, repairs, selling
   fees), resale range, profit, and a **walk-away max bid**.
4. **Emails you** each deal that clears the bar, with comps, risks, and a pre-bid inspection checklist.
   It sends a second **"closing soon"** reminder about a day before each flagged lot closes.

Each lot is analyzed and emailed once. Lots it has seen are saved in `state/seen.json`.

## One-time setup (about 10 minutes)

It runs free on GitHub Actions. In this repo on GitHub, go to **Settings → Secrets and variables →
Actions → New repository secret** and add:

| Secret | What to put |
|---|---|
| `ANTHROPIC_API_KEY` | An API key from https://console.anthropic.com (add a few dollars of credit) |
| `SMTP_USER` | The Gmail address that sends the alerts |
| `SMTP_PASSWORD` | A Gmail **App Password** for that account (Google Account → Security → 2-Step Verification → App passwords), not your normal password |
| `ALERT_EMAILS` | Who gets alerts, comma-separated, e.g. `you@gmail.com,partner@gmail.com` |
| `WEBHOOK_URL` *(optional)* | A Discord or Slack incoming-webhook URL if you also want alerts in a channel |

Not using Gmail? Also set `SMTP_HOST` and `SMTP_PORT` (SSL port, usually 465).

**Text messages:** add your carrier's email-to-text address to `ALERT_EMAILS`, for example
`5551234567@vtext.com` (Verizon) or `5551234567@txt.att.net` (AT&T).

Then go to **Actions → Obenauf Flip Scout → Run workflow** to run it right away. Check the **dry run**
box if you want to see the results in the log without sending email.

> GitHub only runs scheduled workflows from the repository's **default branch**, so this has to be
> merged there to run automatically.

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
