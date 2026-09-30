#!/usr/bin/env python3
"""
Build dashboard-data.json for the SquadStack performance dashboard.

Two data sources, kept strictly separate (the sources-of-truth rule):
  - FIELD  : CrUX API (real users, p75) -> the CWV verdict
  - LAB    : Sitespeed budgetResult.json (this run) -> the regression signal

Field zones (from the budget doc, Part 2.1) decide field pass/amber/fail.
Lab values come straight from the budget run, with pass/fail already decided by Sitespeed.

Env:
  CRUX_API_KEY   - Google Cloud key with "Chrome UX Report API" enabled (same key as PSI)
  TARGET_ORIGIN  - e.g. https://www.squadstack.ai  (origin, no trailing path)

Args:
  --mobile-budget  path to mobile red budgetResult.json
  --desktop-budget path to desktop red budgetResult.json
  --out            output path for dashboard-data.json
"""
import argparse, json, os, sys, urllib.request, datetime

CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"

# Field zones from the budget doc (Part 2.1). Values in ms except CLS.
FIELD_ZONES = {
    "LCP": {"green": 2500, "amber": 3500, "unit": "ms"},
    "INP": {"green": 200,  "amber": 300,  "unit": "ms"},
    "CLS": {"green": 0.1,  "amber": 0.25, "unit": ""},
    "FCP": {"green": 1800, "amber": 3000, "unit": "ms"},
    "TTFB":{"green": 800,  "amber": 1400, "unit": "ms"},
}
# CrUX metric key -> our short name
CRUX_MAP = {
    "largest_contentful_paint": "LCP",
    "interaction_to_next_paint": "INP",
    "cumulative_layout_shift": "CLS",
    "first_contentful_paint": "FCP",
    "experimental_time_to_first_byte": "TTFB",
}

def zone_for(metric, value):
    if value is None:
        return "unknown"
    z = FIELD_ZONES.get(metric)
    if not z:
        return "unknown"
    if value < z["green"]:
        return "green"
    if value <= z["amber"]:
        return "amber"
    return "red"

def _crux_call(scope_key, scope_value, form_factor, key):
    """One CrUX call. scope_key is 'url' or 'origin'. Returns parsed metrics dict or None."""
    body = json.dumps({
        scope_key: scope_value,
        "formFactor": form_factor,
        "metrics": list(CRUX_MAP.keys()),
    }).encode()
    endpoint = f"{CRUX_ENDPOINT}?key={key}"
    req = urllib.request.Request(endpoint, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"CrUX {form_factor} {scope_key} query failed: {e}", file=sys.stderr)
        return None
    return data.get("record", {}).get("metrics", {})

def query_crux(target_url, target_origin, form_factor, key):
    """
    Prefer URL-level data (the specific page). Fall back to origin only if the URL
    has no CrUX record (low-traffic page). Returns {metric:{value,zone}, ...} plus
    a '_scope' marker so the dashboard can show which dataset was used.
    """
    metrics = _crux_call("url", target_url, form_factor, key)
    scope = "url"
    if not metrics:
        metrics = _crux_call("origin", target_origin, form_factor, key)
        scope = "origin" if metrics else "none"
    out = {"_scope": scope}
    for crux_key, short in CRUX_MAP.items():
        m = (metrics or {}).get(crux_key)
        if not m:
            continue
        p75 = m.get("percentiles", {}).get("p75")
        if p75 is None:
            continue
        try:
            val = float(p75)
        except (TypeError, ValueError):
            continue
        out[short] = {"value": val, "zone": zone_for(short, val)}
    return out

def _query_crux_legacy(origin, form_factor, key):
    """(unused) previous origin-only implementation kept for reference."""
    body = json.dumps({
        "origin": origin,
        "formFactor": form_factor,
        "metrics": list(CRUX_MAP.keys()),
    }).encode()
    url = f"{CRUX_ENDPOINT}?key={key}"
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        # 404 = origin not in CrUX dataset; other = transient. Either way, degrade gracefully.
        print(f"CrUX {form_factor} query failed: {e}", file=sys.stderr)
        return {}
    out = {}
    metrics = data.get("record", {}).get("metrics", {})
    for crux_key, short in CRUX_MAP.items():
        m = metrics.get(crux_key)
        if not m:
            continue
        p75 = m.get("percentiles", {}).get("p75")
        if p75 is None:
            continue
        # CLS comes as a string decimal; others as ms numbers (or numeric strings)
        try:
            val = float(p75)
        except (TypeError, ValueError):
            continue
        out[short] = {"value": val, "zone": zone_for(short, val)}
    return out

def read_lab(path):
    """Extract lab metrics from a Sitespeed budgetResult.json into {metric: {value, friendly, limit, status}}."""
    result = {"metrics": [], "passed": None}
    if not path or not os.path.exists(path):
        return result
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        print(f"Could not read lab budget {path}: {e}", file=sys.stderr)
        return result
    working = data.get("working", {})
    failing = data.get("failing", {})
    n_fail = 0
    for bucket, status in ((working, "working"), (failing, "failing")):
        for url, items in bucket.items():
            for it in items:
                result["metrics"].append({
                    "metric": it.get("metric"),
                    "type": it.get("type"),
                    "value": it.get("friendlyValue"),
                    "limit": it.get("friendlyLimit"),
                    "status": it.get("status", status),
                })
                if it.get("status", status) == "failing":
                    n_fail += 1
    result["passed"] = (n_fail == 0)
    result["failing_count"] = n_fail
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mobile-budget", required=True)
    ap.add_argument("--desktop-budget", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    origin = os.environ.get("TARGET_ORIGIN", "https://www.squadstack.ai")
    # The specific page to report on. Defaults to the homepage URL (with trailing slash),
    # which is what the homepage budget is about. URL-level CrUX != origin-level.
    target_url = os.environ.get("TARGET_URL", origin.rstrip("/") + "/")
    key = os.environ.get("CRUX_API_KEY", "")

    field = {"mobile": {}, "desktop": {}}
    if key:
        field["mobile"] = query_crux(target_url, origin, "PHONE", key)
        field["desktop"] = query_crux(target_url, origin, "DESKTOP", key)
    else:
        print("CRUX_API_KEY not set — field section will be empty.", file=sys.stderr)

    def field_verdict(ff):
        vals = field[ff]
        if not vals:
            return "unknown"
        # CWV pass = LCP, INP, CLS all green (Google's assessment uses these three)
        core = [vals.get(m, {}).get("zone") for m in ("LCP", "INP", "CLS")]
        if any(z in (None, "unknown") for z in core):
            return "unknown"
        return "pass" if all(z == "green" for z in core) else "fail"

    def split_scope(ff):
        vals = dict(field[ff])
        scope = vals.pop("_scope", "none")
        return vals, scope

    m_metrics, m_scope = split_scope("mobile")
    d_metrics, d_scope = split_scope("desktop")

    payload = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "origin": origin,
        "url": target_url,
        "field": {
            "mobile":  {"metrics": m_metrics, "verdict": field_verdict("mobile"), "scope": m_scope},
            "desktop": {"metrics": d_metrics, "verdict": field_verdict("desktop"), "scope": d_scope},
            "source": "CrUX API (real users, p75, 28-day)",
        },
        "lab": {
            "mobile":  read_lab(args.mobile_budget),
            "desktop": read_lab(args.desktop_budget),
            "source": "Sitespeed 3gfast, 10-run median (this run)",
        },
    }

    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {args.out}")

if __name__ == "__main__":
    main()
