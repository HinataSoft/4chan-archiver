# 4chan archiver

Archivuje vybrané 4chan thready (ručně vložené i automaticky nalezené podle
klíčových slov) a umožňuje je prohlížet offline v UI podobném 4chanu.

## Provoz

```bash
docker run --rm httpd:alpine htpasswd -nbB admin 'heslo' > nginx/htpasswd
docker compose up --build -d
```

Web běží na `http://localhost:8080/`. Data (SQLite + archiv) jsou ve volume
`archive`, struktura `archive/<board>/<thread id>/`.

### Nasazení pod prefixem cesty

Chceš-li archiv na `https://example.com/4chan/`, **nech config tak, jak je**,
a prefix odřízni na svém vnějším nginx:

```nginx
location = /4chan { return 301 /4chan/; }

location /4chan/ {
    proxy_pass http://localhost:8080/;   # koncové lomítko prefix odřízne
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Aplikace pak o prefixu vůbec neví a **přímý přístup na `IP:8080/` funguje
dál** — obojí současně.

Klient prefix zvládne sám: kořen aplikace si odvozuje z URL vlastního JS
modulu (`import.meta.url`), takže se ptá relativně a vnější nginx si prefix
odřízne zpátky. Nutný je jen ten **redirect z `/4chan` na `/4chan/`** — bez
koncového lomítka by se relativní cesty resolvovaly vůči kořeni domény a
nenačetl by se ani JS, ani CSS.

Když prefix odříznout nemůžeš (proxy není pod tvou kontrolou, nebo ho chceš
mít i uvnitř), použij `nginx/nginx-prefix.conf.example` — hlavní config se
všemi location bloky pod `/4chan/`. Pozor: ten už kořenové cesty neobsluhuje,
takže přímý přístup na `IP:8080/` s ním vrací 404.

## Vývoj bez Dockeru

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
DATA_DIR=./data-dev SERVE_STATIC=1 .venv/bin/python -m app.web
DATA_DIR=./data-dev .venv/bin/python -m app.worker
```

`SERVE_STATIC=1` nechá FastAPI servírovat `/` a `/archive/` na stejných
cestách, jaké v produkci obsluhuje nginx. Je to **jen vývojová cesta**:
`static/` záměrně není v Docker image (klient nemá build step, takže se do
nginx kontejneru bind-mountuje z repa) a v produkci statiku vždycky servíruje
nginx. Adresář se hledá relativně k `app/web.py`, takže na CWD nezáleží.

Testy: `.venv/bin/python -m pytest`. Ukázková data pro klienta:
`DATA_DIR=./data-dev python scripts/make_fixture.py`.

Na Windows nahraď `.venv/bin/...` za `.venv\Scripts\...` (plain `python` na
PATH bývá rozbitý Microsoft Store stub).

## Konfigurace

| proměnná | výchozí | význam |
|---|---|---|
| `DATA_DIR` | `/data` | kořen dat |
| `SERVE_STATIC` | `0` | FastAPI servíruje statiku a `/archive` |
| `POLL_MIN_INTERVAL` | `60` | interval pollu po změně (s) |
| `POLL_MAX_INTERVAL` | `600` | strop backoffu (s) |
| `SCAN_INTERVAL` | `300` | jak často scanner čte katalogy (s) |
| `API_RATE` | `1` | req/s na `a.4cdn.org` (nezvyšuj — pravidla API) |
| `MEDIA_RATE` | `4` | req/s na `i.4cdn.org` |
| `LOG_LEVEL` | `INFO` | |

## Jak to funguje

Worker každých 5 s: projde due pravidla (katalog boardu → shoda klíčových slov
v názvu nebo textu OP → zařazení threadu), pak due thready (`If-Modified-Since`,
merge postů do `thread.json`), pak frontu médií. Thread se polluje, dokud API
nevrátí 404; pak dostane stav `dead` a zůstává v archivu.

Post smazaný moderátorem se z archivu **nemaže** — dostane `"_deleted": true`
a v prohlížeči je označený.
