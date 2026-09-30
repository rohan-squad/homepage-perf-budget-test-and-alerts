#!/usr/bin/env python3
"""
Post a performance-budget alert to Slack via a bot token (chat.postMessage).

Severity rules:
  red   -> BLOCKER, pings the whole channel with <!channel>
  amber -> heads-up, mentions named people (passed via --mention), no channel-wide ping

The bot token is read from the SLACK_BOT_TOKEN environment variable (a GitHub secret).
Requires the 'chat:write' scope and the bot to be a member of the target channel.
"""
import argparse, json, os, sys, urllib.request

def load_breaches(path):
    """Return a list of human-readable breach strings from a Sitespeed budgetResult.json."""
    breaches = []
    if not path or not os.path.exists(path):
        return breaches
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return breaches
    # Sitespeed budgetResult.json is a list of checks, each with status and metric info.
    items = data if isinstance(data, list) else data.get("results", data.get("budget", []))
    if isinstance(items, dict):
        items = [items]
    for it in items or []:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status", "")).lower()
        if status and status != "fail":
            continue
        metric = it.get("metric") or it.get("name") or it.get("key") or "metric"
        value = it.get("value", it.get("actual", "?"))
        limit = it.get("limit", it.get("budget", it.get("max", "?")))
        breaches.append(f"{metric}: {value} (limit {limit})")
    return breaches

def post(token, channel, text):
    payload = json.dumps({
        "channel": channel,
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        print(f"Slack API error: {body.get('error')}", file=sys.stderr)
        sys.exit(1)
    print("Slack message posted.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--severity", choices=["red", "amber"], required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--budget-file", default="")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--mention", default="")
    args = ap.parse_args()

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("SLACK_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    breaches = load_breaches(args.budget_file)
    bullet = "\n".join(f"  • {b}" for b in breaches) if breaches else "  • (see the CI artifact for details)"

    if args.severity == "red":
        header = f":red_circle: <!channel> *BLOCKER — performance budget failed* ({args.device})"
        footer = "This change should not ship. Fix before publishing."
    else:
        who = args.mention.strip()
        header = f":large_yellow_circle: {who} *Heads-up — performance drifted into amber* ({args.device})".rstrip()
        footer = "Not a blocker. Worth a look before it becomes one."

    text = f"{header}\n<{args.url}|{args.url}>\n{bullet}\n{footer}"
    post(token, args.channel, text)

if __name__ == "__main__":
    main()
