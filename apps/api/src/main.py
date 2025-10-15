from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os, re, urllib.parse

app = FastAPI()
CATALOG_BASE = os.getenv("CATALOG_BASE_URL", "https://evengo.space")

def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s

def build_search_link(filters: dict) -> str:
    allowed = ["guest_count", "price_per_guest_max", "price_per_guest_min", "district", "tags"]
    q = {k: v for k, v in (filters or {}).items() if k in allowed and v not in (None, "", [])}
    qs = urllib.parse.urlencode(q, doseq=True)
    return f"{CATALOG_BASE}/search" + (f"?{qs}" if qs else "")

def attach_links(venues: list[dict], filters: dict) -> dict:
    enriched = []
    for v in venues:
        slug = v.get("slug") or slugify(v.get("name",""))
        vv = dict(v)
        vv["slug"] = slug
        vv["link_to_page"] = f"{CATALOG_BASE}/v/{slug}?src=api"
        enriched.append(vv)
    return {
        "confidence": 0.9,
        "filters_used": filters or {},
        "intent": "venue_search",
        "link": build_search_link(filters or {}),
        "reply": "\n".join([f"{i+1}) {x.get('name','?')} — {x.get('district','-')} — {x.get('capacity_min','?')}–{x.get('capacity_max','?')} мест — ~{x.get('price_min','?')}–{x.get('price_max','?')} AZN/гость"
                           for i, x in enumerate(enriched)]),
        "venues": enriched
    }

def do_filter(filters: dict) -> list[dict]:
    # Твой существующий фильтр. Ниже — заглушка.
    return [
        {"id":"crystal-hall-baku-001","name":"Crystal Hall Baku","district":"Khatai","capacity_min":30,"capacity_max":250,"price_min":28,"price_max":62},
        {"id":"shah-palace-002","name":"Shah Palace","district":"Icherisheher","capacity_min":20,"capacity_max":120,"price_min":25,"price_max":55}
    ]

@app.post("/search")
async def search(req: Request):
    filters = await req.json()
    venues = do_filter(filters)
    return JSONResponse(attach_links(venues, filters))
