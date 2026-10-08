#!/usr/bin/env python3
"""Build site/gyms.json (chain gyms with prices/offers) and site/osm.json (other gyms, price not checked).

Priced, from each chain's own public gym pages (all allowed by robots.txt, fetched slowly, ~1 request/second per site):
  PureGym, The Gym Group, JD Gyms, Nuffield Health, Bannatyne (Bannatyne: "from" price + headline offer only).
Everything else comes from OpenStreetMap (leisure=fitness_centre, plus leisure centres) via Overpass, listed as
"price on their site". If a source fails, the last good data for it is kept with its original date.
"""
import json, re, sys, time, html, os, math, threading, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

UA = "Mozilla/5.0 (compatible; HewittGymFinder/1.0; personal gym price finder; +https://andyhewitt35-cmyk.github.io/uk-gym-prices/)"
LIVE = "https://andyhewitt35-cmyk.github.io/uk-gym-prices/"
DELAY = float(os.environ.get("GYM_DELAY", "1.0"))
LIMIT = int(os.environ.get("GYM_LIMIT", "0"))       # for testing: only N gyms per chain
ONLY = os.environ.get("GYM_ONLY", "")               # for testing: comma list of chains
NOW = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(url, timeout=40, tries=2, data=None, headers=None):
    last = None
    for i in range(tries):
        try:
            h = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
            h.update(headers or {})
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if getattr(e, "code", 0) in (403, 404, 410):
                break
            time.sleep(3 + 3 * i)
    raise last


def text_of(fragment):
    fragment = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", " ", fragment, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def money(s):
    m = re.search(r"£\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", s or "")
    return float(m.group(1).replace(",", "")) if m else None


def sitemap_locs(url):
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", get(url, timeout=60))


def crawl(urls, parse, workers=1):
    """Fetch pages politely: each worker waits DELAY between requests."""
    out, fails = [], []
    lock = threading.Lock()

    def job(chunk):
        for u in chunk:
            try:
                g = parse(get(u), u)
                if g:
                    with lock:
                        out.append(g)
            except Exception as e:
                with lock:
                    fails.append(f"{u}: {str(e)[:80]}")
            time.sleep(DELAY)

    chunks = [urls[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(job, chunks))
    return out, fails


def ld_blocks(page):
    out = []
    for blk in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            out.append(json.loads(blk, strict=False))
        except Exception:
            pass
    return out


def plan(name, monthly=None, kind="standard", term=0, jf=None, first=None, was=None, upfront=None, note=""):
    return {k: v for k, v in dict(n=name, m=monthly, k=kind, term=term, jf=jf, first=first, was=was, up=upfront, note=note).items()
            if v not in (None, "")}


# ---------------- PureGym ----------------
PG_FEATURES = ["Free Parking", "Air Con", "Free Wifi", "PTs Available", "Women Only Area", "Showers", "Lockers", "Classes", "Sauna", "Steam", "Pool", "Functional Training"]


def puregym_urls():
    locs = sitemap_locs("https://www.puregym.com/sitemap-0.xml")
    return sorted({u for u in locs if re.fullmatch(r"https://www\.puregym\.com/gyms/[a-z0-9-]+/", u)})


def parse_puregym(page, url):
    hc = next((b for b in ld_blocks(page) if isinstance(b, dict) and b.get("@type") == "HealthClub"), None)
    if not hc or not hc.get("geo"):
        return None
    ad = hc.get("address") or {}
    loc = (hc.get("location") or {}).get("address") or {}
    street = ", ".join(x for x in [loc.get("streetAddress"), ad.get("streetAddress")] if x)
    addr = ", ".join(x for x in [street, (ad.get("addressLocality") or "").title(), ad.get("postalCode")] if x)
    name = "PureGym " + re.sub(r"\s+Gym$", "", hc.get("name") or "").strip()
    # prices: compare table, first occurrence of each package
    cur, prev = {}, {}
    for m in re.finditer(r'<td[^>]*data-pkg-global-id="([A-Z_0-9]+)"[^>]*>\s*<span>\s*<span class="[^"]*previousPrice[^"]*"(?:\s+data-prev-price(?:="([^"]*)")?)?\s*>[^<]*</span>\s*<span class="[^"]*currentPrice[^"]*" data-current-price="([^"]*)"', page, re.S):
        gid = m.group(1)
        if gid in cur:
            continue
        cur[gid] = money(m.group(3))
        prev[gid] = money(m.group(2) or "")
    jfm = re.search(r'data-join-fee="([^"]+)"', page)
    jf = money(jfm.group(1)) if jfm else None
    first_month = "for your first month" in page
    names = {"OFF_PEAK_MONTHLY": ("Off-Peak", "offpeak"), "BASIC_MONTHLY": ("Core", "standard"), "PLUS_MONTHLY": ("Plus", "premium")}
    plans, offers = [], []
    for gid, price in cur.items():
        if price is None:
            continue
        nm, kind = names.get(gid, (gid.replace("_", " ").title(), "other"))
        if prev.get(gid) and prev[gid] > price:
            plans.append(plan(nm, prev[gid], kind, 0, jf, first=price if first_month else None, was=None if first_month else prev[gid]))
            offers.append({"x": f"{nm}: £{price:.2f} for your first month (then £{prev[gid]:.2f})" if first_month else f"{nm}: now £{price:.2f}, was £{prev[gid]:.2f}"})
        else:
            plans.append(plan(nm, price, kind, 0, jf))
    if not plans:
        return dict(c="puregym", n=name, a=addr, pc=ad.get("postalCode"), lat=float(hc["geo"]["latitude"]), lon=float(hc["geo"]["longitude"]),
                    url=url, t=NOW(), status="No prices shown on the gym page (may be opening soon)")
    ohs = hc.get("openingHoursSpecification") or []
    h24 = bool(ohs) and all(o.get("opens") == "00:00" and o.get("closes") in ("23:59", "24:00") and len(o.get("dayOfWeek", [])) == 7 for o in ohs)
    t = text_of(page)
    feat_seg = t[t.find("Features"):t.find("About PureGym")] if "Features" in t else ""
    fac = [f for f in PG_FEATURES if f.lower() in feat_seg.lower()]
    if jf == 0:
        offers.append({"x": "No joining fee"})
    return dict(c="puregym", n=name, a=addr, pc=ad.get("postalCode"), lat=float(hc["geo"]["latitude"]), lon=float(hc["geo"]["longitude"]),
                url=url, t=NOW(), h24=h24, fac=["No contract"] + fac, plans=plans, offers=offers)


# ---------------- The Gym Group ----------------
def tgg_urls():
    locs = sitemap_locs("https://www.thegymgroup.com/sitemap.xml")
    return sorted({u for u in locs if re.fullmatch(r"https://www\.thegymgroup\.com/find-a-gym/[a-z0-9-]+-gyms/[a-z0-9-]+/", u)})


def parse_tgg(page, url):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None
    pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    if not pp.get("latitude") or not pp.get("products"):
        return None
    ad = pp.get("address") or {}
    addr = ", ".join(x for x in [ad.get("address1"), ad.get("address2"), ad.get("city"), ad.get("postcode")] if x)
    plans = []
    kinds = {"OffPeakMonthly": ("Off-peak", "offpeak"), "StandardMonthly": ("Standard", "standard"), "UltimateMonthly": ("Ultimate", "premium")}
    offers = []
    for p in pp["products"]:
        typ = p.get("tggMembershipType") or ""
        if typ in kinds:
            nm, kind = kinds[typ]
            if p.get("isKickerPriceInForce") and p.get("kickerPriceMonthlyFee"):
                plans.append(plan(nm, p["kickerPriceMonthlyFee"], kind, 0, p.get("joiningFee"), first=p["price"]))
                dur = p.get("kickerPriceDuration") or 1
                offers.append({"x": f"{nm}: £{p['price']:.2f}/month for the first {'month' if dur == 1 else str(dur) + ' months'} (then £{p['kickerPriceMonthlyFee']:.2f})"})
            else:
                plans.append(plan(nm, p.get("price"), kind, 0, p.get("joiningFee")))
        elif typ in ("StandardFixed12", "UltimateFixed12"):
            nm = "Standard Saver 12 months" if typ.startswith("Standard") else "Ultimate Saver 12 months"
            plans.append(plan(nm, round(p["price"] / 12, 2), "annual", 12, p.get("joiningFee"), upfront=p["price"], note="paid upfront or in 3 instalments"))
    # offers and promo codes from the page content
    ends, excluded = {}, set()
    path = urllib.parse.urlparse(url).path

    def walk(o):
        if isinstance(o, dict):
            if o.get("promoCode") and isinstance(o.get("dateRange"), dict):
                if any((g or {}).get("gymPageURL") == path for g in o.get("excludedGyms") or []):
                    excluded.add(o["promoCode"])
                ends[o["promoCode"]] = o["dateRange"].get("endDate")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(pp.get("schema"))
    seen = set()

    def walk2(o):
        if isinstance(o, dict):
            if o.get("title") and ("promoCode" in o or "details" in o) and o.get("color"):
                code = o.get("promoCode")
                if code in excluded:
                    return
                key = (o["title"], code)
                dup = not code and re.search(r"1st month|first month", o["title"], re.I) and any("first month" in (x.get("x") or "") for x in offers)
                if key not in seen and not dup and not re.search(r"student", o["title"] + (o.get("details") or ""), re.I):
                    seen.add(key)
                    offers.append({k: v for k, v in dict(x=f"{o['title']}. {o.get('details') or ''}".strip(" ."), code=code, end=ends.get(code)).items() if v})
            for v in o.values():
                walk2(v)
        elif isinstance(o, list):
            for v in o:
                walk2(v)
    walk2(pp.get("schema"))
    oh = pp.get("openingHours") or {}
    h24 = bool(oh) and all((d or {}).get("openingTime") == "00:00:00" and (d or {}).get("closingTime") == "00:00:00" for d in oh.values())
    t = text_of(page[:200000])
    fac = ["No contract"] + [f for f in ["Free parking", "Free classes"] if f.lower() in t.lower()]
    g = dict(c="tgg", n="The Gym Group " + (pp.get("gymName") or ""), a=addr, pc=ad.get("postcode"), lat=float(pp["latitude"]), lon=float(pp["longitude"]),
             url=url, t=NOW(), h24=h24, fac=fac, plans=plans, offers=offers)
    if pp.get("offSale") or (pp.get("branchStatus") and pp["branchStatus"] != "Open"):
        g["status"] = f"Status on their site: {pp.get('branchStatus') or 'not on sale'}"
    return g


# ---------------- JD Gyms ----------------
def jd_urls():
    locs = sitemap_locs("https://www.jdgyms.co.uk/sitemap.xml")
    return sorted({u for u in locs if re.fullmatch(r"https://www\.jdgyms\.co\.uk/gym/[a-z0-9-]+/", u)})


def parse_jd(page, url):
    eg = next((b for b in ld_blocks(page) if isinstance(b, dict) and b.get("@type") in ("ExerciseGym", "HealthClub")), None)
    if not eg:
        return None
    ad = eg.get("address") or {}
    addr = ", ".join(x for x in [ad.get("streetAddress"), ad.get("addressLocality"), ad.get("postalCode")] if x)
    ma = re.search(r'\sid="membership-offers-panel-memberships"', page)
    mb = re.search(r'\sid="membership-offers-panel-(?!memberships)', page[ma.end():]) if ma else None
    panel = page[ma.start():ma.end() + (mb.start() if mb else 60000)] if ma else ""
    plans, offers = [], []
    for art in re.findall(r"<article\b.*?</article>", panel, re.S):
        title = text_of((re.search(r"<h3[^>]*>(.*?)</h3>", art, re.S) or [None, ""])[1])
        benefits = [text_of(x) for x in re.findall(r'<li class="shrink-0">(.*?)</li>', art, re.S)]
        t = text_of(art)
        nojf = any("No Joining Fee" in x for x in benefits)
        jf = 0.0 if nojf else money((re.search(r"[Jj]oining fee[^£]*(£[\d.]+)|(£[\d.]+)\s*joining fee", t) or [None, ""])[0])
        m14 = re.search(r"£([\d,.]+)\s*(\d+) months for the price of (\d+)", t)
        if m14 or re.search(r"annual", title, re.I):
            up = money(t)
            eq = re.search(r"Equal to £([\d.]+) per month", t)
            months = int(m14.group(2)) if m14 else 12
            plans.append(plan(title.split("(")[0].strip(), float(eq.group(1)) if eq else (round(up / months, 2) if up else None), "annual", 12, jf, upfront=up,
                              note=f"{months} months for the price of 12" if m14 and months != 12 else "paid upfront"))
            if m14 and months > 12:
                offers.append({"x": f"{title.split('(')[0].strip()}: {months} months for the price of 12 (£{up:.0f} upfront)"})
            continue
        fm = re.search(r"£([\d.]+)\s*1st month\s*Then £([\d.]+)/month", t)
        mm = re.search(r"£([\d.]+)\s*(?:/month|per month|a month)", t)
        kind = "offpeak" if re.search(r"off.?peak", title, re.I) else ("premium" if "plus" in title.lower() else "standard")
        if fm:
            plans.append(plan(title, float(fm.group(2)), kind, 0 if any("No Contract" in x for x in benefits) else None, jf, first=float(fm.group(1))))
            offers.append({"x": f"{title}: £{float(fm.group(1)):g} for the 1st month (then £{float(fm.group(2)):.2f})"})
        elif mm:
            plans.append(plan(title, float(mm.group(1)), kind, 0 if any("No Contract" in x for x in benefits) else None, jf))
    if any(p.get("jf") == 0 and p["k"] != "annual" for p in plans):
        offers.insert(0, {"x": "No joining fee"})
    ohs = eg.get("openingHoursSpecification") or []
    h24 = bool(ohs) and all(o.get("opens") == "00:00" and o.get("closes") in ("24:00", "23:59") and len(o.get("dayOfWeek", [])) == 7 for o in ohs)
    pt = text_of(page)
    fac = (["No contract"] if any(p.get("term") == 0 for p in plans) else []) + (["Classes"] if "Classes Included" in pt else []) + [f for f in ["Free parking"] if f.lower() in pt.lower()]
    return dict(c="jd", n=eg.get("name") or "JD Gyms", a=addr, pc=ad.get("postalCode"), url=url, t=NOW(), h24=h24, fac=fac, plans=plans, offers=offers,
                **({} if plans else {"status": "No prices shown on the gym page"}))


# ---------------- Nuffield Health ----------------
def nuffield_urls():
    locs = sitemap_locs("https://www.nuffieldhealth.com/sitemap_gyms.xml")
    return sorted({u for u in locs if re.fullmatch(r"https://www\.nuffieldhealth\.com/gyms/[a-z0-9-]+", u) and "/gyms-in-" not in u and not u.endswith("/gyms/")})


def parse_nuffield(page, url):
    mk = re.search(r"data-markers='\[\{\"lat\":([-\d.]+),\"lon\":([-\d.]+)", page)
    if not mk or "membership-plans-section" not in page:
        return None
    name = text_of((re.search(r'gym-hero__title__long">(.*?)</span>', page, re.S) or [None, ""])[1]) or text_of((re.search(r"<title>(.*?)</title>", page, re.S) or [None, ""])[1])
    ad = re.search(r'itemprop="address"[^>]*>(.*?)</div>', page, re.S)
    parts = [text_of(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", ad.group(1), re.S)] if ad else []
    pc = parts[-1] if parts and re.match(r"^[A-Z]{1,2}\d", parts[-1]) else None
    plans, offers = [], []
    for blk in re.split(r'<div class="membership-plans-section__plan(?:\s[^"]*)?">', page)[1:]:
        blk = blk[:6000]
        nm = text_of((re.search(r'plan__name">(.*?)</p>', blk, re.S) or [None, ""])[1])
        pr = money(text_of((re.search(r'plan__price">(.*?)</p>', blk, re.S) or [None, ""])[1]))
        if not nm or pr is None:
            continue
        was = money((re.search(r"Was\s*(£[\d.,]+)", text_of(blk)) or [None, ""])[0])
        jf = money(text_of((re.search(r'plan__joining-fee">(.*?)</p>', blk, re.S) or [None, ""])[1]))
        com = text_of((re.search(r'plan__commitment">(.*?)</p>', blk, re.S) or [None, ""])[1])
        cm = re.search(r"(\d+)\s*month", com)
        term = int(cm.group(1)) if cm else None
        if term == 1 or re.search(r"no commitment|rolling", com + " " + nm, re.I):
            term = 0
        kind = "offpeak" if re.search(r"off.?peak", nm, re.I) else "standard"
        if term == 12 and kind == "standard":
            kind = "annual"
        plans.append(plan(nm, pr, kind, term, jf, was=was if was and was > pr else None, note=com))
        if was and was > pr:
            offers.append({"x": f"{nm}: £{pr:.2f}/month, was £{was:.2f}"})
    promo = ". ".join(x.rstrip(". ") for x in [text_of((re.search(r'gym-hero__promo__date">(.*?)</p>', page, re.S) or [None, ""])[1]),
                                 text_of((re.search(r'gym-hero__promo__offer">(.*?)</p>', page, re.S) or [None, ""])[1])] if x)
    if promo:
        offers.insert(0, {"x": promo})
    sub = text_of((re.search(r'membership-plans-section__subheading">(.*?)</p>', page, re.S) or [None, ""])[1]).lower()
    fac = [f for k, f in [("pool", "Pool"), ("sauna", "Sauna"), ("steam", "Steam room"), ("classes", "Classes")] if k in sub]
    ot = text_of((re.search(r'location__opening-times[^>]*>(.*?)</p>', page, re.S) or [None, ""])[1])
    return dict(c="nuffield", n="Nuffield Health " + re.sub(r"\s*(Fitness (&|and) Wellbeing )?Gym$", "", name).strip(), a=", ".join(parts), pc=pc,
                lat=float(mk.group(1)), lon=float(mk.group(2)), url=url, t=NOW(), h24=bool(re.search(r"24 ?hours|24/7", ot)), hours=ot[:160],
                fac=fac, plans=plans, offers=offers)


# ---------------- Bannatyne ----------------
def bannatyne_urls():
    page = get("https://www.bannatyne.co.uk/health-club/all-locations")
    skip = {"all-locations", "add-ons", "membership", "why-choose-bannatyne", "bcoached", "shop"}
    slugs = sorted({s for s in re.findall(r'href="/health-club/([A-Za-z0-9-]+)"', page) if s.lower() not in skip})
    return ["https://www.bannatyne.co.uk/health-club/" + s for s in slugs]


def parse_bannatyne(page, url):
    org = next((b for b in ld_blocks(page) if isinstance(b, dict) and b.get("location")), None)
    if not org:
        return None
    loc = org["location"][0] if isinstance(org["location"], list) else org["location"]
    addr = html.unescape(loc.get("streetAddress") or "")
    pcm = re.search(r"([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\s*$", addr.upper())
    if not pcm:
        return None
    hero = html.unescape((re.search(r'data-hero_text="([^"]*)"', page) or [None, ""])[1])
    hero = re.sub(r"\s+", " ", hero).strip()
    # student-only prices/codes are dropped (as for The Gym Group) - never shown as the general "from" price
    hero = re.sub(r"\s*Student[^!]*(?:!|$)(?:\s*Use code \w+)?", "", hero, flags=re.I).strip()
    frm = re.search(r"(?<!student )memberships? from (?:as little as )?£\s*([\d.]+)\s*(?:per month|a month|pm)", hero, re.I)
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    nm = "Bannatyne " + " ".join(w.capitalize() for w in slug.replace("-", " ").split())
    addr = re.sub(r"\s+", " ", addr).strip()
    plans = [plan("Membership from", float(frm.group(1)), "standard", None, None, note="lowest price they show; plans and joining fee on their site")] if frm else []
    offer_txt = re.sub(r"\s*Memberships? from.*$", "", hero, flags=re.I).strip(" !.")
    if re.sub(r"[^A-Za-z]", "", offer_txt).isupper():
        offer_txt = offer_txt.capitalize()
        for mon in ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]:
            offer_txt = re.sub(rf"\b{mon}\b", mon.capitalize(), offer_txt)
        offer_txt = re.sub(r"([!.]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), offer_txt)
    offers = [{"x": offer_txt}] if offer_txt and re.search(r"off|free|save|£|%", offer_txt, re.I) else []
    return dict(c="bannatyne", n=nm, a=addr, pc=pcm.group(1), url=url, t=NOW(), h24=False, fac=[], plans=plans, offers=offers, partial=True)


CHAINS = {
    "puregym": (puregym_urls, parse_puregym, 2),
    "tgg": (tgg_urls, parse_tgg, 1),
    "jd": (jd_urls, parse_jd, 1),
    "nuffield": (nuffield_urls, parse_nuffield, 1),
    "bannatyne": (bannatyne_urls, parse_bannatyne, 1),
}


def geocode(gyms):
    """postcodes.io bulk lookup for gyms without coordinates."""
    need = [g for g in gyms if g.get("lat") is None and g.get("pc")]
    for i in range(0, len(need), 100):
        chunk = need[i:i + 100]
        body = json.dumps({"postcodes": [g["pc"] for g in chunk]}).encode()
        try:
            res = json.loads(get("https://api.postcodes.io/postcodes", data=body, headers={"Content-Type": "application/json"}))["result"]
        except Exception as e:
            print("  postcodes.io failed:", e, file=sys.stderr)
            continue
        for g, r in zip(chunk, res):
            x = r.get("result")
            if x:
                g["lat"], g["lon"] = x["latitude"], x["longitude"]
        time.sleep(1)


def finish(g):
    """from = cheapest regular monthly payment (any commitment); fromNC = cheapest with no contract."""
    pl = g.get("plans", [])
    monthly = [p for p in pl if p.get("m") and not p.get("up")]
    nc = [p for p in monthly if p.get("term") == 0]
    g["from"] = min(p["m"] for p in monthly) if monthly else (min(p["m"] for p in pl if p.get("m")) if any(p.get("m") for p in pl) else None)
    g["fromNC"] = min(p["m"] for p in nc) if nc else None
    g["nc"] = bool(nc)
    return g


# ---------------- OpenStreetMap ----------------
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
TILES = [(49.8, -8.7, 51.3, 1.9), (51.3, -8.7, 52.3, 1.9), (52.3, -8.7, 53.2, 1.9), (53.2, -8.7, 54.2, 1.9), (54.2, -8.7, 55.5, 1.9), (55.5, -8.7, 61.0, 1.9)]
NOT_GYM = re.compile(r"judo|karate|taekwondo|tae kwon do|jiu.?jitsu|bjj|martial|kickbox|muay|thai box|boxing|aikido|kung fu|mma\b|gymnastic|trampolin|"
                     r"yoga|pilates|dance|pole|barre|cheer|swim school|climb|bouldering|golf|tennis|squash|physio|clinic|scout|ballet", re.I)
CHAIN_PAT = [("puregym", r"pure ?gym"), ("tgg", r"^the gym( group)?\b|thegymgroup"), ("jd", r"jd gyms?"), ("nuffield", r"nuffield"), ("bannatyne", r"bannatyne"),
             ("anytime", r"anytime fitness"), ("everlast", r"everlast"), ("everyoneactive", r"everyone active"), ("davidlloyd", r"david lloyd"),
             ("snap", r"snap fitness"), ("better", r"^better\b|better gym|\(better\)"), ("places", r"places leisure|places gym"), ("1life", r"1life"),
             ("jetts", r"jetts"), ("fitness4less", r"fitness ?4 ?less"), ("energie", r"energie fitness"), ("simply", r"simply gym"), ("virgin", r"virgin active")]
PRICED = {"puregym", "tgg", "jd", "nuffield", "bannatyne"}


OSM_BUDGET = float(os.environ.get("OSM_BUDGET", "1200"))   # seconds for all Overpass work


def overpass_tile(bb):
    s, w, n, e = bb
    q = f"""[out:json][timeout:120];
(
  nwr["leisure"="fitness_centre"]["name"]({s},{w},{n},{e});
  nwr["leisure"="sports_centre"]["name"~"leisure|fitness|gym|health club|wellbeing",i]({s},{w},{n},{e});
);
out center tags;"""
    last = None
    for ep in OVERPASS:
        try:
            d = json.loads(get(ep, timeout=150, tries=1, data=urllib.parse.urlencode({"data": q}).encode()))
            if "elements" in d and not (d.get("remark") and "error" in d["remark"].lower()):
                return d["elements"], (d.get("osm3s") or {}).get("timestamp_osm_base")
            last = d.get("remark") or "no elements"
        except Exception as e:
            last = e
        print(f"  overpass {ep} {bb} failed: {str(last)[:80]}", file=sys.stderr)
    raise RuntimeError(f"all Overpass servers failed: {str(last)[:80]}")


def osm_gyms(prev_rows):
    """Returns (rows, failed_tiles, data_timestamp). A tile that fails is split in four and retried once;
    if a piece still fails, the previous rows inside it are kept."""
    t0, els, failed, stamps = time.time(), [], [], []
    keep = []

    def do(bb, depth):
        if time.time() - t0 > OSM_BUDGET:
            raise RuntimeError("time budget used up")
        try:
            e, ts = overpass_tile(bb)
            els.extend(e); stamps.append(ts or ""); time.sleep(3)
        except Exception as ex:
            if depth < 1 and time.time() - t0 < OSM_BUDGET:
                s, w, n, e = bb; mlat, mlon = (s + n) / 2, (w + e) / 2
                for sub in [(s, w, mlat, mlon), (s, mlon, mlat, e), (mlat, w, n, mlon), (mlat, mlon, n, e)]:
                    try:
                        do(sub, depth + 1)
                    except Exception as ex2:
                        fail(sub, ex2)
            else:
                raise

    def fail(bb, ex):
        failed.append(bb)
        s, w, n, e = bb
        keep.extend(r for r in prev_rows if s <= r[0] < n and w <= r[1] < e)
        print(f"  OSM tile {bb} failed ({str(ex)[:60]}); kept {sum(1 for r in prev_rows if s <= r[0] < n and w <= r[1] < e)} previous", file=sys.stderr)

    for bb in TILES:
        try:
            do(bb, 0)
        except Exception as ex:
            fail(bb, ex)
    out, seen = [], set()
    for x in els:
        t = x.get("tags") or {}
        name = (t.get("name") or "").strip()
        if not name or NOT_GYM.search(name) or t.get("access") == "private" or t.get("disused:leisure"):
            continue
        lat = x.get("lat") or (x.get("center") or {}).get("lat")
        lon = x.get("lon") or (x.get("center") or {}).get("lon")
        if lat is None:
            continue
        key = (x["type"], x["id"])
        if key in seen:
            continue
        seen.add(key)
        brand = (t.get("brand") or "") + " " + name + " " + (t.get("operator") or "")
        chain = next((k for k, p in CHAIN_PAT if re.search(p, brand.strip(), re.I)), "")
        if chain in PRICED:
            continue        # we have these from the chain's own site
        web = t.get("website") or t.get("contact:website") or t.get("url") or ""
        if web and not web.startswith("http"):
            web = "https://" + web
        addr = ", ".join(v for v in [" ".join(v for v in [t.get("addr:housenumber"), t.get("addr:street")] if v), t.get("addr:city"), t.get("addr:postcode")] if v)
        kind = "lc" if t.get("leisure") == "sports_centre" else "g"
        out.append([round(lat, 5), round(lon, 5), name[:80], web[:200], 1 if (t.get("opening_hours") or "").strip() == "24/7" else 0, addr[:120], chain, kind])
    have = {(r[0], r[1], r[2]) for r in out}
    out += [r for r in keep if (r[0], r[1], r[2]) not in have]
    return out, failed, min((x for x in stamps if x), default=None)


def previous(name):
    try:
        return json.loads(get(LIVE + name, timeout=60))
    except Exception:
        return None


def main():
    os.makedirs("site", exist_ok=True)
    prev = previous("gyms.json") or {}
    prev_by_chain = {}
    for g in prev.get("gyms", []):
        prev_by_chain.setdefault(g["c"], []).append(g)
    status, gyms = {}, []

    def run_chain(key):
        urls_fn, parse, workers = CHAINS[key]
        t0 = time.time()
        try:
            urls = urls_fn()
            if LIMIT:
                urls = urls[:LIMIT]
            got, fails = crawl(urls, parse, workers)
            old = prev_by_chain.get(key, [])
            if not got or (old and len(got) < 0.5 * len(old) and not LIMIT):
                raise RuntimeError(f"only {len(got)} gyms parsed from {len(urls)} pages ({len(fails)} errors) - keeping previous data")
            return key, got, dict(ok=True, at=NOW(), gyms=len(got), pages=len(urls), errors=len(fails), secs=round(time.time() - t0)), fails
        except Exception as e:
            old = prev_by_chain.get(key, [])
            pst = (prev.get("status") or {}).get(key, {})
            return key, old, dict(ok=False, at=pst.get("at"), gyms=len(old), error=str(e)[:200]), []

    keys = [k for k in CHAINS if not ONLY or k in ONLY.split(",")]
    osm_result = {}

    def run_osm():
        p = previous("osm.json") or {}
        prev_rows = p.get("gyms", [])
        try:
            rows, failed, ts = osm_gyms(prev_rows)
            if len(rows) < 1000 and not ONLY and len(prev_rows) > len(rows):
                raise RuntimeError(f"only {len(rows)} OSM gyms - keeping previous")
            osm_result.update(ok=not failed, items=rows, at=NOW(), osm=ts, failedTiles=len(failed),
                              error=f"{len(failed)} map areas failed - older list kept there" if failed else "")
        except Exception as e:
            osm_result.update(ok=False, items=prev_rows, at=p.get("at"), osm=p.get("osm"), error=str(e)[:200])

    with ThreadPoolExecutor(len(keys) + 1) as ex:
        fo = ex.submit(run_osm) if not os.environ.get("NO_OSM") else None
        for key, got, st, fails in ex.map(run_chain, keys):
            gyms += got
            status[key] = st
            print(f"{key}: {st}", flush=True)
            for f in fails[:5]:
                print("   ", f, file=sys.stderr)
        if fo:
            fo.result()
    geocode(gyms)
    gyms = [finish(g) for g in gyms if g.get("lat") is not None]
    if not gyms:
        sys.exit("No gym data at all - not publishing")
    with open("site/gyms.json", "w") as f:
        json.dump(dict(built=NOW(), status=status, gyms=gyms), f, separators=(",", ":"), ensure_ascii=False)
    print(f"Wrote site/gyms.json: {len(gyms)} priced-chain gyms")
    if osm_result or not os.path.exists("site/osm.json"):
        print(f"OSM: ok={osm_result.get('ok')} {len(osm_result.get('items', []))} gyms {osm_result.get('error', '')}")
        with open("site/osm.json", "w") as f:
            json.dump(dict(at=osm_result.get("at"), ok=osm_result.get("ok", False), osm=osm_result.get("osm"), failedTiles=osm_result.get("failedTiles"), error=osm_result.get("error", "not run"),
                           cols=["lat", "lon", "name", "web", "h24", "addr", "chain", "kind"], gyms=osm_result.get("items", [])),
                      f, separators=(",", ":"), ensure_ascii=False)

if __name__ == "__main__":
    main()
