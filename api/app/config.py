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

WA_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WA_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WA_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
SEND_DELAY_S = float(os.getenv("SEND_DELAY_S", "1.5"))

# Respostas que contam como pedido de saída (comparação exata, minúsculas)
OPT_OUT_WORDS = {"baja", "stop", "no", "parar", "cancelar"}
