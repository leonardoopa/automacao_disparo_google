import mongomock
import pytest
from fastapi.testclient import TestClient

from app import collector, main, whatsapp

ORIGIN = (41.3605573, 2.1255221)


def csv_row(pid, phone, lat="41.37", lon="2.13"):
    return {
        "title": f"Super {pid}", "phone": phone, "latitude": lat, "longitude": lon, "place_id": pid,
        "category": "Supermercado", "address": "x", "emails": "", "link": "",
        "review_rating": "4", "review_count": "1",
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "db", mongomock.MongoClient()["t"])
    rows = [csv_row("a", "612 34 56 78"), csv_row("b", "933 37 12 34"), csv_row("far", "612 34 56 79", lat="41.55")]
    monkeypatch.setattr(collector, "run_job", lambda body: rows)
    monkeypatch.setattr(main.config, "SEND_DELAY_S", 0)
    return TestClient(main.app)


def test_collect_consent_gate_and_send(client, monkeypatch):
    r = client.post("/collect", json={"lat": ORIGIN[0], "lon": ORIGIN[1], "grid": 1})
    assert r.status_code == 202
    run = client.get(f"/collect/{r.json()['run_id']}").json()
    assert run["status"] == "done" and run["new_leads"] == 2  # "far" fica fora dos 10 km

    assert client.get("/leads").json()["total"] == 2
    assert client.get("/leads", params={"mobile_only": True}).json()["total"] == 1

    # sem consent ninguém é elegível
    assert client.post("/messages/send", json={"template": "t"}).json()["eligible"] == 0

    # consent exige a origem do aceite
    assert client.patch("/leads/a", json={"consent": True}).status_code == 422
    assert client.patch("/leads/a", json={"consent": True, "consent_source": "ligou e aceitou"}).status_code == 200
    client.patch("/leads/b", json={"consent": True, "consent_source": "visita"})

    # telefone fixo (b) nunca entra no WhatsApp, mesmo com consent
    dry = client.post("/messages/send", json={"template": "t"}).json()
    assert dry["dry_run"] and [x["place_id"] for x in dry["sent"]] == ["a"]

    monkeypatch.setattr(main.config, "WA_TOKEN", "x")
    monkeypatch.setattr(main.config, "WA_PHONE_NUMBER_ID", "1")
    calls = []
    monkeypatch.setattr(whatsapp, "send_template", lambda *a: calls.append(a) or "wamid.1")
    real = {"template": "t", "dry_run": False}
    assert len(client.post("/messages/send", json=real).json()["sent"]) == 1
    assert calls[0][0] == "+34612345678"
    assert client.post("/messages/send", json=real).json()["eligible"] == 0  # não repete o mesmo template

    # "BAJA" pelo webhook tira o lead de qualquer envio futuro
    hook = {"entry": [{"changes": [{"value": {"messages": [{"from": "34612345678", "text": {"body": "BAJA"}}]}}]}]}
    assert client.post("/webhook/whatsapp", json=hook).status_code == 200
    assert client.post("/messages/send", json={"template": "t2"}).json()["eligible"] == 0


def test_shared_phone_gets_one_message_and_opt_out_blocks_number(client, monkeypatch):
    rows = [csv_row("x1", "612 00 00 01"), csv_row("x2", "612 00 00 01"), csv_row("y", "612 00 00 02")]
    monkeypatch.setattr(collector, "run_job", lambda body: rows)
    client.post("/collect", json={"lat": ORIGIN[0], "lon": ORIGIN[1], "grid": 1})
    for pid in ("x1", "x2", "y"):
        client.patch(f"/leads/{pid}", json={"consent": True, "consent_source": "visita"})

    dry = client.post("/messages/send", json={"template": "t"}).json()
    assert sorted(x["phone"] for x in dry["sent"]) == ["+34612000001", "+34612000002"]
    assert client.post("/messages/send", json={"template": "t", "limit": 2}).json()["eligible"] == 2

    monkeypatch.setattr(main.config, "WA_TOKEN", "x")
    monkeypatch.setattr(main.config, "WA_PHONE_NUMBER_ID", "1")
    calls = []
    monkeypatch.setattr(whatsapp, "send_template", lambda *a: calls.append(a[0]) or "wamid.1")
    real = {"template": "t", "dry_run": False}
    assert len(client.post("/messages/send", json=real).json()["sent"]) == 2
    assert sorted(calls) == ["+34612000001", "+34612000002"]
    assert client.post("/messages/send", json=real).json()["eligible"] == 0  # x2 também conta como enviado

    # "BAJA" vindo do número compartilhado bloqueia as duas lojas
    hook = {"entry": [{"changes": [{"value": {"messages": [{"from": "34612000001", "text": {"body": "baja"}}]}}]}]}
    client.post("/webhook/whatsapp", json=hook)
    shared = [l for l in client.get("/leads").json()["items"] if l["phone_e164"] == "+34612000001"]
    assert len(shared) == 2 and all(l["opt_out"] for l in shared)
    t2 = client.post("/messages/send", json={"template": "t2"}).json()
    assert [x["phone"] for x in t2["sent"]] == ["+34612000002"]


def reply(client, phone, text):
    hook = {"entry": [{"changes": [{"value": {"messages": [{"from": phone, "text": {"body": text}}]}}]}]}
    assert client.post("/webhook/whatsapp", json=hook).status_code == 200


def test_template_params_use_each_store_name(client, monkeypatch):
    rows = [{**csv_row("a", "612 34 56 78"), "title": "Super\n  Market   Ñandú"}]
    monkeypatch.setattr(collector, "run_job", lambda body: rows)
    client.post("/collect", json={"lat": ORIGIN[0], "lon": ORIGIN[1], "grid": 1})
    client.patch("/leads/a", json={"consent": True, "consent_source": "visita"})
    monkeypatch.setattr(main.config, "WA_TOKEN", "x")
    monkeypatch.setattr(main.config, "WA_PHONE_NUMBER_ID", "1")
    calls = []
    monkeypatch.setattr(whatsapp, "send_template", lambda *a: calls.append(a) or "wamid.1")
    body = {"template": "euno_intro", "params": ["{name}", "{name}"], "dry_run": False}
    assert client.post("/messages/send", json=body).json()["eligible"] == 1
    assert calls[0][3] == ["Super Market Ñandú", "Super Market Ñandú"]


def test_pdf_goes_only_to_recent_repliers_once(client, monkeypatch, tmp_path):
    pdf = tmp_path / "euno.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    rows = [csv_row("a", "612 00 00 01"), csv_row("c", "612 00 00 03"), csv_row("d", "612 00 00 04")]
    monkeypatch.setattr(collector, "run_job", lambda body: rows)
    monkeypatch.setattr(main.config, "WA_PDF_PATH", str(pdf))
    monkeypatch.setattr(main.config, "WA_TOKEN", "x")
    monkeypatch.setattr(main.config, "WA_PHONE_NUMBER_ID", "1")
    uploads, docs = [], []
    monkeypatch.setattr(whatsapp, "send_template", lambda *a: "wamid.t")
    monkeypatch.setattr(whatsapp, "upload_media", lambda path: uploads.append(path) or "media.1")
    monkeypatch.setattr(whatsapp, "send_document", lambda *a: docs.append(a) or "wamid.d")
    client.post("/collect", json={"lat": ORIGIN[0], "lon": ORIGIN[1], "grid": 1})
    for pid in "acd":
        client.patch(f"/leads/{pid}", json={"consent": True, "consent_source": "visita"})
    client.post("/messages/send", json={"template": "t", "dry_run": False})

    # ninguém respondeu: nada de PDF
    assert client.post("/messages/send-pdf", json={}).json()["eligible"] == 0

    reply(client, "34612000001", "Sí, envíemelo")
    reply(client, "34612000003", "baja")
    dry = client.post("/messages/send-pdf", json={}).json()
    assert dry["dry_run"] and [(x["name"], x["last_reply"]) for x in dry["sent"]] == [("Super a", "sí, envíemelo")]
    assert uploads == []  # dry-run não sobe nada

    real = {"dry_run": False}
    assert len(client.post("/messages/send-pdf", json=real).json()["sent"]) == 1
    assert docs[0][0] == "+34612000001" and docs[0][1] == "media.1" and docs[0][2] == "euno.pdf"
    assert client.post("/messages/send-pdf", json=real).json()["eligible"] == 0  # PDF só uma vez por número

    # segundo respondente reaproveita o media_id em vez de subir o PDF de novo
    reply(client, "34612000004", "ok, mándamelo")
    assert len(client.post("/messages/send-pdf", json=real).json()["sent"]) == 1
    assert uploads == [str(pdf)] and docs[1][1] == "media.1"


def test_pdf_missing_file_is_a_clear_error(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "WA_PDF_PATH", str(tmp_path / "nao-existe.pdf"))
    monkeypatch.setattr(main.config, "WA_TOKEN", "x")
    monkeypatch.setattr(main.config, "WA_PHONE_NUMBER_ID", "1")
    monkeypatch.setattr(collector, "run_job", lambda body: [csv_row("a", "612 00 00 01")])
    monkeypatch.setattr(whatsapp, "send_template", lambda *a: "wamid.t")
    client.post("/collect", json={"lat": ORIGIN[0], "lon": ORIGIN[1], "grid": 1})
    client.patch("/leads/a", json={"consent": True, "consent_source": "visita"})
    reply(client, "34612000001", "sí")
    r = client.post("/messages/send-pdf", json={"dry_run": False})
    assert r.status_code == 400 and "PDF não encontrado" in r.json()["detail"]


@pytest.fixture
def evo(client, monkeypatch, tmp_path):
    """Provider Evolution com 3 celulares e 1 fixo coletados; textos em tmp_path."""
    rows = [
        csv_row("a", "612 00 00 01"), csv_row("c", "612 00 00 03"), csv_row("d", "612 00 00 04"),
        csv_row("f", "933 37 12 34"),
    ]
    monkeypatch.setattr(collector, "run_job", lambda body: rows)
    for k, v in {"PROVIDER": "evolution", "EVO_URL": "http://evo", "EVO_KEY": "k", "EVO_INSTANCE": "i",
                 "EVO_WEBHOOK_TOKEN": "segredo", "TEMPLATES_DIR": str(tmp_path), "DAILY_SEND_LIMIT": 0}.items():
        monkeypatch.setattr(main.config, k, v)
    (tmp_path / "euno.txt").write_text("Hola, {name}!\nVi {name} en Google.\n", encoding="utf-8")
    client.post("/collect", json={"lat": ORIGIN[0], "lon": ORIGIN[1], "grid": 1})
    for pid in "acdf":
        client.patch(f"/leads/{pid}", json={"consent": True, "consent_source": "visita"})
    return client


def evo_hook(client, jid, text, token="segredo", from_me=False, ext=False):
    msg = {"extendedTextMessage": {"text": text}} if ext else {"conversation": text}
    body = {"event": "MESSAGES_UPSERT", "data": {"key": {"remoteJid": jid, "fromMe": from_me}, "message": msg}}
    return client.post("/webhook/evolution", params={"token": token}, json=body)


def test_evolution_text_uses_file_name_and_daily_cap(evo, monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "evo_send_text", lambda to, text: sent.append((to, text)) or "id.1")
    monkeypatch.setattr(main.config, "DAILY_SEND_LIMIT", 2)

    dry = evo.post("/messages/send", json={"template": "euno"}).json()
    assert dry["eligible"] == 2 and dry["daily_remaining"] == 2  # teto de 2 já vale no dry-run; fixo f fica de fora
    first = evo.post("/messages/send", json={"template": "euno", "dry_run": False}).json()
    assert len(first["sent"]) == 2 and first["daily_remaining"] == 0
    assert sent[0] == ("+34612000001", "Hola, Super a!\nVi Super a en Google.")
    assert evo.post("/messages/send", json={"template": "euno", "dry_run": False}).json()["eligible"] == 0  # teto

    monkeypatch.setattr(main.config, "DAILY_SEND_LIMIT", 0)
    custom = {"template": "euno", "text": "Hola {name}", "dry_run": False}
    assert len(evo.post("/messages/send", json=custom).json()["sent"]) == 1  # sobra o d
    assert sent[2] == ("+34612000004", "Hola Super d")


def test_evolution_missing_text_and_bad_template_name(evo):
    assert evo.post("/messages/send", json={"template": "nao-existe"}).status_code == 400
    assert evo.post("/messages/send", json={"template": "../etc/passwd"}).status_code == 422


def test_evolution_webhook_reply_opt_out_and_pdf(evo, monkeypatch, tmp_path):
    pdf = tmp_path / "euno.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(main.config, "WA_PDF_PATH", str(pdf))
    docs = []
    monkeypatch.setattr(whatsapp, "evo_send_text", lambda *a: "id")
    monkeypatch.setattr(whatsapp, "evo_send_document", lambda *a: docs.append(a) or "id.d")
    evo.post("/messages/send", json={"template": "euno", "dry_run": False})

    assert evo_hook(evo, "34612000001@s.whatsapp.net", "x", token="errado").status_code == 403
    evo_hook(evo, "34612000001@s.whatsapp.net", "Sí, envíamelo", ext=True)
    evo_hook(evo, "34612000003@s.whatsapp.net", "BAJA")
    evo_hook(evo, "34612000004@s.whatsapp.net", "ok", from_me=True)  # mensagem nossa: ignora
    evo_hook(evo, "999@lid", "ok")  # sem telefone: ignora

    dry = evo.post("/messages/send-pdf", json={}).json()
    assert [(x["name"], x["last_reply"]) for x in dry["sent"]] == [("Super a", "sí, envíamelo")]
    real = evo.post("/messages/send-pdf", json={"dry_run": False}).json()
    assert len(real["sent"]) == 1 and docs[0][0] == "+34612000001" and docs[0][2] == "euno.pdf"
    assert evo.post("/messages/send-pdf", json={"dry_run": False}).json()["eligible"] == 0


def test_check_whatsapp_makes_landline_eligible_and_drops_mobile_without_it(evo, monkeypatch):
    monkeypatch.setattr(whatsapp, "evo_check_numbers", lambda nums: {"34933371234": True, "34612000001": False})
    r = evo.post("/leads/check-whatsapp", json={"include_mobile": True}).json()
    assert r["on_whatsapp"] == ["+34933371234"] and r["not_on_whatsapp"] == 1 and r["no_answer"] == 2
    phones = [x["phone"] for x in evo.post("/messages/send", json={"template": "euno"}).json()["sent"]]
    assert "+34933371234" in phones and "+34612000001" not in phones
    assert evo.get("/leads", params={"has_whatsapp": True}).json()["total"] == 1
    # quem já foi checado não é consultado de novo
    assert evo.post("/leads/check-whatsapp", json={"include_mobile": True}).json()["asked"] == 2


def test_check_whatsapp_needs_evolution(client):
    assert client.post("/leads/check-whatsapp", json={}).status_code == 400


def test_evolution_client_request_shapes(monkeypatch, tmp_path):
    for k, v in {"EVO_URL": "http://evo", "EVO_KEY": "k", "EVO_INSTANCE": "inst"}.items():
        monkeypatch.setattr(whatsapp.config, k, v)
    calls = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json))
        if url.endswith("whatsappNumbers/inst"):
            return Resp([{"jid": "34933371234@s.whatsapp.net", "exists": True, "number": "34933371234"},
                         {"jid": "34911111111@s.whatsapp.net", "exists": False, "number": "34911111111"}])
        return Resp({"key": {"id": "ABC"}})

    monkeypatch.setattr(whatsapp.httpx, "post", fake_post)
    assert whatsapp.evo_send_text("+34 612 00 00 01", "oi") == "ABC"
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    whatsapp.evo_send_document("+34612000001", str(pdf), "x.pdf", "legenda")
    assert whatsapp.evo_check_numbers(["+34933371234", "+34911111111"]) == {"34933371234": True, "34911111111": False}
    assert calls[0] == ("http://evo/message/sendText/inst", {"apikey": "k"}, {"number": "34612000001", "text": "oi"})
    assert calls[1][0] == "http://evo/message/sendMedia/inst"
    assert calls[1][2] == {"number": "34612000001", "mediatype": "document", "mimetype": "application/pdf",
                           "fileName": "x.pdf", "media": "JVBERg==", "caption": "legenda"}
    assert calls[2][2] == {"numbers": ["34933371234", "34911111111"]}


def test_send_real_requires_credentials(client):
    assert client.post("/messages/send", json={"template": "t", "dry_run": False}).status_code == 400
