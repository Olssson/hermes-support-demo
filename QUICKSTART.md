# Demo bez skrzynki i kluczy

Python 3.11+. Z katalogu repo zainstaluj zależności: `python -m pip install -r requirements.txt` (najlepiej w wirtualnym środowisku). Następnie wklej blok:

```sh
python - <<'PY'
from pathlib import Path
import httpx
from nexo_support_mcp.config import Settings
from nexo_support_mcp.adapters.baselinker_adapter import HttpBaselinkerAdapter
from nexo_support_mcp.adapters.invoice_adapter import HttpInvoiceCorrectionAdapter
from nexo_support_mcp.adapters.email_adapter import parse_raw_email
from nexo_ops.transports import ShadowTransport, OutgoingEmail

settings = Settings(db_path=Path('unused'), baselinker_api_token='demo',
                    invoice_api_url='https://invoices.example.invalid', invoice_api_token='demo')

def order_api(request):
    assert b'method=getOrders' in request.content
    return httpx.Response(200, json={'status':'SUCCESS','orders':[{'order_id':101,'products':[]}]})

def invoice_api(request):
    assert request.method == 'POST' and request.headers['Idempotency-Key'] == 'demo-101'
    return httpx.Response(200, json={'id':'DEMO-1','number':'DEMO/1','invoice_id':'DEMO-FV'})

mail = parse_raw_email(b'From: buyer@example.invalid\r\nSubject: Zwrot zamowienia 101\r\n\r\nCzy zwrot zostal rozliczony?')
order = HttpBaselinkerAdapter(settings, transport=httpx.MockTransport(order_api)).get_order('101')
receipt = HttpInvoiceCorrectionAdapter(settings, transport=httpx.MockTransport(invoice_api)).create(
    order_id='101', sku='DEMO', correction_type='return', reason='Scenariusz testowy', idempotency_key='demo-101')
outbox = ShadowTransport()
outbox.send(OutgoingEmail(to_email='buyer@example.invalid', from_email='shop@example.invalid',
    subject='Re: ' + mail.subject, body=f'Symulacja: potwierdzenie korekty {receipt.correction_number}.',
    message_id='<demo@example.invalid>'))
assert str(order.order_id) == '101' and receipt.correction_number == 'DEMO/1'
assert len(outbox.recorded) == 1
print(mail.subject, '→ zamówienie', order.order_id, '→', outbox.recorded[0].body)
PY
```

Fikcyjny mail przechodzi przez rzeczywisty parser i adaptery. Serwisy HTTP są podstawione, odpowiedź nie opuszcza pamięci procesu. To przykład integracji komponentów, nie agent prowadzący sprawy ani test prawdziwych kont. Zobacz [konfigurację własnych narzędzi](INSTALL.md).
