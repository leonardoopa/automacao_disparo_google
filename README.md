# mercados-led

Coleta mercados num raio de 10 km de um endereço (Google Maps), guarda no MongoDB e dispara
mensagem de WhatsApp (Cloud API oficial) só para quem tem consentimento registrado.

- `scraper`: imagem `gosom/google-maps-scraper:v1.18.1` (motor, sem API key)
- `mongo`: banco NoSQL (`leads`, `runs`, `messages`)
- `api`: FastAPI (`/docs` mostra tudo)

## Subir

```bash
docker compose up -d --build
```

O endereço de origem fica em `.env` (`ORIGIN_ADDRESS`). Docs interativos: http://127.0.0.1:8000/docs

## Fluxo

1. `POST /collect` `{}` usa `ORIGIN_ADDRESS`, raio 10 km, grade 3x3. Acompanhe em `GET /collect/{run_id}`.
   Parâmetros: `radius_km`, `keywords`, `depth` (mais fundo = mais lugares e mais risco de bloqueio), `grid`, `email`.
2. `GET /leads?mobile_only=true` lista por distância. Fixo (prefixo 9) não recebe WhatsApp, é para ligar.
3. Depois de o contato aceitar (ligação, visita, e-mail), registre:
   `PATCH /leads/{place_id}` `{"consent": true, "consent_source": "ligou em 29/10 e aceitou"}`
4. `POST /messages/send` `{"template": "nome_aprovado", "lang": "es", "params": ["..."]}`.
   Começa em `dry_run: true` (só lista quem receberia). Use `"dry_run": false` para enviar de verdade.
   Cada lead recebe cada template uma vez só.
5. Respostas "baja", "stop", "no", "parar", "cancelar" chegam por `/webhook/whatsapp` e tiram o lead de qualquer envio.

## Antes de enviar de verdade

- Número de WhatsApp Business na Cloud API da Meta e template aprovado. Preencha `WHATSAPP_*` no `.env`.
- A Meta só aceita iniciar conversa com quem deu opt-in. Na Espanha, mensagem comercial sem consentimento
  também esbarra no RGPD e na LSSI. Confirme com um advogado.
- Não use bibliotecas não oficiais de WhatsApp (risco de banimento do número).
- O webhook não valida a assinatura da Meta. Antes de expor à internet, adicione `X-Hub-Signature-256`.

## Testes

```bash
pip install -r requirements.txt
cd api && pytest
```

Os testes cobrem geometria, telefone, filtro de raio, trava de consentimento, envio e opt-out, com Mongo
falso e motor simulado. O motor real (Google Maps) e a Cloud API da Meta não foram exercitados.
