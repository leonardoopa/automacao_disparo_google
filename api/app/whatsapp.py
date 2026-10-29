import os

import httpx

from . import config


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


def send_template(to_e164, template, lang, params=()):
    """Envia um template aprovado pela Meta (WhatsApp Cloud API). Retorna o id da mensagem."""
    template_body = {"name": template, "language": {"code": lang}}
    if params:
        template_body["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
        ]
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
