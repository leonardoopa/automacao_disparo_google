import hmac
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

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
    has_whatsapp: bool | None = None,
    category: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    skip: int = Query(0, ge=0),
):
    q: dict = {}
    if mobile_only:
        q["is_mobile"] = True
    if consent is not None:
        q["consent"] = consent
    if has_whatsapp is not None:
        q["has_whatsapp"] = has_whatsapp
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
    template: str  # cloud: nome do template aprovado. evolution: nome do arquivo em templates/ (e chave de "já enviou")
    lang: str = "es"
    params: list[str] = []  # cloud. "{name}" vira o nome do estabelecimento de cada lead
    text: str | None = None  # evolution. Se vazio, lê templates/<template>.txt. "{name}" vira o nome do estabelecimento
    with_pdf: bool = False  # manda o PDF junto da mensagem 1 (evolution: logo depois do texto; cloud: no cabeçalho do template)
    limit: int = Field(20, ge=1, le=100)
    dry_run: bool = True


def clean(text):
    """Variável de template da Meta não aceita quebra de linha, tab nem espaços repetidos."""
    return " ".join(str(text).split())


def unique_by_phone(query, limit):
    """Um lead por telefone (lojas dividem número). Número de quem saiu da lista bloqueia todas as lojas dele."""
    seen = set(db.leads.distinct("phone_e164", {"opt_out": True}))
    leads = []
    if limit <= 0:
        return leads
    for lead in db.leads.find(query).sort("distance_km", 1):
        if lead["phone_e164"] in seen:
            continue
        seen.add(lead["phone_e164"])
        leads.append(lead)
        if len(leads) == limit:
            break
    return leads


def sendable_phone():
    """Celular, ou fixo que a Evolution confirmou ter WhatsApp. Quem a checagem disse que não tem fica de fora."""
    return {"$or": [{"is_mobile": True}, {"has_whatsapp": True}], "has_whatsapp": {"$ne": False}}


def require_configured():
    if not whatsapp.configured():
        need = "EVOLUTION_URL, EVOLUTION_API_KEY e EVOLUTION_INSTANCE" if config.PROVIDER == "evolution" else (
            "WHATSAPP_TOKEN e WHATSAPP_PHONE_NUMBER_ID"
        )
        raise HTTPException(400, f"{need} não configurados")


def daily_room():
    """Quantas mensagens 1 ainda cabem hoje (UTC). None = sem teto."""
    if not config.DAILY_SEND_LIMIT:
        return None
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.messages.count_documents({"type": {"$ne": "pdf"}, "sent_at": {"$gte": start}})
    return max(0, config.DAILY_SEND_LIMIT - used)


def load_text(body):
    if body.text:
        return body.text
    if not re.fullmatch(r"[\w-]+", body.template):
        raise HTTPException(422, "template inválido")
    path = os.path.join(config.TEMPLATES_DIR, f"{body.template}.txt")
    if not os.path.isfile(path):
        raise HTTPException(400, f"texto não encontrado: informe 'text' ou crie {path}")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def pause():
    time.sleep(config.SEND_DELAY_S * random.uniform(1, 2))


@app.post("/messages/send")
def send_messages(body: SendIn):
    """Mensagem 1: só para quem tem consent, não saiu da lista, tem WhatsApp e ainda não recebeu esse template."""
    evo = config.PROVIDER == "evolution"
    if not body.dry_run:
        require_configured()
    text = load_text(body) if evo else None
    media_id, filename = None, os.path.basename(config.WA_PDF_PATH)
    if body.with_pdf and not body.dry_run:
        check_pdf_file()
        if not evo:
            media_id = pdf_media_id()
    q = {
        "consent": True,
        "opt_out": False,
        "phone_e164": {"$ne": None},
        "sent_templates": {"$ne": body.template},
        **sendable_phone(),
    }
    room = daily_room()
    leads = unique_by_phone(q, body.limit if room is None else min(body.limit, room))
    done, failed = [], []
    for lead in leads:
        item = {"place_id": lead["place_id"], "name": lead["name"], "phone": lead["phone_e164"]}
        if body.dry_run:
            done.append(item)
            continue
        try:
            if evo:
                wamid = whatsapp.evo_send_text(lead["phone_e164"], text.replace("{name}", clean(lead["name"])))
            else:
                params = [clean(p.replace("{name}", lead["name"])) for p in body.params]
                header = (media_id, filename) if body.with_pdf else None
                wamid = whatsapp.send_template(lead["phone_e164"], body.template, body.lang, params, header)
        except httpx.HTTPError as e:
            failed.append({**item, "error": str(e)[:200]})
            continue
        fields = {"last_sent_at": now(), "wamid": wamid}
        if body.with_pdf and not evo:  # cloud: o PDF já foi no cabeçalho
            fields.update(pdf_sent=True, pdf_sent_at=now())
        db.leads.update_many(
            {"phone_e164": lead["phone_e164"]},
            {"$addToSet": {"sent_templates": body.template}, "$set": fields},
        )
        db.messages.insert_one({**item, "template": body.template, "wamid": wamid, "sent_at": now()})
        if body.with_pdf and evo:
            pause()
            try:
                dwamid = whatsapp.evo_send_document(lead["phone_e164"], config.WA_PDF_PATH, filename, config.WA_PDF_CAPTION)
            except httpx.HTTPError as e:
                item = {**item, "pdf": False, "pdf_error": str(e)[:200]}  # o texto já foi; /messages/send-pdf {"only_replied": false} tenta de novo
            else:
                db.leads.update_many(
                    {"phone_e164": lead["phone_e164"]}, {"$set": {"pdf_sent": True, "pdf_sent_at": now()}}
                )
                db.messages.insert_one({**item, "type": "pdf", "wamid": dwamid, "sent_at": now()})
                item = {**item, "pdf": True}
        done.append(item)
        pause()
    return {
        "dry_run": body.dry_run,
        "eligible": len(leads),
        "sent": done,
        "failed": failed,
        "daily_remaining": None if room is None else max(0, room - (0 if body.dry_run else len(done))),
    }


class SendPdfIn(BaseModel):
    only_replied: bool = True  # false: também quem recebeu a mensagem 1 e ainda não respondeu (reenvio do PDF que falhou)
    limit: int = Field(20, ge=1, le=100)
    dry_run: bool = True


def check_pdf_file():
    if not os.path.isfile(config.WA_PDF_PATH):
        raise HTTPException(400, f"PDF não encontrado em {config.WA_PDF_PATH}")


def pdf_media_id():
    """Cloud: sobe o PDF uma vez e reaproveita o media_id (a Meta guarda 30 dias; renovamos com 25)."""
    check_pdf_file()
    path = config.WA_PDF_PATH
    mtime = os.path.getmtime(path)
    cached = db.meta.find_one({"_id": "pdf_media"})
    if cached and cached["path"] == path and cached["mtime"] == mtime and time.time() - cached["uploaded"] < 25 * 86400:
        return cached["media_id"]
    try:
        media_id = whatsapp.upload_media(path)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"upload do PDF falhou: {str(e)[:200]}")
    db.meta.replace_one(
        {"_id": "pdf_media"},
        {"path": path, "mtime": mtime, "media_id": media_id, "uploaded": time.time()},
        upsert=True,
    )
    return media_id


@app.post("/messages/send-pdf")
def send_pdf(body: SendPdfIn):
    """Mensagem 2: manda o PDF a quem respondeu e ainda não o recebeu. Cloud: respostas das últimas 24 h (janela da Meta).
    Evolution: últimos 7 dias. O dry_run mostra a resposta de cada um. Quem não quer: PATCH /leads/{place_id} {"opt_out": true}."""
    evo = config.PROVIDER == "evolution"
    if not body.dry_run:
        require_configured()
    q = {
        "consent": True,
        "opt_out": False,
        "phone_e164": {"$ne": None},
        "pdf_sent": {"$ne": True},
        **sendable_phone(),
    }
    if body.only_replied:
        q["last_reply_at"] = {"$gte": datetime.now(timezone.utc) - timedelta(hours=168 if evo else 24)}
    elif evo:  # na Cloud API só a janela de 24 h permite mensagem livre
        q["last_sent_at"] = {"$exists": True}
    else:
        raise HTTPException(400, "na Cloud API o PDF só sai para quem respondeu nas últimas 24 h")
    leads = unique_by_phone(q, body.limit)
    items = [
        {"place_id": l["place_id"], "name": l["name"], "phone": l["phone_e164"], "last_reply": l.get("last_reply")}
        for l in leads
    ]
    if body.dry_run or not leads:
        return {"dry_run": body.dry_run, "eligible": len(leads), "sent": items if body.dry_run else [], "failed": []}
    if evo:
        check_pdf_file()
    else:
        media_id = pdf_media_id()
    filename = os.path.basename(config.WA_PDF_PATH)
    done, failed = [], []
    for lead, item in zip(leads, items):
        try:
            if evo:
                wamid = whatsapp.evo_send_document(lead["phone_e164"], config.WA_PDF_PATH, filename, config.WA_PDF_CAPTION)
            else:
                wamid = whatsapp.send_document(lead["phone_e164"], media_id, filename, config.WA_PDF_CAPTION)
        except httpx.HTTPError as e:
            failed.append({**item, "error": str(e)[:200]})
            continue
        db.leads.update_many({"phone_e164": lead["phone_e164"]}, {"$set": {"pdf_sent": True, "pdf_sent_at": now()}})
        db.messages.insert_one({**item, "type": "pdf", "wamid": wamid, "sent_at": now()})
        done.append(item)
        pause()
    return {"dry_run": False, "eligible": len(leads), "sent": done, "failed": failed}


class CheckIn(BaseModel):
    include_mobile: bool = False  # por padrão só os fixos
    limit: int = Field(50, ge=1, le=200)


@app.post("/leads/check-whatsapp")
def check_whatsapp(body: CheckIn):
    """Evolution: pergunta ao WhatsApp quais números existem, sem enviar nada. Quem tem WhatsApp vira elegível.
    Consultas em massa também chamam a atenção do WhatsApp: use lotes pequenos e espalhe em vários dias."""
    if config.PROVIDER != "evolution":
        raise HTTPException(400, "a checagem de números só existe com WHATSAPP_PROVIDER=evolution")
    require_configured()
    q: dict = {"phone_e164": {"$ne": None}, "wa_checked_at": {"$exists": False}}
    if not body.include_mobile:
        q["is_mobile"] = False
    phones: list[str] = []
    for lead in db.leads.find(q, {"phone_e164": 1}).sort("distance_km", 1):
        if lead["phone_e164"] not in phones:
            phones.append(lead["phone_e164"])
        if len(phones) == body.limit:
            break
    result: dict = {}
    for i in range(0, len(phones), 20):
        try:
            result.update(whatsapp.evo_check_numbers(phones[i : i + 20]))
        except httpx.HTTPError as e:
            raise HTTPException(502, f"checagem falhou: {str(e)[:200]}")
        pause()
    on, off = [], 0
    for phone in phones:
        found = result.get(phone.lstrip("+"))
        if found is None:
            continue
        db.leads.update_many({"phone_e164": phone}, {"$set": {"has_whatsapp": found, "wa_checked_at": now()}})
        if found:
            on.append(phone)
        else:
            off += 1
    return {"asked": len(phones), "on_whatsapp": on, "not_on_whatsapp": off, "no_answer": len(phones) - len(on) - off}


@app.get("/webhook/whatsapp")
def wa_verify(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode == "subscribe" and config.WA_VERIFY_TOKEN and token == config.WA_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(403, "token inválido")


def register_reply(digits, text):
    """Guarda a resposta do contato e marca opt_out em 'baja', 'stop', 'no', etc. (vale para todas as lojas do número)."""
    text = (text or "").strip().lower()
    fields = {"last_reply": text[:200], "last_reply_at": now()}
    if text in config.OPT_OUT_WORDS:
        fields["opt_out"] = True
    db.leads.update_many({"phone_e164": "+" + str(digits)}, {"$set": fields})


@app.post("/webhook/whatsapp")
def wa_incoming(payload: dict = Body(...)):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                register_reply(msg.get("from", ""), (msg.get("text") or {}).get("body"))
    return {"ok": True}


def evo_sender_digits(data):
    """Telefone de quem escreveu. Contas novas chegam como @lid (sem telefone): usa o campo alternativo, se vier."""
    key = data.get("key") or {}
    for jid in (key.get("remoteJid"), key.get("remoteJidAlt"), key.get("senderPn"), data.get("senderPn")):
        if isinstance(jid, str) and jid.endswith("@s.whatsapp.net"):
            return jid.split("@")[0]
    return None


@app.post("/webhook/evolution")
def evo_incoming(payload: dict = Body(...), token: str = Query("")):
    """Configure na instância: URL .../webhook/evolution?token=<EVOLUTION_WEBHOOK_TOKEN>, evento MESSAGES_UPSERT."""
    if not config.EVO_WEBHOOK_TOKEN or not hmac.compare_digest(token, config.EVO_WEBHOOK_TOKEN):
        raise HTTPException(403, "token inválido")
    if str(payload.get("event", "")).lower().replace("_", ".") != "messages.upsert":
        return {"ok": True}
    data = payload.get("data") or {}
    for d in data if isinstance(data, list) else [data]:
        if (d.get("key") or {}).get("fromMe"):
            continue
        digits = evo_sender_digits(d)
        if not digits:
            continue
        msg = d.get("message") or {}
        register_reply(digits, msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text"))
    return {"ok": True}
