# UK gym prices

Personal tool: https://andyhewitt35-cmyk.github.io/uk-gym-prices/

Find gyms near you (📍 Near me, or a postcode or town), cheapest monthly price first, with plans, joining fee, current offers, 24/7 and facilities. Every price shows when it was checked.

## Where the data comes from
- **Prices and offers:** each chain's own public gym pages, read once a day by GitHub Actions (`fetch_gyms.py`). It identifies itself honestly, follows robots.txt and makes about 1 request a second per site.
  - PureGym: price table and joining fee on each gym page.
  - The Gym Group: membership data on each gym page, plus offer codes and end dates.
  - JD Gyms: membership cards on each gym page.
  - Nuffield Health: plans on each gym page (most are 12-month contracts).
  - Bannatyne: only the "from" price and headline offer shown on each club page.
- **Other gyms:** OpenStreetMap, via Overpass (`leisure=fitness_centre`, plus leisure centres). These show "price on their site" with a link.
- **Not checked:**
  - Anytime Fitness: bot protection blocks US servers.
  - Everyone Active: Cloudflare block.
  - Everlast: doesn't respond.
  - David Lloyd: no prices online.
  - Better: free-text prices.
  - Snap, Places, 1Life: not checked yet.
- **Postcode lookup:** postcodes.io. **Town names:** GeoNames.

If a source fails, its last good data is kept, with its original dates and a warning on the page.

## Schedule
Daily at 04:40 UTC (05:40 BST). A keep-alive commit runs every 30 days so GitHub doesn't pause the schedule.

## Files
- `fetch_gyms.py`: builds site/gyms.json and site/osm.json.
- `site/`: the page.
- `test.js`: browser test (playwright-core).
- `publish.sh`: one-time publish.
