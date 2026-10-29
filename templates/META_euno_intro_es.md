Nome sugerido: euno_intro_led
Categoria: Marketing
Idioma: Spanish (es)

--- Corpo (variáveis {{1}} e {{2}}; a Meta pede um exemplo para cada uma) ---
Hola, {{1}}, ¿qué tal? Soy Hugo, de Euno.

Vi {{2}} en Google y quería hacerle una pregunta rápida: ¿hoy en día todavía cambian a mano los carteles de ofertas en papel?

Trabajamos con paneles LED de alto brillo para la fachada y con pantallas digitales para frutería, carnicería y cajas. Las ofertas se cambian en minutos, sin imprenta ni rotulista, y el consumo de energía es bajo. Incluso se puede vender espacio en las pantallas a marcas proveedoras para que anuncien dentro de la tienda.

He preparado un material breve que muestra cómo funciona. ¿Puedo enviarle el PDF por aquí para que le eche un vistazo?

--- Rodapé (recomendado; a API reconhece "baja" como saída da lista) ---
Responda BAJA para no recibir más mensajes.

--- Envio ---
POST /messages/send
{"template": "euno_intro_led", "lang": "es", "params": ["{name}", "{name}"], "dry_run": true}

"{name}" vira o nome do estabelecimento de cada lead. Com os dois parâmetros iguais o texto sai
"Hola, Mercadona, ¿qué tal? ... Vi Mercadona en Google". Se preferir "Hola, equipo de Mercadona",
use "params": ["equipo de {name}", "{name}"].

--- Mensagem 2: o PDF ---
Não é template. Só sai dentro de 24 h depois que o contato responde (regra da Meta).
POST /messages/send-pdf {"dry_run": true}   lista quem respondeu e o que respondeu
POST /messages/send-pdf {"dry_run": false}  sobe assets/euno_supermercados_es.pdf (uma vez) e envia

=== Variante com o PDF junto (with_pdf) ===
Nome sugerido: euno_intro_pdf
Categoria: Marketing / Idioma: Spanish (es)
Cabeçalho: tipo Documento (a Meta pede um PDF de exemplo na criação; use assets/euno_supermercados_es.pdf)
Corpo: o mesmo de templates/euno_intro_pdf.txt, trocando cada {name} por {{1}} e {{2}} (primeiro e segundo)
Rodapé: Responda BAJA para no recibir más mensajes.

Envio:
POST /messages/send
{"template": "euno_intro_pdf", "lang": "es", "params": ["{name}", "{name}"], "with_pdf": true, "dry_run": true}
O PDF vai no cabeçalho da própria mensagem: é uma mensagem só, sem esperar resposta.
