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
4. Mensagem 1: `POST /messages/send` `{"template": "euno_intro_led", "lang": "es", "params": ["{name}", "{name}"]}`.
   `{name}` vira o nome do estabelecimento de cada lead. Texto do template e como cadastrar na Meta: `templates/META_euno_intro_es.md`.
   Começa em `dry_run: true` (só lista quem receberia). Use `"dry_run": false` para enviar de verdade.
   Cada número recebe cada template uma vez só, mesmo que várias lojas o compartilhem.
   Para mandar o PDF junto, sem esperar resposta: `"with_pdf": true`. Evolution: o texto sai e o PDF logo depois
   (use `"template": "euno_intro_pdf"`, cujo texto já diz que o PDF segue anexo). Cloud: o PDF vai no cabeçalho do
   template, que precisa ser criado com cabeçalho de Documento (ver `templates/META_euno_intro_es.md`).
5. Mensagem 2 (PDF) separada, só depois da resposta: `POST /messages/send-pdf`. Só vai a quem respondeu nas últimas 24 h (regra da Meta para mensagem
   fora de template). O `dry_run` (padrão) mostra a resposta de cada um; quem não quer o PDF:
   `PATCH /leads/{place_id}` `{"opt_out": true}`. O arquivo é `assets/euno_supermercados_es.pdf` (montado no container).
6. Respostas "baja", "stop", "no", "parar", "cancelar" chegam por `/webhook/whatsapp` e tiram o número de qualquer envio.

## Evolution API (alternativa à Cloud API)

`WHATSAPP_PROVIDER=evolution` no `.env`, com `EVOLUTION_URL`, `EVOLUTION_API_KEY` e `EVOLUTION_INSTANCE` (instância já
conectada por QR no WhatsApp Business do número remetente, em Aparelhos conectados). Não oficial: mensagem comercial fria
pode levar ao banimento do número. Por isso a API trava em 30 mensagens 1 por dia (`DAILY_SEND_LIMIT`) e espaça os envios.

- Mensagem 1 sem template: `POST /messages/send` `{"template": "euno_intro_led"}` lê `templates/euno_intro_led.txt`
  (ou mande `"text"` na hora). `{name}` vira o nome do estabelecimento. O nome do template só identifica a campanha.
- PDF: junto da mensagem 1 com `"with_pdf": true`, ou depois com `POST /messages/send-pdf` para quem respondeu nos
  últimos 7 dias. Se o PDF falhar depois de o texto sair, `{"only_replied": false}` reenvia a quem ficou sem ele.
- Quem responde: com a Evolution o número fica conectado como aparelho vinculado, então as respostas aparecem no
  WhatsApp Business do seu pai e ele atende pelo celular. Na Cloud API sozinha (sem convivência) as respostas só
  chegam ao webhook: o app dele não mostra a conversa.
- Quais fixos têm WhatsApp: `POST /leads/check-whatsapp` `{"limit": 50}` pergunta à Evolution, sem enviar nada. Quem tem
  vira elegível ao envio; celular que a checagem diz não ter WhatsApp sai. Consulta em massa também chama atenção do
  WhatsApp: faça 50 por dia. `GET /leads?has_whatsapp=true` lista.
- Respostas: configure o webhook da instância para `https://<URL pública>/webhook/evolution?token=<EVOLUTION_WEBHOOK_TOKEN>`
  com o evento `MESSAGES_UPSERT`. Contas que chegam como `@lid` sem telefone são ignoradas.
- Escrito contra a documentação da Evolution v2 (`sendText`, `sendMedia`, `whatsappNumbers`). Os testes usam respostas
  simuladas: ainda não rodou contra uma instância real.

## Antes de enviar de verdade

- Número de WhatsApp Business na Cloud API da Meta e template aprovado. Preencha `WHATSAPP_*` no `.env`.
- A Meta só aceita iniciar conversa com quem deu opt-in. Na Espanha, mensagem comercial sem consentimento
  também esbarra no RGPD e na LSSI. Confirme com um advogado.
- A Cloud API é o caminho oficial. A Evolution (e qualquer biblioteca não oficial) arrisca o banimento do número.
- O webhook da Cloud API não valida a assinatura da Meta. Antes de expor à internet, adicione `X-Hub-Signature-256`.
  O webhook da Evolution exige o `?token=` da URL.

## Testes

```bash
pip install -r requirements.txt
cd api && pytest
```

Os testes cobrem geometria, telefone, filtro de raio, trava de consentimento, envio e opt-out, com Mongo
falso e motor simulado. O motor real (Google Maps) e a Cloud API da Meta não foram exercitados.
