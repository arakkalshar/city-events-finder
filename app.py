"""
City Events Finder - Backend
INFO 520 | Data Communications | VCU Business
Aggregates events from: Ticketmaster, PredictHQ, OpenStreetMap/Overpass
"""

import os
import hashlib
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string
import requests
from google.cloud import secretmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ─── Secret / Key Loading ─────────────────────────────────────────────────────

def get_secret(secret_id: str, fallback_env: str) -> str | None:
    """Try GCP Secret Manager first, fall back to env var."""
    project_id = os.environ.get("GCP_PROJECT_ID")
    if project_id:
        try:
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8").strip()
        except Exception as e:
            logger.warning(f"Secret Manager unavailable for {secret_id}: {e}")
    return os.environ.get(fallback_env)


TICKETMASTER_KEY = get_secret("ticketmaster-api-key", "TICKETMASTER_API_KEY") or "iRNAE4rzBRbQFlKaJgygkBnUaDADneVA"
PREDICTHQ_KEY    = get_secret("predicthq-api-key",    "PREDICTHQ_API_KEY")    or "tmrQZRpGXvjx77uISLKRpoxoGCZVuBlr2S_V2iB1"

# ─── API Fetchers ─────────────────────────────────────────────────────────────

def fetch_ticketmaster(city: str) -> list[dict]:
    """Ticketmaster Discovery API — returns normalized events."""
    if not TICKETMASTER_KEY:
        logger.warning("Ticketmaster key not set, skipping.")
        return []
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey": TICKETMASTER_KEY,
        "city": city,
        "size": 20,
        "sort": "date,asc",
    }
    try:
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        events = data.get("_embedded", {}).get("events", [])
        normalized = []
        for e in events:
            date_str = e.get("dates", {}).get("start", {}).get("dateTime", "")
            venue    = e.get("_embedded", {}).get("venues", [{}])[0]
            normalized.append({
                "id":      hashlib.md5(f"tm_{e['id']}".encode()).hexdigest(),
                "name":    e.get("name", "Unknown Event"),
                "date":    date_str,
                "venue":   venue.get("name", ""),
                "address": venue.get("address", {}).get("line1", ""),
                "city":    venue.get("city", {}).get("name", city),
                "url":     e.get("url", ""),
                "source":  "Ticketmaster",
                "image":   (e.get("images") or [{}])[0].get("url", ""),
            })
        logger.info(f"Ticketmaster: {len(normalized)} events for {city}")
        return normalized
    except Exception as ex:
        logger.error(f"Ticketmaster error: {ex}")
        return []


def fetch_predicthq(city: str) -> list[dict]:
    """PredictHQ Events API — returns normalized events."""
    if not PREDICTHQ_KEY:
        logger.warning("PredictHQ key not set, skipping.")
        return []
    url = "https://api.predicthq.com/v1/events/"
    headers = {"Authorization": f"Bearer {PREDICTHQ_KEY}"}
    params  = {
        "q":         city,
        "country":   "US",
        "limit":     20,
        "sort":      "start",
        "active.gte": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=8)
        r.raise_for_status()
        results = r.json().get("results", [])
        normalized = []
        for e in results:
            normalized.append({
                "id":      hashlib.md5(f"phq_{e['id']}".encode()).hexdigest(),
                "name":    e.get("title", "Unknown Event"),
                "date":    e.get("start", ""),
                "venue":   e.get("entities", [{}])[0].get("name", "") if e.get("entities") else "",
                "address": "",
                "city":    city,
                "url":     f"https://control.predicthq.com/events/{e['id']}",
                "source":  "PredictHQ",
                "image":   "",
            })
        logger.info(f"PredictHQ: {len(normalized)} events for {city}")
        return normalized
    except Exception as ex:
        logger.error(f"PredictHQ error: {ex}")
        return []


def fetch_overpass(city: str) -> list[dict]:
    """
    OpenStreetMap Overpass API — no key required.
    Pulls venues/event spaces tagged as amenity=events_venue in the city.
    Returns them as 'upcoming' items since OSM is venue-based (not date-based).
    """
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:15];
    area["name"="{city}"]["boundary"="administrative"]->.searchArea;
    (
      node["amenity"="events_venue"](area.searchArea);
      node["amenity"="theatre"](area.searchArea);
      node["amenity"="concert_hall"](area.searchArea);
      node["leisure"="stadium"](area.searchArea);
    );
    out body 20;
    """
    try:
        r = requests.post(url, data={"data": query}, timeout=15)
        r.raise_for_status()
        elements = r.json().get("elements", [])
        normalized = []
        for e in elements:
            tags = e.get("tags", {})
            name = tags.get("name", "Unnamed Venue")
            if not name or name == "Unnamed Venue":
                continue
            normalized.append({
                "id":      hashlib.md5(f"osm_{e['id']}".encode()).hexdigest(),
                "name":    f"Events at {name}",
                "date":    "",   # OSM venues don't have date info
                "venue":   name,
                "address": tags.get("addr:street", ""),
                "city":    city,
                "url":     tags.get("website", f"https://www.openstreetmap.org/node/{e['id']}"),
                "source":  "OpenStreetMap",
                "image":   "",
            })
        logger.info(f"OpenStreetMap: {len(normalized)} venues for {city}")
        return normalized[:10]
    except Exception as ex:
        logger.error(f"Overpass error: {ex}")
        return []


# ─── Dedup & Sort ─────────────────────────────────────────────────────────────

def dedup(events: list[dict]) -> list[dict]:
    """Remove duplicates by normalizing event names."""
    seen, unique = set(), []
    for e in events:
        key = e["name"].strip().lower()[:50]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def sort_by_date(events: list[dict]) -> list[dict]:
    """Sort events by date ascending; undated events go to the end."""
    def parse_date(d):
        if not d:
            return datetime.max.replace(tzinfo=timezone.utc)
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(d[:19], fmt[:len(d[:19])])
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return datetime.max.replace(tzinfo=timezone.utc)
    return sorted(events, key=lambda e: parse_date(e.get("date", "")))


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return "OK", 200


@app.route("/api/events")
def api_events():
    city = request.args.get("city", "").strip()
    if not city:
        return jsonify({"error": "city parameter is required"}), 400

    all_events = []
    all_events += fetch_ticketmaster(city)
    all_events += fetch_predicthq(city)
    all_events += fetch_overpass(city)

    all_events = dedup(all_events)
    all_events = sort_by_date(all_events)

    sources = list({e["source"] for e in all_events})
    return jsonify({
        "city":    city,
        "count":   len(all_events),
        "sources": sources,
        "events":  all_events,
    })


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


# ─── Frontend Template ────────────────────────────────────────────────────────

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>City Events Finder</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
    header { background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
             padding: 2.5rem 1rem; text-align: center; border-bottom: 2px solid #00b4d8; }
    header h1 { font-size: 2.2rem; color: #00b4d8; font-weight: 700; letter-spacing: 1px; }
    header p  { margin-top: .4rem; color: #94a3b8; font-size: 1rem; }
    .search-box { max-width: 600px; margin: 2rem auto; padding: 0 1rem; display: flex; gap: .6rem; }
    .search-box input { flex: 1; padding: .75rem 1.2rem; border-radius: 8px; border: 1.5px solid #334155;
                        background: #1e293b; color: #e2e8f0; font-size: 1rem; outline: none; }
    .search-box input:focus { border-color: #00b4d8; }
    .search-box button { padding: .75rem 1.5rem; background: #00b4d8; color: #0f172a;
                         border: none; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 1rem; }
    .search-box button:hover { background: #0096b7; }
    #status { text-align: center; color: #64748b; padding: .5rem; font-size: .9rem; }
    #sources { text-align: center; margin-bottom: 1rem; }
    .badge { display: inline-block; padding: .25rem .7rem; border-radius: 20px; font-size: .75rem;
             font-weight: 600; margin: 0 .2rem; }
    .badge-tm  { background: #1e3a5f; color: #60a5fa; border: 1px solid #3b82f6; }
    .badge-phq { background: #1a3325; color: #4ade80; border: 1px solid #22c55e; }
    .badge-osm { background: #3b2a1a; color: #fb923c; border: 1px solid #f97316; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1.2rem; max-width: 1100px; margin: 0 auto; padding: 0 1rem 3rem; }
    .card { background: #1e293b; border-radius: 12px; overflow: hidden;
            border: 1px solid #334155; transition: transform .2s, border-color .2s; }
    .card:hover { transform: translateY(-3px); border-color: #00b4d8; }
    .card-img { width: 100%; height: 140px; object-fit: cover; background: #0f172a; }
    .card-img-placeholder { width: 100%; height: 140px; background: linear-gradient(135deg,#1e3a5f,#0f172a);
                             display: flex; align-items: center; justify-content: center; font-size: 2.5rem; }
    .card-body { padding: 1rem; }
    .card-source { font-size: .7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; margin-bottom: .4rem; }
    .src-Ticketmaster { color: #60a5fa; }
    .src-PredictHQ    { color: #4ade80; }
    .src-OpenStreetMap{ color: #fb923c; }
    .card-title { font-size: 1rem; font-weight: 600; color: #f1f5f9; margin-bottom: .5rem; line-height: 1.4; }
    .card-meta  { font-size: .82rem; color: #64748b; margin-top: .3rem; }
    .card-meta span { margin-right: .8rem; }
    .card-link  { display: inline-block; margin-top: .8rem; font-size: .82rem; color: #00b4d8;
                  text-decoration: none; font-weight: 600; }
    .card-link:hover { text-decoration: underline; }
    .empty { text-align: center; padding: 3rem; color: #475569; }
  </style>
</head>
<body>
  <header>
    <h1>🗺️ City Events Finder</h1>
    <p>Discover events from Ticketmaster · PredictHQ · OpenStreetMap</p>
  </header>

  <div class="search-box">
    <input id="cityInput" type="text" placeholder="Enter a city (e.g. Richmond, Chicago, Austin)" />
    <button onclick="search()">Search</button>
  </div>
  <div id="status"></div>
  <div id="sources"></div>
  <div class="grid" id="results"></div>

  <script>
    const cityInput = document.getElementById('cityInput');
    cityInput.addEventListener('keydown', e => { if (e.key === 'Enter') search(); });

    function formatDate(d) {
      if (!d) return 'Date TBD';
      try { return new Date(d).toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric', year:'numeric', hour:'2-digit', minute:'2-digit'}); }
      catch { return d; }
    }

    function srcEmoji(src) {
      return src === 'Ticketmaster' ? '🎟️' : src === 'PredictHQ' ? '📍' : '🗺️';
    }

    async function search() {
      const city = cityInput.value.trim();
      if (!city) return;
      document.getElementById('status').textContent = 'Searching...';
      document.getElementById('results').innerHTML = '';
      document.getElementById('sources').innerHTML = '';
      try {
        const res = await fetch('/api/events?city=' + encodeURIComponent(city));
        const data = await res.json();
        document.getElementById('status').textContent = `Found ${data.count} events in ${data.city}`;
        document.getElementById('sources').innerHTML = (data.sources || []).map(s =>
          `<span class="badge badge-${s === 'Ticketmaster' ? 'tm' : s === 'PredictHQ' ? 'phq' : 'osm'}">${srcEmoji(s)} ${s}</span>`
        ).join('');
        if (!data.events.length) {
          document.getElementById('results').innerHTML = '<div class="empty">No events found. Try another city!</div>';
          return;
        }
        document.getElementById('results').innerHTML = data.events.map(e => `
          <div class="card">
            ${e.image
              ? `<img class="card-img" src="${e.image}" alt="${e.name}" onerror="this.style.display='none'">`
              : `<div class="card-img-placeholder">🎪</div>`}
            <div class="card-body">
              <div class="card-source src-${e.source}">${srcEmoji(e.source)} ${e.source}</div>
              <div class="card-title">${e.name}</div>
              <div class="card-meta">
                <span>📅 ${formatDate(e.date)}</span>
                ${e.venue ? `<span>📍 ${e.venue}</span>` : ''}
              </div>
              ${e.url ? `<a class="card-link" href="${e.url}" target="_blank">View Details →</a>` : ''}
            </div>
          </div>
        `).join('');
      } catch (err) {
        document.getElementById('status').textContent = 'Error: ' + err.message;
      }
    }
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
