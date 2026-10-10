"""Obenauf Flip Scout: scan Obenauf auctions and alert on lots worth flipping.

    python scout.py               # normal run (used by the scheduled workflow)
    python scout.py --dry-run     # analyze but don't send alerts or save state
    python scout.py --dump pages  # also save the text of every crawled page
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

import analyst
import notify
from scraper import Crawler

HERE = Path(__file__).parent
STATE_FILE = HERE / "state" / "seen.json"
CENTRAL = ZoneInfo("America/Chicago")
STATE_RETENTION_DAYS = 120
REMIND_WITHIN = timedelta(hours=30)

log = logging.getLogger("scout")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    cutoff = (datetime.now(CENTRAL) - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    state = {k: v for k, v in state.items() if v["first_seen"] >= cutoff}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


def lot_key(lot: dict) -> str:
    # Lot detail URLs are unique; fall back to auction + lot number + title.
    if lot["url"] != lot["auction_url"]:
        return lot["url"]
    return f"{lot['auction_url']}#{lot.get('lot_number')}#{lot['title']}"


def parse_close(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=CENTRAL)


def is_alert_worthy(deal: dict, cfg: dict) -> bool:
    return (
        deal["verdict"] != "PASS"
        and deal["est_profit_at_expected_hammer"] >= cfg["alert_profit_floor"]
        and deal["all_in_cost_at_expected_hammer"] <= cfg["max_all_in_budget"]
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump", type=Path, help="directory to save crawled page text for debugging")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    state = load_state()
    now = datetime.now(CENTRAL)
    shortlist: list[dict] = []

    with Crawler(cfg, dump_dir=args.dump) as crawler:
        auctions = crawler.discover_auctions()
        for auction in auctions:
            for page in crawler.crawl_catalog(auction):
                try:
                    lots = analyst.screen_page(page, cfg)
                except Exception:
                    log.exception("screening failed for %s", page.snapshot.url)
                    continue
                for lot in lots:
                    key = lot_key(lot)
                    entry = state.get(key)
                    if entry is None:
                        entry = state[key] = {"first_seen": now.isoformat(), "title": lot["title"]}
                        if lot["deep_dive"]:
                            shortlist.append(lot)
                    entry.update(
                        current_bid=lot["current_bid"], closes_at=lot["closes_at"], last_seen=now.isoformat()
                    )
        log.info("screened %d new lots worth a deep dive", len(shortlist))

        new_deals: list[dict] = []
        for lot in shortlist[: cfg["max_deep_dives_per_run"]]:
            detail = None
            if lot["url"] != lot["auction_url"]:
                snap = crawler.snapshot(lot["url"])
                detail = snap.text if snap else None
            try:
                deal = analyst.appraise(lot, detail, cfg)
            except Exception:
                log.exception("appraisal failed for %s", lot["title"])
                deal = None
            entry = state[lot_key(lot)]
            if deal is None:
                # Leave it un-appraised so the next run retries it.
                del state[lot_key(lot)]
                continue
            entry["deal"] = deal
            entry["lot"] = lot
            log.info("%s: %s, est. profit %s", lot["title"], deal["verdict"], deal["est_profit_at_expected_hammer"])
            if is_alert_worthy(deal, cfg):
                lot["deal"] = deal
                new_deals.append(lot)
                entry["alerted"] = now.isoformat()
        # Lots past the deep-dive cap get forgotten so the next run picks them up.
        for lot in shortlist[cfg["max_deep_dives_per_run"]:]:
            state.pop(lot_key(lot), None)

    reminders: list[dict] = []
    for entry in state.values():
        close = parse_close(entry.get("closes_at"))
        if (
            entry.get("alerted")
            and not entry.get("reminded")
            and entry["alerted"] != now.isoformat()
            and close
            and now < close <= now + REMIND_WITHIN
        ):
            lot = dict(entry["lot"], deal=entry["deal"], current_bid=entry.get("current_bid"))
            reminders.append(lot)
            entry["reminded"] = now.isoformat()

    new_deals.sort(key=lambda l: l["deal"]["est_profit_at_expected_hammer"], reverse=True)
    if new_deals or reminders:
        subject, html_body, text = notify.build_report(new_deals, reminders)
        if args.dry_run:
            print(text)
            (HERE / "last_report.html").write_text(html_body)
        else:
            notify.send(subject, html_body, text)
    else:
        log.info("nothing alert-worthy this run")

    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
