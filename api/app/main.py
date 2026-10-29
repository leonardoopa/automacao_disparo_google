import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import collector, config, whatsapp
from .db import db, init_db
from .geo import geocode


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="mercados-led", lifespan=lifespan)


def now():
    return datetime.now(timezone.utc)


class CollectIn(BaseModel):
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float = Field(10, gt=0, le=25)
    keywords: list[str] = Field(default_factory=lambda: list(config.DEFAULT_KEYWORDS))
    depth: int = Field(5, ge=1, le=30)
    grid: int = Field(3, ge=1, le=5)
    email: bool = False


@app.post("/collect", status_code=202)
def start_collect(body: CollectIn, background: BackgroundTasks):
    if db.runs.find_one({"status": {"$in": ["queued", "running"]}}):
        raise HTTPException(409, "já existe uma coleta em andamento")
    if body.lat is not None and body.lon is not None:
        origin = (body.lat, body.lon)
    else:
        address = body.address or config.ORIGIN_ADDRESS
        if not address:
            raise HTTPException(422, "informe lat/lon, address ou ORIGIN_ADDRESS no .env")
        try:
            origin = geocode(address)
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"geocodificação falhou: {e}")
    run_id = uuid.uuid4().hex[:12]
    db.runs.insert_one(
        {"_id": run_id, "status": "queued", "origin": list(origin), "radius_km": body.radius_km, "started_at": now()}
    )
    background.add_task(
        collector.collect, db, run_id, origin, body.radius_km, body.keywords, body.depth, body.grid, body.email
    )
    return {"run_id": run_id, "origin": origin}


@app.get("/collect/{run_id}")
def get_collect(run_id: str):
    run = db.runs.find_one({"_id": run_id})
    if not run:
        raise HTTPException(404, "run não encontrado")
    return run


@app.get("/leads")
def list_leads(
    mobile_only: bool = False,
    consent: bool | None = None,
    category: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    skip: int = Query(0, ge=0),
):
    q: dict = {}
    if mobile_only:
        q["is_mobile"] = True
    if consent is not None:
        q["consent"] = consent
    if category:
        q["category"] = {"$regex": re.escape(category), "$options": "i"}
    items = list(db.leads.find(q, {"_id": 0}).sort("distance_km", 1).skip(skip).limit(limit))
    return {"total": db.leads.count_documents(q), "items": items}


class LeadPatch(BaseModel):
    consent: bool | None = None
    consent_source: str | None = Field(None, min_length=3)
    opt_out: bool | None = None


@app.patch("/leads/{place_id}")
def patch_lead(place_id: str, body: LeadPatch):
    upd: dict = {}
    if body.consent:
        if not body.consent_source:
            raise HTTPException(422, "consent=true exige consent_source (como e quando o contato aceitou)")
        upd.update(consent=True, consent_source=body.consent_source, consent_at=now())
    elif body.consent is False:
        upd["consent"] = False
    if body.opt_out is not None:
        upd["opt_out"] = body.opt_out
    if not upd:
        raise HTTPException(422, "nada para atualizar")
    if not db.leads.update_one({"place_id": place_id}, {"$set": upd}).matched_count:
        raise HTTPException(404, "lead não encontrado")
    return {"ok": True}


class SendIn(BaseModel):
    template: str
    lang: str = "es"
    params: list[str] = []
    limit: int = Field(20, ge=1, le=100)
    dry_run: bool = True


@app.post("/messages/send")
def send_messages(body: SendIn):
    """Envia o template só para quem tem consent, não saiu da lista, tem celular e ainda não recebeu esse template."""
    if not body.dry_run and not (config.WA_TOKEN and config.WA_PHONE_NUMBER_ID):
        raise HTTPException(400, "WHATSAPP_TOKEN e WHATSAPP_PHONE_NUMBER_ID não configurados")
    q = {
        "consent": True,
        "opt_out": False,
        "is_mobile": True,
        "phone_e164": {"$ne": None},
        "sent_templates": {"$ne": body.template},
    }
    leads = list(db.leads.find(q).sort("distance_km", 1).limit(body.limit))
    done, failed = [], []
    for lead in leads:
        item = {"place_id": lead["place_id"], "name": lead["name"], "phone": lead["phone_e164"]}
        if body.dry_run:
            done.append(item)
            continue
        try:
            wamid = whatsapp.send_template(lead["phone_e164"], body.template, body.lang, body.params)
        except httpx.HTTPError as e:
            failed.append({**item, "error": str(e)[:200]})
            continue
        db.leads.update_one(
            {"_id": lead["_id"]},
            {"$addToSet": {"sent_templates": body.template}, "$set": {"last_sent_at": now(), "wamid": wamid}},
        )
        db.messages.insert_one({**item, "template": body.template, "wamid": wamid, "sent_at": now()})
        done.append(item)
        time.sleep(config.SEND_DELAY_S)
    return {"dry_run": body.dry_run, "eligible": len(leads), "sent": done, "failed": failed}


@app.get("/webhook/whatsapp")
def wa_verify(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode == "subscribe" and config.WA_VERIFY_TOKEN and token == config.WA_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(403, "token inválido")


@app.post("/webhook/whatsapp")
def wa_incoming(payload: dict = Body(...)):
    """Respostas dos contatos: marca opt_out em 'baja', 'stop', 'no', etc."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                text = ((msg.get("text") or {}).get("body") or "").strip().lower()
                fields = {"last_reply": text[:200], "last_reply_at": now()}
                if text in config.OPT_OUT_WORDS:
                    fields["opt_out"] = True
                db.leads.update_one({"phone_e164": "+" + str(msg.get("from", ""))}, {"$set": fields})
    return {"ok": True}
