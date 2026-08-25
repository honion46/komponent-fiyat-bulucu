import re
from decimal import Decimal, InvalidOperation

def parse_price(value):
    if value is None: return None
    s = re.sub(r"[^0-9,.\-]", "", str(value))
    if not s: return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    try: return Decimal(s)
    except InvalidOperation: return None

def clean_text(v): return re.sub(r"\s+", " ", str(v or '')).strip()
