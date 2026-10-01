# Nepal flight tracker

Checks Google Flights for round trips to Kathmandu (KTM) and sends a push
notification to your phone when a fare drops below your target price.
It runs on GitHub Actions for free, so no computer needs to be on.

- **Full scan every 3 hours:** every departure date x every stay length (default: 21 x 11 = 231 searches per home airport)
- **Quick check every 20 minutes:** the 5 cheapest date pairs from the last full scan
- **Alert** when the cheapest fare is under `target_price` **and** cheaper than the last alert, so each alert means a new low
- **Daily summary** (low priority) with the current cheapest fare, so you know it's still running
- **Warning** if 6 runs in a row find nothing (e.g. Google blocking requests)

## Phone setup (ntfy)
1. Install **ntfy** from the Play Store.
2. Tap **+**, subscribe to your topic (the secret name stored in `NTFY_TOPIC`).
3. Set this topic's notification priority to high so urgent alerts get through Do Not Disturb.

## Changing settings (`config.json`)
Edit it on github.com (pencil icon), commit, and the next run uses the new values.

| Setting | Meaning |
|---|---|
| `origins` | Home airports, e.g. `["JFK", "EWR"]`. Each extra airport adds about 15 min per full scan |
| `depart_from` / `depart_to` | Departure window |
| `stay_min_days` / `stay_max_days` | Trip length range |
| `max_stops` | 1 = only 1-stop flights, 2 = allow 2 stops |
| `target_price` | Alert when a fare in USD drops below this |
| `alert_on_any_new_low` | `true` = also alert on every new low above the target |
| `daily_summary` / `daily_summary_hour_utc` | Daily status message (14 UTC = 10am Eastern) |
| `unpriced_retries` | Extra searches when Google lists a flight without its price (it often adds the price on a later request) |

## Manual controls
Actions tab → **Track flights** → **Run workflow** → mode `full`, `quick`, or `test`.
To pause: Actions tab → Track flights → **...** → Disable workflow.

`state.json` holds the cheapest fares found and the price history.

## Notes
- The repo must be **public** for unlimited free Actions minutes (a private repo would exceed the 2,000 free minutes a month in about a week).
- GitHub may delay scheduled runs by 5–15+ minutes when it's busy. That's normal.
- Prices come from Google Flights scraping, which is unofficial. If Google changes its page, the tracker sends a warning and needs a library update.
