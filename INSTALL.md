# Instalacja i podłączenie własnych narzędzi

## 1. Zainstaluj komponenty

```sh
git clone https://github.com/Olssson/hermes-support-demo.git
cd hermes-support-demo
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
umask 077
cp -n .env.example .env
```

Zainstaluj Hermes Agent według [oficjalnej instrukcji](https://hermes-agent.nousresearch.com/docs/). Dla osobnego środowiska możesz utworzyć `hermes profile create marketplace-support --no-alias --no-skills`, a potem wybrać model przez `hermes -p marketplace-support setup model`. **Ten krok nie podłącza adapterów do modelu**: repo nie zawiera jeszcze serwera MCP ani instalatora kompletnego agenta. Nie udostępniaj modelowi ogólnego dostępu do katalogu z sekretami.

## 2. Skonfiguruj integracje

- **Skrzynka:** w lokalnym `.env` ustaw `IMAP_HOST`, `IMAP_PORT`, `IMAP_USERNAME` i `IMAP_PASSWORD` według dokumentacji dostawcy. Użyj osobnej skrzynki testowej i TLS. `fetch_new` otwiera skrzynkę tylko do odczytu i używa `BODY.PEEK`, nie oznaczając wiadomości jako przeczytanych. Adapter zawiera również osobne metody modyfikacji i wysyłki; nie udostępniaj ich w profilu tylko do odczytu. Na start nie wypełniaj SMTP.
- **BaseLinker:** ustaw `BASELINKER_API_TOKEN`. Do pierwszego wdrożenia udostępnij integracji wyłącznie operacje odczytu; nie wystawiaj `set_order_status` jako narzędzia modelu.
- **Orders:** `INVOICE_API_URL` wskazuje Twój własny serwis dokumentów obsługujący `GET /corrections` i `POST /corrections`; `INVOICE_API_TOKEN` to jego klucz. To nie jest endpoint BaseLinkera. Wykonawca musi sprawdzić zakres korekty, respektować idempotencję i umożliwić odczyt istniejącego dokumentu po niepewnym wyniku.

## 3. Sprawdź konfigurację bez sieci

```sh
python - <<'PY'
from nexo_support_mcp.config import load_settings
from nexo_support_mcp.adapters.email_adapter import ImapSmtpEmailAdapter
from nexo_support_mcp.adapters.baselinker_adapter import HttpBaselinkerAdapter
from nexo_support_mcp.adapters.invoice_adapter import HttpInvoiceCorrectionAdapter
s = load_settings()
print('IMAP ustawiony:', ImapSmtpEmailAdapter(s).can_fetch)
print('SMTP ustawiony:', ImapSmtpEmailAdapter(s).can_send)
print('BaseLinker ustawiony:', HttpBaselinkerAdapter(s).configured)
print('Orders ustawiony:', HttpInvoiceCorrectionAdapter(s).configured)
PY
```

`True` oznacza obecność konfiguracji, nie poprawne logowanie. Polecenie nie pobiera poczty, nie wysyła odpowiedzi i nie wystawia dokumentów.

## Jak wygląda pełne wdrożenie

Wydzielony proces odczytuje wiadomości po IMAP i zapisuje identyfikator wątku oraz stan sprawy. Support pobiera fakty o zamówieniu, a serwis reguł sprawdza, czy wolno zlecić operację. Orders zwraca numer i identyfikator potwierdzonego dokumentu. Dopiero wtedy kolejka odpowiedzi może wysłać właściwą wiadomość. Braki lub sprzeczności trafiają do operatora; zwykłe sprawy nie wymagają akceptowania każdego maila. Do uruchomienia tej ścieżki nadal potrzeba warstwy orkiestracji, bazy spraw i ograniczonego serwera narzędzi dla Hermesa. Nie włączaj SMTP ani korekt live, dopóki nie sprawdzisz tej ścieżki na kontach testowych.
