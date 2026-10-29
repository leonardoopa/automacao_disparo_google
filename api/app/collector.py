import csv
import io
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from . import config
from .geo import grid_cells, haversine_km
from .phones import normalize_phone

log = logging.getLogger("collector")


def run_job(body, poll_s=5):
    """Cria um job no motor, espera terminar e devolve as linhas do CSV."""
    with httpx.Client(base_url=config.SCRAPER_URL, timeout=30) as c:
        r = c.post("/api/v1/jobs", json=body)
        r.raise_for_status()
        job_id = r.json()["id"]
        deadline = time.monotonic() + body["max_time"] + 120
        while time.monotonic() < deadline:
            status = c.get(f"/api/v1/jobs/{job_id}").json().get("Status")
            if status == "ok":
                rows = list(csv.DictReader(io.StringIO(c.get(f"/api/v1/jobs/{job_id}/download").text)))
                try:
                    c.delete(f"/api/v1/jobs/{job_id}")  # libera disco do motor
                except httpx.HTTPError:
                    pass
                return rows
            if status == "failed":
                raise RuntimeError(f"job {job_id} falhou no motor")
            time.sleep(poll_s)
    raise TimeoutError(f"job {job_id} passou do limite de tempo")


def _num(value, cast):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def to_lead(row, origin, radius_km):
    """Linha do CSV -> documento de lead. None se faltar id/coordenada ou estiver fora do raio."""
    place_id = row.get("place_id") or row.get("data_id")
    lat, lon = _num(row.get("latitude"), float), _num(row.get("longitude"), float)
    if not place_id or lat is None or lon is None:
        return None
    dist = haversine_km(origin[0], origin[1], lat, lon)
    if dist > radius_km:  # o motor não corta pelo raio, então filtramos aqui
        return None
    e164, mobile = normalize_phone(row.get("phone"))
    return {
        "place_id": place_id,
        "name": row.get("title"),
        "category": row.get("category"),
        "address": row.get("address"),
        "phone_raw": row.get("phone") or None,
        "phone_e164": e164,
        "is_mobile": mobile,
        "emails": [e.strip() for e in re.split(r"[;,]", row.get("emails") or "") if e.strip()],
        "website": row.get("website") or None,
        "rating": _num(row.get("review_rating"), float),
        "reviews": _num(row.get("review_count"), int),
        "lat": lat,
        "lon": lon,
        "distance_km": round(dist, 2),
        "maps_link": row.get("link") or None,
    }


def save(db, leads):
    """Upsert por place_id. Campos de consentimento só são criados na primeira vez."""
    now = datetime.now(timezone.utc)
    created = 0
    for lead in leads:
        res = db.leads.update_one(
            {"place_id": lead["place_id"]},
            {
                "$set": {**lead, "updated_at": now},
                "$setOnInsert": {
                    "first_seen": now,
                    "consent": False,
                    "consent_source": None,
                    "opt_out": False,
                    "sent_templates": [],
                },
            },
            upsert=True,
        )
        created += bool(res.upserted_id)
    return created


def collect(db, run_id, origin, radius_km, keywords, depth, grid, email):
    """Um job por célula da grade, em sequência (vários ao mesmo tempo levam bloqueio do Google)."""
    cells = grid_cells(origin[0], origin[1], radius_km, grid)
    db.runs.update_one({"_id": run_id}, {"$set": {"status": "running", "cells_total": len(cells), "cells_done": 0, "new_leads": 0}})
    try:
        for n, cell in enumerate(cells, 1):
            body = {
                "name": f"{run_id}-{n}",
                "keywords": keywords,
                "lang": "es",
                **cell,
                "fast_mode": False,
                "depth": depth,
                "email": email,
                "max_time": config.JOB_MAX_SECONDS,
            }
            leads = [x for x in (to_lead(r, origin, radius_km) for r in run_job(body)) if x]
            created = save(db, leads)
            db.runs.update_one({"_id": run_id}, {"$set": {"cells_done": n}, "$inc": {"new_leads": created}})
        db.runs.update_one({"_id": run_id}, {"$set": {"status": "done", "finished_at": datetime.now(timezone.utc)}})
    except Exception as e:  # noqa: BLE001 - qualquer falha precisa fechar o run
        log.exception("coleta %s falhou", run_id)
        db.runs.update_one({"_id": run_id}, {"$set": {"status": "failed", "error": str(e)[:300]}})
