from src.core.query_parser import parse_query
from src.core.normalizer import parse_price

def test_query():
    q=parse_query('LM358 DIP-8 10 adet')
    assert q.mpn=='LM358'; assert q.package=='DIP-8'; assert q.quantity==10

def test_price(): assert str(parse_price('1.234,56 TL'))=='1234.56'
