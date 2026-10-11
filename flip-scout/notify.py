"""Send deal alerts by email (and optionally a Slack/Discord webhook)."""

from __future__ import annotations

import html
import json
import logging
import os
import smtplib
import urllib.request
from email.message import EmailMessage

log = logging.getLogger(__name__)

VERDICT_COLORS = {"STRONG BUY": "#1a7f37", "WORTH A BID": "#9a6700", "PASS": "#57606a"}


def _money(x) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "?"


def _deal_html(lot: dict, reminder: bool) -> str:
    d = lot["deal"]
    e = html.escape
    comps = "".join(
        f'<li>{e(c["description"])} - <b>{_money(c["price"])}</b> '
        f'(<a href="{e(c["source_url"])}">source</a>)</li>'
        for c in d["comps"][:6]
    )
    risks = "".join(f"<li>{e(r)}</li>" for r in d["risks"])
    checks = "".join(f"<li>{e(c)}</li>" for c in d["inspection_checklist"])
    color = VERDICT_COLORS.get(d["verdict"], "#57606a")
    tag = "CLOSING SOON - " if reminder else ""
    return f"""
<div style="border:1px solid #d0d7de;border-radius:8px;padding:16px;margin:0 0 20px;font-family:Arial,sans-serif">
  <div style="font-size:12px;color:{color};font-weight:bold">{tag}{e(d['verdict'])} · {e(d['confidence'])} confidence</div>
  <h2 style="margin:6px 0"><a href="{e(lot['url'])}">{e(lot['title'])}</a></h2>
  <div style="color:#57606a;font-size:13px">{e(lot['auction_title'])} · Lot {e(str(lot.get('lot_number') or '?'))}
    · Closes {e(lot.get('closes_at') or 'see listing')}</div>
  <p style="font-size:15px">{e(d['headline'])}</p>
  <table style="font-size:14px;border-collapse:collapse">
    <tr><td style="padding:2px 12px 2px 0">Current bid (when found)</td><td><b>{_money(lot.get('current_bid'))}</b></td></tr>
    <tr><td style="padding:2px 12px 2px 0">Expected hammer</td><td>{_money(d['expected_hammer_price'])}</td></tr>
    <tr><td style="padding:2px 12px 2px 0"><b>Walk-away max bid</b></td><td><b>{_money(d['max_bid'])}</b></td></tr>
    <tr><td style="padding:2px 12px 2px 0">Resale estimate</td><td>{_money(d['est_resale_low'])} - {_money(d['est_resale_high'])}</td></tr>
    <tr><td style="padding:2px 12px 2px 0">All-in cost @ expected hammer</td><td>{_money(d['all_in_cost_at_expected_hammer'])}
      (repairs {_money(d['repair_estimate'])}, transport {_money(d['transport_estimate'])})</td></tr>
    <tr><td style="padding:2px 12px 2px 0"><b>Est. profit</b></td><td><b style="color:{color}">{_money(d['est_profit_at_expected_hammer'])}</b></td></tr>
    <tr><td style="padding:2px 12px 2px 0">Time to sell</td><td>~{d['days_to_sell']} days · {e(d['where_to_sell'])}</td></tr>
  </table>
  <p style="font-size:13px"><b>Buyers:</b> {e(d['buyer_pool'])}</p>
  <p style="font-size:13px;margin-bottom:2px"><b>Comps</b></p><ul style="font-size:13px;margin-top:0">{comps}</ul>
  <p style="font-size:13px;margin-bottom:2px"><b>Risks</b></p><ul style="font-size:13px;margin-top:0">{risks}</ul>
  <p style="font-size:13px;margin-bottom:2px"><b>Check before bidding</b></p><ul style="font-size:13px;margin-top:0">{checks}</ul>
</div>"""


def build_report(new_deals: list[dict], reminders: list[dict]) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_text_summary)."""
    best = max((l["deal"]["est_profit_at_expected_hammer"] for l in new_deals + reminders), default=0)
    parts = []
    if new_deals:
        parts.append(f"{len(new_deals)} new flip{'s' if len(new_deals) != 1 else ''}")
    if reminders:
        parts.append(f"{len(reminders)} closing soon")
    subject = f"Obenauf Flip Scout: {', '.join(parts)} (top est. profit {_money(best)})"

    body = ['<div style="max-width:720px">']
    if new_deals:
        body.append('<h1 style="font-family:Arial,sans-serif">New flip candidates</h1>')
        body += [_deal_html(l, False) for l in new_deals]
    if reminders:
        body.append('<h1 style="font-family:Arial,sans-serif">Closing within a day</h1>')
        body += [_deal_html(l, True) for l in reminders]
    body.append(
        '<p style="color:#57606a;font-size:12px;font-family:Arial,sans-serif">Estimates are AI-generated from '
        "the listing and web comps. Always inspect in person and read the auction terms before bidding.</p></div>"
    )

    lines = [subject]
    for l in new_deals + reminders:
        d = l["deal"]
        lines.append(
            f"- {d['verdict']}: {l['title']} | max bid {_money(d['max_bid'])} | "
            f"est. profit {_money(d['est_profit_at_expected_hammer'])} | {l['url']}"
        )
    return subject, "\n".join(body), "\n".join(lines)


def send(subject: str, html_body: str, text: str) -> None:
    sent = False
    recipients = [a.strip() for a in os.environ.get("ALERT_EMAILS", "").split(",") if a.strip()]
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if recipients and user and password:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("SMTP_FROM") or user
        msg["To"] = ", ".join(recipients)
        msg.set_content(text)
        msg.add_alternative(html_body, subtype="html")
        host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
        port = int(os.environ.get("SMTP_PORT") or 465)
        with smtplib.SMTP_SSL(host, port) as s:
            s.login(user, password)
            s.send_message(msg)
        log.info("emailed %s", ", ".join(recipients))
        sent = True

    hook = os.environ.get("WEBHOOK_URL")
    if hook:
        # Discord reads "content", Slack reads "text"; sending both works for either.
        payload = json.dumps({"content": text[:1900], "text": text}).encode()
        req = urllib.request.Request(hook, payload, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20)
        log.info("posted to webhook")
        sent = True

    if not sent:
        log.info("no email/webhook configured - finds are only saved to reports/finds/")


def _deal_md(lot: dict, reminder: bool) -> str:
    d = lot["deal"]
    lines = [
        f"### {'⏰ CLOSING SOON - ' if reminder else ''}{d['verdict']}: {lot['title']}",
        "",
        f"**Listing:** {lot['url']}  ",
        f"**Auction:** {lot['auction_title']} · Lot {lot.get('lot_number') or '?'} · "
        f"Closes {lot.get('closes_at') or 'see listing'}",
        "",
        d["headline"],
        "",
        f"- Current bid (when found): {_money(lot.get('current_bid'))}",
        f"- Expected hammer: {_money(d['expected_hammer_price'])}",
        f"- **Walk-away max bid: {_money(d['max_bid'])}**",
        f"- Resale estimate: {_money(d['est_resale_low'])} - {_money(d['est_resale_high'])}",
        f"- All-in cost @ expected hammer: {_money(d['all_in_cost_at_expected_hammer'])} "
        f"(repairs {_money(d['repair_estimate'])}, transport {_money(d['transport_estimate'])})",
        f"- **Est. profit: {_money(d['est_profit_at_expected_hammer'])}** ({d['confidence']} confidence)",
        f"- Time to sell: ~{d['days_to_sell']} days · {d['where_to_sell']}",
        f"- Buyers: {d['buyer_pool']}",
        "",
        "Comps:",
        *[f"- {c['description']} - {_money(c['price'])} ({c['source_url']})" for c in d["comps"][:6]],
        "",
        "Risks:",
        *[f"- {r}" for r in d["risks"]],
        "",
        "Check before bidding:",
        *[f"- {c}" for c in d["inspection_checklist"]],
        "",
    ]
    return "\n".join(lines)


def build_markdown(new_deals: list[dict], reminders: list[dict], run_time: str) -> str:
    parts = [f"# Flip Scout finds - {run_time}", ""]
    if new_deals:
        parts += ["## New flip candidates", ""] + [_deal_md(l, False) for l in new_deals]
    if reminders:
        parts += ["## Closing within a day", ""] + [_deal_md(l, True) for l in reminders]
    return "\n".join(parts)
