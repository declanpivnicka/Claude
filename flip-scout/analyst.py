"""Claude-powered screening and valuation of auction lots."""

from __future__ import annotations

import json
import logging
from datetime import date

import anthropic

from scraper import CatalogPage

log = logging.getLogger(__name__)

client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# Pass 1: read a catalog page, pull out every lot, and flag the ones worth a
# real valuation. Cheap model, one call per catalog page.
# ---------------------------------------------------------------------------

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "lots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "link_index": {
                        "type": ["integer", "null"],
                        "description": "Index into LOT LINKS for this lot's detail page, or null if none.",
                    },
                    "lot_number": {"type": ["string", "null"]},
                    "title": {"type": "string"},
                    "summary": {
                        "type": "string",
                        "description": "Year/make/model, miles/hours, condition notes - whatever the listing says.",
                    },
                    "current_bid": {"type": ["number", "null"]},
                    "closes_at": {
                        "type": ["string", "null"],
                        "description": "Closing time as ISO 8601 in America/Chicago if the page states it, else null.",
                    },
                    "rough_resale_value": {
                        "type": ["number", "null"],
                        "description": "Gut-feel resale value in USD, from general market knowledge.",
                    },
                    "deep_dive": {
                        "type": "boolean",
                        "description": "True if this could plausibly clear the profit target.",
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "link_index", "lot_number", "title", "summary", "current_bid",
                    "closes_at", "rough_resale_value", "deep_dive", "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lots"],
    "additionalProperties": False,
}


def _buyer_profile(cfg: dict) -> str:
    return f"""We are a small two-person flipping operation based near Round Lake, Illinois. We buy at
Obenauf Auction Service (government/municipal surplus, consignment, equipment) and resell.

Profit goal per flip: ${cfg['target_profit_min']:,}-${cfg['target_profit_max']:,} net after ALL costs
(we'll still look at anything above ${cfg['alert_profit_floor']:,}). Max all-in spend: ${cfg['max_all_in_budget']:,}.

What we've flipped successfully before:
{chr(10).join('- ' + t for t in cfg['track_record'])}

Categories we like:
{chr(10).join('- ' + t for t in cfg['interests'])}

Our costs on top of the hammer price: {cfg['buyer_premium_pct']}% buyer's premium, {cfg['sales_tax_pct']}% sales
tax, {cfg['credit_card_fee_pct']}% card fee, ~${cfg['default_transport_cost']:,} transport if it can't be driven,
{cfg['selling_fees_pct']}% selling fees on resale, plus any repairs needed to make it sellable."""


def screen_page(page: CatalogPage, cfg: dict) -> list[dict]:
    links = "\n".join(f"[{i}] {l.text} -> {l.url}" for i, l in enumerate(page.item_links))
    prompt = f"""{_buyer_profile(cfg)}

Below is one page of an online auction catalog: "{page.auction_title}".
Extract EVERY lot on the page. For each, give a gut-feel resale value and decide whether it deserves a
deep-dive valuation. Set deep_dive=true when the item could plausibly sell for at least
${cfg['target_profit_min']:,} more than it is likely to hammer for (remember online auctions usually run
up in the final hours - the current bid is not the final price), or when it's in a category we've done
well with. Be selective: skip routine low-value lots (furniture, small tools, scrap, ordinary
used cars that dealers bid to retail). If the page lists no lots (e.g. a terms page), return an empty list.

PAGE TEXT:
{page.snapshot.text}

LOT LINKS:
{links or '(none found)'}"""

    with client.messages.stream(
        model=cfg["screen_model"],
        max_tokens=32000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCREEN_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason != "end_turn":
        log.warning("screen of %s stopped with %s", page.snapshot.url, resp.stop_reason)
        return []
    text = next(b.text for b in resp.content if b.type == "text")
    lots = json.loads(text)["lots"]
    for lot in lots:
        idx = lot.pop("link_index")
        lot["url"] = (
            page.item_links[idx].url
            if idx is not None and 0 <= idx < len(page.item_links)
            else page.snapshot.url
        )
        lot["auction_title"] = page.auction_title
        lot["auction_url"] = page.auction_url
    return lots


# ---------------------------------------------------------------------------
# Pass 2: full valuation with live comps from the web. Strong model, one call
# per shortlisted lot.
# ---------------------------------------------------------------------------

DEAL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["STRONG BUY", "WORTH A BID", "PASS"]},
        "headline": {"type": "string", "description": "One line: what it is and why it's a deal (or not)."},
        "est_resale_low": {"type": "number"},
        "est_resale_high": {"type": "number"},
        "expected_hammer_price": {"type": "number", "description": "Where we think bidding will actually end."},
        "max_bid": {
            "type": "number",
            "description": "Highest hammer bid that still leaves the minimum target profit at the LOW resale estimate.",
        },
        "repair_estimate": {"type": "number"},
        "transport_estimate": {"type": "number"},
        "all_in_cost_at_expected_hammer": {"type": "number"},
        "est_profit_at_expected_hammer": {"type": "number"},
        "days_to_sell": {"type": "integer"},
        "where_to_sell": {"type": "string"},
        "buyer_pool": {"type": "string", "description": "Who buys these and how deep that market is."},
        "comps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "price": {"type": "number"},
                    "source_url": {"type": "string"},
                },
                "required": ["description", "price", "source_url"],
                "additionalProperties": False,
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "inspection_checklist": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "verdict", "headline", "est_resale_low", "est_resale_high", "expected_hammer_price",
        "max_bid", "repair_estimate", "transport_estimate", "all_in_cost_at_expected_hammer",
        "est_profit_at_expected_hammer", "days_to_sell", "where_to_sell", "buyer_pool",
        "comps", "risks", "inspection_checklist", "confidence",
    ],
    "additionalProperties": False,
}


def appraise(lot: dict, detail_text: str | None, cfg: dict) -> dict | None:
    prompt = f"""{_buyer_profile(cfg)}

Today is {date.today():%B %d, %Y}. Evaluate this auction lot as a flip for us.

Auction: {lot['auction_title']}
Lot: {lot.get('lot_number') or '?'} - {lot['title']}
Listing summary: {lot['summary']}
Current bid: {lot['current_bid'] if lot['current_bid'] is not None else 'unknown'}
Closes: {lot.get('closes_at') or 'unknown'}
Listing URL: {lot['url']}

LISTING DETAIL PAGE TEXT:
{detail_text or '(not available)'}

Use web search to find real comparable asking/sold prices (eBay sold, Machinery Pete, TractorHouse,
IronPlanet/GovPlanet results, Facebook Marketplace, GovDeals/Municibid results, dealer listings,
military-surplus dealers, etc.). Prefer sold prices over asking prices and discount asking prices
accordingly. Estimate where bidding will realistically end, what it costs us all-in, what we can sell it
for, and the resulting profit. Set max_bid so that even at the LOW resale estimate we still clear
${cfg['target_profit_min']:,}. Be honest - say PASS when the margin isn't there or the comps are thin.
Verdicts: STRONG BUY = profit at expected hammer >= ${cfg['target_profit_min']:,} with medium/high
confidence; WORTH A BID = profitable only if it goes cheap (bid up to max_bid); PASS = otherwise."""

    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(4):  # resume server-tool turns that pause mid-search
        with client.beta.messages.stream(
            model=cfg["analyst_model"],
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": cfg["analyst_effort"],
                "format": {"type": "json_schema", "schema": DEAL_SCHEMA},
            },
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}],
            messages=messages,
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    if resp.stop_reason != "end_turn":
        log.warning("appraisal of %s stopped with %s", lot["title"], resp.stop_reason)
        return None
    text = [b.text for b in resp.content if b.type == "text"][-1]
    return json.loads(text)
