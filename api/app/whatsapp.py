import base64
import os
import re
from functools import lru_cache

import httpx

from . import config


def configured():
    if config.PROVIDER == "evolution":
        return bool(config.EVO_URL and config.EVO_KEY and config.EVO_INSTANCE)
    return bool(config.WA_TOKEN and config.WA_PHONE_NUMBER_ID)


def _base():
    return f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_NUMBER_ID}"


def _headers():
    return {"Authorization": f"Bearer {config.WA_TOKEN}"}


def _send(to_e164, kind, body):
    r = httpx.post(
        f"{_base()}/messages",
        headers=_headers(),
        json={"messaging_product": "whatsapp", "to": to_e164.lstrip("+"), "type": kind, kind: body},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["messages"][0]["id"]


def send_template(to_e164, template, lang, params=(), header_document=None):
    """Envia um template aprovado pela Meta (WhatsApp Cloud API). Retorna o id da mensagem.
    header_document=(media_id, filename) manda o PDF junto, num template criado com cabeçalho do tipo Documento."""
    template_body = {"name": template, "language": {"code": lang}}
    components = []
    if header_document:
        media_id, filename = header_document
        components.append(
            {"type": "header", "parameters": [{"type": "document", "document": {"id": media_id, "filename": filename}}]}
        )
    if params:
        components.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in params]})
    if components:
        template_body["components"] = components
    return _send(to_e164, "template", template_body)


def upload_media(path, mime="application/pdf"):
    """Sobe o arquivo para a Meta e devolve o media_id (vale 30 dias)."""
    with open(path, "rb") as f:
        r = httpx.post(
            f"{_base()}/media",
            headers=_headers(),
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (os.path.basename(path), f, mime)},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()["id"]


def send_document(to_e164, media_id, filename, caption=""):
    """Envia um documento já enviado à Meta. Só funciona dentro de 24 h após a última resposta do contato."""
    doc = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption
    return _send(to_e164, "document", doc)


# --- Evolution API (v2): rotas /{recurso}/{ação}/{instância}, chave da instância no header apikey ---


def _evo(path, payload, timeout=30):
    r = httpx.post(
        f"{config.EVO_URL}/{path}/{config.EVO_INSTANCE}",
        headers={"apikey": config.EVO_KEY},
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def _digits(e164):
    return re.sub(r"\D", "", e164)


def _msg_id(data):
    return ((data.get("key") or {}).get("id") or data.get("id") or "") if isinstance(data, dict) else ""


def evo_send_text(to_e164, text):
    return _msg_id(_evo("message/sendText", {"number": _digits(to_e164), "text": text}))


@lru_cache(maxsize=2)
def _b64(path, mtime):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def evo_send_document(to_e164, path, filename, caption=""):
    payload = {
        "number": _digits(to_e164),
        "mediatype": "document",
        "mimetype": "application/pdf",
        "fileName": filename,
        "media": _b64(path, os.path.getmtime(path)),
    }
    if caption:
        payload["caption"] = caption
    return _msg_id(_evo("message/sendMedia", payload, timeout=120))


def evo_check_numbers(numbers_e164):
    """Quais números têm WhatsApp. Devolve {dígitos: bool}."""
    data = _evo("chat/whatsappNumbers", {"numbers": [_digits(n) for n in numbers_e164]})
    out = {}
    for d in data if isinstance(data, list) else []:
        digits = _digits(str(d.get("number") or str(d.get("jid", "")).split("@")[0]))
        if digits:
            out[digits] = bool(d.get("exists"))
    return out
