import os

SCRAPER_URL = os.getenv("SCRAPER_URL", "http://scraper:8080")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo:27017")
MONGO_DB = os.getenv("MONGO_DB", "mercados")

ORIGIN_ADDRESS = os.getenv("ORIGIN_ADDRESS", "")
JOB_MAX_SECONDS = int(os.getenv("JOB_MAX_SECONDS", "600"))
DEFAULT_KEYWORDS = [
    k.strip()
    for k in os.getenv("KEYWORDS", "supermercado,mercado,tienda de alimentación").split(",")
    if k.strip()
]

# "cloud" = API oficial da Meta (templates); "evolution" = Evolution API (QR no app, texto livre, não oficial)
PROVIDER = os.getenv("WHATSAPP_PROVIDER", "cloud").lower()
EVO_URL = os.getenv("EVOLUTION_URL", "").rstrip("/")
EVO_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVO_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVO_WEBHOOK_TOKEN = os.getenv("EVOLUTION_WEBHOOK_TOKEN", "")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "/srv/templates")
# Teto de mensagens 1 por dia (UTC). 0 = sem teto. Na Evolution o padrão é 30 para não queimar o número.
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "30" if PROVIDER == "evolution" else "0"))

WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WA_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WA_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
SEND_DELAY_S = float(os.getenv("SEND_DELAY_S", "1.5"))
WA_PDF_PATH = os.getenv("WHATSAPP_PDF_PATH", "/srv/assets/euno_supermercados_es.pdf")
WA_PDF_CAPTION = os.getenv("WHATSAPP_PDF_CAPTION", "Aquí tiene el material de Euno. Quedo a su disposición para cualquier duda.")

# Respostas que contam como pedido de saída (comparação exata, minúsculas)
OPT_OUT_WORDS = {"baja", "stop", "no", "parar", "cancelar"}
