"""Nepal flight price tracker.

Searches Google Flights (via the fast-flights library) for round trips in a
flexible date window and sends a push notification through ntfy.sh when a
fare drops below the target price and beats the cheapest fare seen so far.

Usage:
    python tracker.py full      # scan every departure date x stay length
    python tracker.py quick     # re-check only the cheapest date pairs found so far
    python tracker.py auto      # full if the last full scan is older than full_scan_every_hours, else quick
    python tracker.py test      # send a test notification

Environment:
    NTFY_TOPIC   ntfy.sh topic to publish to (keep it secret)
    DRY_RUN=1    print notifications instead of sending them
"""

import json
import os
import random
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from selectolax.lexbor import LexborHTMLParser

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
STATE_FILE = ROOT / "state.json"

# Date pairs where a flight was still listed without a price after retries,
# mapped to the cheapest priced fare on that page (filled in by search()).
UNPRICED_PAIRS = {}


# ---------------------------------------------------------------- searching

def build_query(origin, depart, ret):
    return create_query(
        flights=[
            FlightQuery(date=depart, from_airport=origin, to_airport=CONFIG["destination"]),
            FlightQuery(date=ret, from_airport=CONFIG["destination"], to_airport=origin),
        ],
        trip="round-trip",
        passengers=Passengers(adults=CONFIG["adults"]),
        currency="USD",
        language="en-US",
        max_stops=CONFIG["max_stops"],
    )


def parse_results(html):
    """Return ([(price, airlines, route)], unpriced_count) from a results page.

    Parsed here rather than with fast_flights.parse(), which crashes when an
    itinerary has no price listed.
    """
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise RuntimeError("results script not found (blocked or page changed)")
    js = script.text()
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        return [], 0
    payload = json.loads(data)

    results, unpriced = [], 0
    for section in (payload[2], payload[3]):  # "best" and "other" flights
        if not section or not section[0]:
            continue
        for item in section[0]:
            try:
                price = item[1][0][1]
            except (IndexError, TypeError):
                unpriced += 1  # itinerary listed without a price
                continue
            flight = item[0]
            legs = flight[2]
            route = " > ".join([legs[0][3]] + [leg[6] for leg in legs])
            results.append((price, ", ".join(flight[1]), route))
    return results, unpriced


def fetch_results(q, label):
    """parse_results() for a query, retrying on errors. None if all attempts fail."""
    for attempt in range(3):
        try:
            return parse_results(fetch_flights_html(q))
        except Exception as e:
            print(f"  ! {label} attempt {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
    return None


def search(origin, depart, ret):
    """Cheapest result for one date pair, or None."""
    q = build_query(origin, depart, ret)
    label = f"{origin} {depart}->{ret}"
    fetched = fetch_results(q, label)
    if fetched is None:
        return None
    results, unpriced = fetched
    # Google sometimes lists a flight (often Air India) without its price and
    # includes the price on a later request, so ask again a couple of times.
    for _ in range(CONFIG["unpriced_retries"]):
        if not unpriced:
            break
        time.sleep(random.uniform(1.5, 3.0))
        fetched = fetch_results(q, label)
        if fetched is None:
            break
        more, unpriced = fetched
        results += more
    if unpriced:
        UNPRICED_PAIRS[(origin, depart, ret)] = min(results)[0] if results else float("inf")
    if not results:
        return None
    price, airlines, route = min(results)
    stops = route.count(">") - 1
    return {
        "price": price,
        "origin": origin,
        "depart": depart,
        "return": ret,
        "airlines": airlines,
        "route": route,
        "stops": stops,
        "url": q.url(),
    }


def all_date_pairs():
    start = date.fromisoformat(CONFIG["depart_from"])
    end = date.fromisoformat(CONFIG["depart_to"])
    pairs = []
    d = start
    while d <= end:
        for stay in range(CONFIG["stay_min_days"], CONFIG["stay_max_days"] + 1):
            pairs.append((d.isoformat(), (d + timedelta(days=stay)).isoformat()))
        d += timedelta(days=1)
    return pairs


def run_searches(jobs):
    found = []
    for i, (origin, depart, ret) in enumerate(jobs, 1):
        r = search(origin, depart, ret)
        if r:
            found.append(r)
            print(f"[{i}/{len(jobs)}] {origin} {depart}->{ret}: ${r['price']} {r['airlines']}")
        else:
            print(f"[{i}/{len(jobs)}] {origin} {depart}->{ret}: no result")
        time.sleep(random.uniform(1.5, 4.0))  # be polite, avoid getting blocked
    return found


# ---------------------------------------------------------------- state

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "best_ever": None,        # cheapest fare ever seen
        "last_alerted_price": None,
        "current_best": None,     # cheapest fare in the latest scan
        "top_pairs": [],          # cheapest date pairs from the last full scan
        "watch_pairs": [],        # pairs with flights listed without a price
        "failed_runs": 0,
        "last_summary_date": None,
        "history": [],
    }


def save_state(state):
    state["history"] = state["history"][-500:]
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


# ---------------------------------------------------------------- notifying

def notify(title, message, url=None, priority="default", tags="airplane"):
    topic = os.environ.get("NTFY_TOPIC")
    if os.environ.get("DRY_RUN") or not topic:
        print(f"\n=== NOTIFICATION ({priority}) ===\n{title}\n{message}\n{url or ''}\n")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": tags,
                 **({"Click": url} if url else {})},
    )
    urllib.request.urlopen(req, timeout=30)


def describe(r):
    stops = "nonstop" if r["stops"] == 0 else f"{r['stops']} stop{'s' if r['stops'] > 1 else ''}"
    return (f"${r['price']} round trip\n"
            f"{r['depart']} -> {r['return']}\n"
            f"{r['airlines']} ({stops}: {r['route']})\n"
            f"Tap to open in Google Flights.")


# ---------------------------------------------------------------- main

def process(found, state, full_scan):
    now = datetime.now(timezone.utc)
    if not found:
        state["failed_runs"] += 1
        if state["failed_runs"] == 6:
            notify("Flight tracker needs attention",
                   "6 runs in a row returned no results. Google may be blocking "
                   "requests or the page format changed.", priority="high", tags="warning")
        return
    state["failed_runs"] = 0

    found.sort(key=lambda r: r["price"])
    best = found[0]

    if full_scan:
        seen, top = set(), []
        for r in found:
            key = (r["origin"], r["depart"], r["return"])
            if key not in seen:
                seen.add(key)
                top.append(key)
            if len(top) >= CONFIG["quick_check_top_n"]:
                break
        state["top_pairs"] = top
        # Hidden fares: pairs where Google showed a flight without its price.
        # Quick checks re-search them so a price that appears later is caught.
        watch = sorted(UNPRICED_PAIRS, key=lambda k: UNPRICED_PAIRS[k])
        state["watch_pairs"] = [list(k) for k in watch[:CONFIG["watch_unpriced_max"]]]
        state["current_best"] = best
    elif state["current_best"] is None or best["price"] <= state["current_best"]["price"]:
        state["current_best"] = best

    state["history"].append({"time": now.isoformat(timespec="minutes"),
                             "mode": "full" if full_scan else "quick",
                             "price": best["price"], "depart": best["depart"],
                             "return": best["return"], "origin": best["origin"]})

    if state["best_ever"] is None or best["price"] < state["best_ever"]["price"]:
        state["best_ever"] = best

    # Alert: under target AND cheaper than the last price we alerted about.
    last = state["last_alerted_price"]
    under_target = best["price"] < CONFIG["target_price"]
    new_low = last is None or best["price"] < last
    if new_low and (under_target or CONFIG["alert_on_any_new_low"]):
        title = (f"CHEAP FLIGHT: ${best['price']} to Nepal!" if under_target
                 else f"New low: ${best['price']} to Nepal")
        notify(title, describe(best), best["url"],
               priority="urgent" if under_target else "default",
               tags="rotating_light,airplane" if under_target else "chart_with_downwards_trend")
        state["last_alerted_price"] = best["price"]

    # Daily "still alive" summary (also sent right after the very first scan).
    today = now.date().isoformat()
    first_ever = state["last_summary_date"] is None
    if CONFIG["daily_summary"] and (first_ever or (
            now.hour >= CONFIG["daily_summary_hour_utc"]
            and state["last_summary_date"] != today)):
        cb, be = state["current_best"], state["best_ever"]
        notify(f"Daily flight update: ${cb['price']}",
               f"Cheapest right now:\n{describe(cb)}\n\n"
               f"Lowest ever seen: ${be['price']} ({be['depart']} -> {be['return']})\n"
               f"Alert target: under ${CONFIG['target_price']}",
               cb["url"], priority="low", tags="calendar")
        state["last_summary_date"] = today


def hours_since_full_scan(state):
    fulls = [h["time"] for h in state["history"] if h["mode"] == "full"]
    if not fulls:
        return float("inf")
    last = datetime.fromisoformat(fulls[-1])
    return (datetime.now(timezone.utc) - last).total_seconds() / 3600


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode == "test":
        notify("Flight tracker test", "Notifications are working!", tags="white_check_mark")
        return

    state = load_state()
    if mode == "auto":
        mode = "full" if hours_since_full_scan(state) >= CONFIG["full_scan_every_hours"] else "quick"
    if mode == "quick" and state["top_pairs"]:
        jobs = [tuple(p) for p in state["top_pairs"]]
        jobs += [tuple(p) for p in state.get("watch_pairs", []) if tuple(p) not in jobs]
        full_scan = False
    else:
        jobs = [(o, d, r) for o in CONFIG["origins"] for d, r in all_date_pairs()]
        full_scan = True

    print(f"Mode: {'full' if full_scan else 'quick'} - {len(jobs)} searches")
    found = run_searches(jobs)
    process(found, state, full_scan)
    save_state(state)
    if state["current_best"]:
        print(f"\nCheapest now: ${state['current_best']['price']}  "
              f"Lowest ever: ${state['best_ever']['price']}")


if __name__ == "__main__":
    main()
