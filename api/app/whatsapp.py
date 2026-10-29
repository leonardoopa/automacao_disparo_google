import httpx

from . import config


def send_template(to_e164, template, lang, params=()):
    """Envia um template aprovado pela Meta (WhatsApp Cloud API). Retorna o id da mensagem."""
    template_body = {"name": template, "language": {"code": lang}}
    if params:
        template_body["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
        ]
    r = httpx.post(
        f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_NUMBER_ID}/messages",
        headers={"Authorization": f"Bearer {config.WA_TOKEN}"},
        json={
            "messaging_product": "whatsapp",
            "to": to_e164.lstrip("+"),
            "type": "template",
            "template": template_body,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["messages"][0]["id"]
