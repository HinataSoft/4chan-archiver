# 4chan archiver — design

Datum: 2026-08-17

## 1. Účel

Služba, která trvale archivuje vybrané 4chan thready a umožňuje je prohlížet
offline v UI podobném 4chanu.

Dvě cesty, jak se thread dostane do archivu:

1. **Ručně** — uživatel vloží URL threadu do webového rozhraní.
2. **Automaticky** — scanner prochází vybrané boardy a zařazuje thready,
   jejichž subject nebo text OP postu obsahuje některé z klíčových slov.

Zařazený thread se polluje tak dlouho, dokud ho 4chan servíruje (tj. dokud
API nevrátí 404). Poté zůstává v archivu natrvalo jako `dead`.

### Non-goals

- Fulltextové vyhledávání v obsahu postů (lze doplnit později jako FTS5 tabulka
  plněná z JSON souborů — datový model to nijak neblokuje).
- Cross-thread odkazy mezi archivovanými thready.
- Export threadu do ZIP / standalone HTML.
- Víceuživatelský provoz, účty, role.
- Posílání příspěvků na 4chan. Služba je čistě read-only.

## 2. Architektura

Jeden Python image, dva entrypointy, plus nginx:

```
nginx  ──► /            → statický JS klient (/srv/static)
       ──► /archive/    → alias na data volume (JSON i média servíruje nginx přímo)
       ──► /api/        → proxy_pass na app:8000
       auth_basic na celém serveru (htpasswd)

app    ── FastAPI/uvicorn. Jen CRUD nad SQLite. Nikdy nechodí na 4chan.
worker ── asyncio smyčka: poller + scanner + stahovač médií.
           Jediná komponenta, která chodí na síť.
```

Rozdělení na dva procesy je záměrné: worker může spadnout, restartovat se nebo
být zabitý, a archiv zůstává prohlížitelný. Zároveň platí, že prohlížení
archivu nezávisí ani na `app` — obsah threadů servíruje nginx jako statické
soubory.

Komunikace mezi `app` a `worker` je výhradně přes SQLite (WAL,
`busy_timeout = 5000`). Žádný broker, žádné RPC. Ručně vložený thread worker
uvidí na nejbližším tiku smyčky (do ~5 s).

### Dev režim bez nginx

Při `SERVE_STATIC=1` `app` mountne `StaticFiles` na `/archive` a `/`, tedy na
**stejné cesty**, jaké v produkci obsluhuje nginx. Klient tak nikdy nemusí
vědět, kde běží, a nemá žádnou dev-only větev.

Vývoj = dva příkazy, bez Dockeru:

```
python -m app.web
python -m app.worker
```

Docker Compose pouští tytéž dva moduly v kontejnerech a přidává nginx. Jediná
cesta, která se v devu netestuje, je samotný nginx config.

## 3. Úložiště

```
data/
  app.db                       SQLite (WAL)
  archive/
    g/
      12345678/
        thread.json            snapshot postů + naše metadata
        1699887766543.webm     full soubor, jméno = 4chan `tim` + ext
        1699887766543s.jpg     thumbnail (vždy .jpg, sufix `s`)
```

Struktura je `<board>/<thread id>/`. Jména souborů kopírují 4chan CDN
konvenci, takže klient si cestu k médiu odvodí přímo z postu:
`/archive/{board}/{no}/{tim}{ext}`.

Původní jméno souboru se **neukládá jako jméno na disku** — původní názvy
kolidují v rámci threadu, obsahují `/`, `..` a znaky nepřijatelné pro Windows
i Linux. Cesta odvozená z čísla je bezpečná a je to jediný důvod, proč nginx
může média servírovat bez jakéhokoli sanitizačního kódu. Původní název je
dostupný v `posts[].filename` + `.ext` a používá se jen pro zobrazení
a `download` atribut.

### thread.json

Obal kolem syrové odpovědi 4chan API:

```jsonc
{
  "board": "g",
  "no": 12345678,
  "status": "live",              // live | dead
  "first_seen": "2026-08-17T09:00:00Z",
  "last_updated": "2026-08-17T11:32:10Z",
  "died_at": null,
  "posts": [ /* syrové post objekty z API, v pořadí */ ],
  "media": {
    "1699887766543": { "ext": ".webm", "file": "ok", "thumb": "ok", "bytes": 4192304 }
  }
}
```

**Posty se merguje, nikdy nepřepisují.** Když mod smaže post, 4chan ho
v dalším pollu už nevrátí; my ho v poli `posts` ponecháme a označíme
`"_deleted": true`. Bez toho by archiv tiše ztrácel právě ten obsah, kvůli
kterému se archivuje. Prefix podtržítka odděluje naše pole od 4chan schématu.

Merge algoritmus (na klíči `no`):

- post v odpovědi a ne v archivu → připojit
- post v archivu a ne v odpovědi → nastavit `_deleted: true` (pokud už není)
- post v obou → přepsat poli z odpovědi (mění se `sticky`, `closed`,
  `replies`, může přibýt `filedeleted`), `_deleted` zůstává `false`

Zápis `thread.json` je atomický: zapsat do `thread.json.tmp` a přejmenovat.

### SQLite

Provozní stav, ne obsah. Obsah postů se v DB nedrží — jediný zdroj pravdy je
`thread.json` a DB je z archivu regenerovatelná.

```sql
CREATE TABLE threads (
  id            INTEGER PRIMARY KEY,
  board         TEXT NOT NULL,
  no            INTEGER NOT NULL,
  subject       TEXT,                    -- odvozeno z OP, pro výpis
  status        TEXT NOT NULL,           -- live | dead | error
  source        TEXT NOT NULL,           -- 'manual' | 'rule:<id>'
  first_seen    TEXT NOT NULL,
  last_polled   TEXT,
  next_poll_at  TEXT NOT NULL,
  poll_interval INTEGER NOT NULL,        -- sekundy, aktuální backoff
  last_modified TEXT,                    -- hodnota pro If-Modified-Since
  post_count    INTEGER NOT NULL DEFAULT 0,
  bytes         INTEGER NOT NULL DEFAULT 0,
  fail_count    INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  died_at       TEXT,
  UNIQUE (board, no)
);
CREATE INDEX idx_threads_due ON threads (next_poll_at)
  WHERE status IN ('live', 'error');

CREATE TABLE rules (
  id           INTEGER PRIMARY KEY,
  board        TEXT NOT NULL,
  keywords     TEXT NOT NULL,            -- JSON pole stringů, OR
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL,
  last_scan_at TEXT,
  last_error   TEXT
);

CREATE TABLE media (
  thread_id  INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  tim        INTEGER NOT NULL,
  ext        TEXT NOT NULL,
  kind       TEXT NOT NULL,              -- file | thumb
  status     TEXT NOT NULL,              -- pending | ok | failed
  bytes      INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  PRIMARY KEY (thread_id, tim, kind)
);
CREATE INDEX idx_media_pending ON media (status) WHERE status = 'pending';
```

## 4. Worker

Jedna asyncio smyčka, tři úlohy nad sdílenými rate limitery:

- `a.4cdn.org` — **1 request/s** (vyžadují to API pravidla 4chanu)
- `i.4cdn.org` — vlastní, volnější limiter (výchozí 4 req/s)

Všechny JSON požadavky posílají `If-Modified-Since` z uloženého
`last_modified`.

### Poller

Vybere thready se `status IN ('live', 'error')` a `next_poll_at <= now`,
zpracuje po jednom. (`error` je jen značka pro dashboard, ne konec pollování —
takový thread se dál zkouší na maximálním intervalu a první úspěšná odpověď ho
vrátí na `live`.)

| odpověď | akce |
|---|---|
| `304` | nic; `poll_interval *= 1.5` (max 600 s) |
| `200` | merge postů, zápis `thread.json`, zařazení nových médií do `media`, `poll_interval = 60` |
| `404` | `status = 'dead'`, `died_at = now`, konec pollování |
| `5xx` / timeout | `fail_count++`, `last_error`, backoff; po 10 selháních `status = 'error'` (poll pokračuje na max intervalu) |

Adaptivní interval: **60 s** po změně, násobení 1,5× při každém 304 až do
**10 min**. Živý thread se sleduje hustě, měsíc stará mrtvola nezabírá rate
limit.

Archivované thready nepotřebují zvláštní obsluhu: 4chan je servíruje dál
i poté, co vypadnou z catalogu, a zmizí až s 404. To přesně odpovídá zadání
„stahuj, dokud nebude smazán".

### Scanner

Každých 5 min pro každé `enabled` pravidlo:

1. `GET /{board}/catalog.json` — jeden request pokrývá OP posty celého boardu.
2. Pro každý OP: spojit `sub` + `com`, **odstranit HTML tagy a rozbalit
   entity** (jinak `&gt;` a `<br>` rozbijí hledání), převést na lowercase.
3. Hledat podřetězce z `keywords` (case-insensitive, OR).
4. Shoda → INSERT do `threads` se `source = 'rule:<id>'`,
   `next_poll_at = now`. Duplicity ignoruje UNIQUE(board, no).

Pravidla jsou napevno „subject + text OP postu, substring, case-insensitive" —
bez regexu a bez konfigurovatelných polí.

### Stahovač médií

Fronta z `media WHERE status = 'pending'`. Pro každý záznam stáhne thumbnail
i full soubor z `i.4cdn.org/{board}/{tim}{ext}` resp. `{tim}s.jpg`.

- stahuje se do `{tim}{ext}.part` a přejmenovává až po dokončení — přerušený
  worker nikdy nenechá poloviční soubor, který by vypadal jako hotový
- existující soubor se přeskočí (idempotence po restartu)
- selhání → `fail_count++`, 3 pokusy, pak `status = 'failed'` (retryovatelné
  z UI)
- velikost se nijak nelimituje; stahují se všechny full soubory i thumbnaily

## 5. HTTP API

FastAPI nad SQLite. Žádný endpoint nechodí na 4chan.

| endpoint | popis |
|---|---|
| `POST /api/threads` | `{url}` → parser → zařazení. 400 na neplatné URL, 409 na duplicitu |
| `GET /api/threads` | filtry `status`, `board`, `q` (podřetězec v subjectu), stránkování |
| `DELETE /api/threads/{id}` | smaže záznam v DB i adresář `archive/{board}/{no}/` |
| `POST /api/threads/{id}/retry` | přepne `failed` média zpět na `pending` |
| `GET /api/rules` | výpis pravidel |
| `POST /api/rules` | `{board, keywords[]}` |
| `PATCH /api/rules/{id}` | změna `keywords` / `enabled` |
| `DELETE /api/rules/{id}` | smaže pravidlo (už stažené thready zůstávají) |
| `GET /api/stats` | dashboard: počty podle stavu, poslední poll, počet chyb, obsazený disk |

Parser URL je tolerantní a akceptuje:

- `https://boards.4chan.org/g/thread/12345678`
- `https://boards.4channel.org/g/thread/12345678/slug-text`
- s fragmentem `#p12345690`
- holé `g/12345678`

Obsah threadu se přes API **nechodí** — klient čte statický
`/archive/{board}/{no}/thread.json`.

## 6. Klient

Vanilla JS moduly, žádný build step. Tři stránky:

- `index.html` — dashboard (stav ze `/api/stats`), formulář pro vložení URL,
  seznam threadů s filtry a mazáním
- `rules.html` — správa pravidel
- `thread.html?b=g&no=12345678` — prohlížeč threadu

### Prohlížeč threadu

Načte `/archive/{b}/{no}/thread.json` a vykreslí UI podobné 4chanu:

- hlavička postu: subject, jméno, datum, `No.123456`
- greentext (`>`), spoilery
- **quotelinky `>>123456`** — klik odscrolluje na cíl a zvýrazní ho, hover
  ukáže plovoucí náhled citovaného postu
- **backlinky** — z quotelinků se dopočítá mapa odpovědí a pod každý post se
  doplní odkazy na posty, které ho citují
- odkazy na posty mimo tento thread se vykreslí jako neaktivní deadlinky
- obrázky: thumbnail, klik expanduje inline na originál
- **webm/mp4: klik nahradí thumbnail inline `<video controls loop>`**
- posty s `_deleted: true` mají viditelné odlišení, ne tiché zmizení
- soubor: `original name.webm (2.4 MB, 1280x720)`, HTML-escapované,
  odkaz s `download="original name.webm"`

## 7. Chyby a odolnost

- Selhání sítě jde do `fail_count` + `last_error` a prodlouží backoff; nikdy
  neshodí smyčku workeru.
- Plný disk zastaví stahování médií a vyskočí na dashboardu. Pollování JSONu
  běží dál — text je levný a je to ta hodnotnější část archivu.
- SQLite ve WAL s `busy_timeout`; zápisy jsou krátké transakce.
- Atomické zápisy: `thread.json.tmp` → rename, `{tim}{ext}.part` → rename.
  Restart workeru v libovolném okamžiku nenechá poškozený stav.

## 8. Konfigurace

Přes proměnné prostředí (výchozí hodnoty v závorce):

| proměnná | význam |
|---|---|
| `DATA_DIR` | kořen dat (`/data`) |
| `SERVE_STATIC` | `app` servíruje statiku a `/archive` (`0`) |
| `POLL_MIN_INTERVAL` | výchozí interval pollu v s (`60`) |
| `POLL_MAX_INTERVAL` | strop backoffu v s (`600`) |
| `SCAN_INTERVAL` | interval scanneru v s (`300`) |
| `API_RATE` | req/s na `a.4cdn.org` (`1`) |
| `MEDIA_RATE` | req/s na `i.4cdn.org` (`4`) |
| `LOG_LEVEL` | (`INFO`) |

## 9. Testování

pytest + pytest-asyncio proti **fake 4chan serveru** s fixture JSONy. V CI
neteče žádný síťový provoz.

Pokryté jednotky:

- merge postů: přidaný post, smazaný post (`_deleted`), změna `closed`,
  `filedeleted`
- backoff scheduleru: 304 prodlužuje, 200 resetuje, 404 ukončuje
- parser URL: tabulka platných i neplatných vstupů
- keyword matcher: HTML entity, tagy, diakritika, case-insensitivita
- stahovač médií: `.part` → rename, přeskočení existujícího, retry a `failed`
- API endpointy přes `httpx.AsyncClient`

Integrační test: vlož URL → poll → soubory na disku → fake server vrátí 404 →
thread je `dead`.
