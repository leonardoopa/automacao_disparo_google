import phonenumbers
from phonenumbers import PhoneNumberType as T


def normalize_phone(raw, region="ES"):
    """Retorna (E.164 ou None, é celular). WhatsApp só serve para celular (6xx/7xx na Espanha)."""
    if not raw:
        return None, False
    try:
        num = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None, False
    if not phonenumbers.is_valid_number(num):
        return None, False
    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    return e164, phonenumbers.number_type(num) in (T.MOBILE, T.FIXED_LINE_OR_MOBILE)
