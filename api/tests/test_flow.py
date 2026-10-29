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


def test_send_real_requires_credentials(client):
    assert client.post("/messages/send", json={"template": "t", "dry_run": False}).status_code == 400
