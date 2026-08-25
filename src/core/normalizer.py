import re
from decimal import Decimal, InvalidOperation

def normalize_text(v): return re.sub(r'\s+', ' ', str(v or '')).strip()
def normalize_mpn(v): return re.sub(r'[^A-Z0-9./_-]', '', normalize_text(v).upper())
def parse_price(v):
    if v is None: return None
    s = re.sub(r'[^\d,.\-]', '', str(v))
    if not s: return None
    if ',' in s and '.' in s: s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif ',' in s: s = s.replace(',', '.')
    try: return Decimal(s)
    except InvalidOperation: return None
