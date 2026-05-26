# portflow-api-python

Generate student evaluation overviews from the Portflow API.

This repository currently contains one main script:

- `portflow.py`
- `portflow_export_full.py`

It can show:

- A single student overview in the terminal
- A CSV export for all selected students
- A compact table for selected students
- A full student portfolio export to folders (evaluations + skills + evidence)

## Requirements

- Python 3.10+
- `requests` package

Install dependency:

```bash
pip install requests
```

## Configuration

All settings are read from a `.env` file in the project root. Copy `.env.example` to `.env` and fill in the values.

### `.env` variables

| Variable | Verplicht | Beschrijving |
|---|---|---|
| `PORTFLOW_BEARER_TOKEN` | ja | JWT bearer token voor de Portflow API. Zie [Capture bearer token](#capture-bearer-token). Als leeg, vraagt het script om invoer bij elke run. |
| `PORTFLOW_COACH_STUDENTS_JSON` | nee | JSON-array met eigen coachstudenten. Zie [Student JSON formaat](#student-json-formaat). Wordt gebruikt bij `--students 3`. Als leeg, valt het script terug op alle zichtbare portfolios. |
| `PORTFLOW_TRIBE_STUDENTS_JSON` | nee | JSON-array met alle studenten in je tribe (eigen studenten + collega's). Zelfde formaat als `PORTFLOW_COACH_STUDENTS_JSON`. Wordt gebruikt bij `--students 4`. |
| `PORTFLOW_SEMESTER_START` | nee | Startdatum van het huidige semester in `YYYY-MM-DD` formaat (bijv. `2026-02-12`). Overschrijft de standaardwaarde in de code. Gebruikt door `portflow_export_full.py`. |
| `PORTFLOW_SEMESTER_END` | nee | Einddatum van het huidige semester in `YYYY-MM-DD` formaat (bijv. `2026-06-30`). Overschrijft de standaardwaarde in de code. Gebruikt door `portflow_export_full.py`. |

### Student JSON formaat

`PORTFLOW_COACH_STUDENTS_JSON` en `PORTFLOW_TRIBE_STUDENTS_JSON` zijn JSON-arrays van studentobjecten:

```json
PORTFLOW_COACH_STUDENTS_JSON=[
    {"name": "Voornaam Achternaam", "start_date": "2026-02-12", "semester": "4", "tribe": "Tribe Naam", "gilde": "BE"},
    {"name": "Andere Student",      "start_date": null,         "semester": "3", "tribe": "Tribe Naam", "gilde": "FE"},
    {"separator": true},
    {"name": "Student Andere Tribe","start_date": null,         "semester": "3", "tribe": "Tribe B",    "gilde": "AI"}
]
```

Velden per student:

| Veld | Type | Beschrijving |
|---|---|---|
| `name` | string | Volledige naam zoals in Portflow (hoofdlettergevoelig). |
| `start_date` | `"YYYY-MM-DD"` of `null` | Semesterstartdatum van deze student. Overschrijft de globale semesterstart voor filterdoeleinden. Gebruik `null` als de student de globale datum volgt. |
| `semester` | string | Semesternummer voor weergave in de tabel (bijv. `"3"` of `"4"`). Puur informatief. |
| `tribe` | string | Tribenaam voor weergave in de tabel. Puur informatief. |
| `gilde` | string | Gildenaam voor weergave in de tabel (bijv. `"BE"`, `"FE"`, `"AI"`, `"UX"`). Puur informatief. |
| `separator` | `true` | Speciaal object; voegt een horizontale scheidingslijn in de tabel in. Geen andere velden nodig. |

## Run

Standaard run (interactief):

```bash
python3 portflow.py
```

Toon alle zichtbare studenten (niet alleen `PORTFLOW_COACH_STUDENTS_JSON`):

```bash
python3 portflow.py --students 1
```

Toon de coachtabel direct zonder tussenvragen:

```bash
python3 portflow.py --semester 1 --students 3 --output 3
```

Toon tribe-tabel:

```bash
python3 portflow.py --semester 1 --students 4 --output 3
```

Met anonieme namen (bijv. tijdens een presentatie):

```bash
python3 portflow.py --semester 1 --students 3 --output 3 --anoniem
```

Full collection export voor één student:

```bash
python3 portflow_export_full.py
```

Met opties:

```bash
python3 portflow_export_full.py --students-source section --section-id 72086 --output-dir ./exports
```

Studenten ophalen via shared collections:

```bash
python3 portflow_export_full.py --students-source shared
```

## CLI Options (`portflow.py`)

- `--semester {1,2,3}`
	- `1`: huidig semester (standaard)
	- `2`: alle semesters
	- `3`: sep25 t/m jan26
- `--students {1,2,3,4}`
	- `1`: alle studenten via shared collection
	- `2`: studenten via sectie (coachingsdashboard)
	- `3`: coach-studenten uit `PORTFLOW_COACH_STUDENTS_JSON` in `.env`
	- `4`: tribe-studenten uit `PORTFLOW_TRIBE_STUDENTS_JSON` in `.env`
- `--output {1,2,3}`
	- `1`: één student weergeven
	- `2`: alle studenten exporteren naar CSV
	- `3`: studenten weergeven als tabel
- `--anoniem`
	- Vervang alle studentnamen door `*******` in de uitvoer (handig bij screenshares)
- `--dump-schema`
	- Schrijf gevonden API-veldpaden/typen naar `schema_inventory.txt`
- `--debug-api`
	- Log alle API-calls en responses naar `api_debug_log.jsonl`
- `--debug-pending`
	- Log evaluatie-beslissingen naar `pending_debug.json`

## CLI Options (`portflow_export_full.py`)

- `--output-dir <pad>`
	- Basismap voor de export (standaard: huidige map `./`)
- `--token <token>`
	- Bearer token als CLI-argument (optioneel; anders via `PORTFLOW_BEARER_TOKEN` in `.env` of prompt)
- `--section-id <id>`
	- Section-id voor het ophalen van de studentenlijst via het coachingsdashboard (optioneel; overschrijft de standaard in de code)
- `--students-source {section,shared}`
	- `section` (standaard): haal studentenlijst op via sectie/coachingsdashboard
	- `shared`: haal studentenlijst op via gedeelde collecties

## Runtime Flow

Bij het starten van `portflow.py`:

1. Toont het huidige semesterbereik (uit `PORTFLOW_SEMESTER_START` / `PORTFLOW_SEMESTER_END` of de standaardwaarden in de code).
2. Haalt de studentenlijst op (coach-subset standaard, of een andere bron via `--students`).
3. Toont de studentenlijst.
4. Vraagt om de gewenste uitvoermethode (tenzij `--output` is meegegeven):
	- `1` Één student weergeven
	- `2` Alle studenten exporteren naar CSV
	- `3` Studenten weergeven als tabel
5. Toont de uitvoer en sluit af.

## Capture bearer token

Om de scripts te kunnen gebruiken heb je een bearer token nodig van de Portflow API. Het token is een JWT dat je uit de browser haalt nadat je bent ingelogd.

### Stap 1 – Token ophalen uit de browser

1. Log in op [Portflow via Canvas](https://canvas.hu.nl/courses/51659/external_tools/1134) met je HU-account.
2. Open de **Inspector** in je browser (rechtermuisknop → Inspecteren, of `F12` / `Cmd+Option+I`).
3. Ga naar het tabblad **Network** (Netwerk).
4. Filter op `dashboard` in de zoekbalk.
5. Herlaad de pagina (`F5` / `Cmd+R`).
6. Klik op het eerste `dashboard`-verzoek in de lijst.
7. Open de tab **Headers** en zoek naar `Authorization:`.
8. Kopieer de waarde achter `Bearer ` (het token zelf, zonder het woord "Bearer").

De screenshot hieronder toont hoe je het token ophaalt in Safari (het token in de screenshot is ongeldig):

![Screenshot](docs/images/portflow-dashboard-redacted.png)

### Stap 2 – Token opslaan in `.env`

Plak het gekopieerde token in je `.env` bestand:

```env
PORTFLOW_BEARER_TOKEN=eyJraWQiOi...
```

Het token verloopt na ongeveer 2 uur. Haal dan een nieuw token op via bovenstaande stappen.

Als `PORTFLOW_BEARER_TOKEN` leeg is of ontbreekt, vragen de scripts bij elke run om een token via de terminal. Je kunt het token ook meegeven als CLI-argument bij `portflow_export_full.py` via `--token <token>`.

## Output Behavior

### Full export (`portflow_export_full.py`)

Flow:

1. Script vraagt om een student te kiezen.
2. Script maakt een nieuwe map met `studentnaam + datum`.
3. Script exporteert per evaluatie map:
	 - bewijsstukken (bijlagen)
	- bewijsstukken die als `@evidence` mention in evaluatietekst staan
	- per `@evidence` mention een submap met het gelinkte bestand en alle verwijzende opmerkingen/zelfevaluaties/beoordelingen
	 - opmerkingen (feedback/comments)
	 - zelfevaluaties
	 - beoordelingen (incl. beoordelaar, datum en niveau waar beschikbaar)
4. Script maakt daarnaast 10 vaardigheidsmappen met een `overzicht.txt` per vaardigheid.
5. Script maakt in de root een samenvattend `overzicht.txt` met alle gevonden items.
6. Script maakt in de root een `index.csv` met per item: evaluatie-map, vaardigheid, code, auteur, datum, type, niveau en aantal bijlagen.

Globale structuur:

```text
/Student Naam dd-mm-yyyy/
	overzicht.txt
	index.csv
	/evaluatie dd-mm-yyyy titel-van-de-evaluatie/
		bewijsstuk 1.pdf
		opmerking 1 - auteur - dd-mm-yyyy.txt
		zelfevaluatie - auteur - dd-mm-yyyy.txt
		beoordeling - auteur - dd-mm-yyyy - niveau.txt
	/vaardigheid 1 OC Overzicht creeren/
		overzicht.txt
	...
	/vaardigheid 10 RE Reflecteren/
		overzicht.txt
```

### Single student (`1`)

- Shows per-goal evaluations in terminal.
- Includes self-evaluations.
- Self-evaluations are shown as:
	- `? (self, <date>)`

### CSV export (`2`)

- Writes `results.csv`.
- Uses selected student set (coach subset or all).

### Table (`3`)

- Prints compact table with goal abbreviations (`OC`, `KO`, etc.).
- Self-evaluations are rendered as `?`.

Example (anonymized):

```text
Naam         | OC      | KO      | JKO     | KPM     | PL      | BD      | SW      | FO      | PH      | RE
------------------------------------------------------------------------------------------------------------------
Student 01   |         |         |         |         |         |         |         | 1       | 1       | 1
Student 02   | 3       | 3       | 2       | 2       | 2       | 2       | 2       | ?       | 2       | 3
Student 03   |         |         |         |         |         | 2       | 1       |         | ?, 1    | 2
Student 04   |         |         | 2       |         | ?       | 2, 2    | 2       | 2       | 2       |
Student 05   |         |         |         |         | ?       | ?       | ?       |         | ?       | ?
Student 06   | 1       | 1       | 1, 1    | 1       | 1       | 2       | 2       | 2       | 1       | 1
Student 07   | ?, ?    | ?, ?    | ?, ?    | ?, ?    | ?, ?    | ?, 1, ? | 1, 1    | ?       | ?, ?, ? | 1
Student 08   |         |         | 3       |         | 2       | 3, 3    | 2       | ?       | ?       | 2
Student 09   | ?       | ?       | ?       |         |         | 1       | ?       | ?       | 2       |
Student 10   | ?, ?    | ?, ?    | ?, ?    | ?, ?    | 2       | 2       | 2, ?    | 2       | 2       | 1
Student 11   | 1       | 1       | 1       | 2       | ?       | 2       | ?       | ?       | ?       | ?
Student 12   | 2, ?    | 2, ?    | 2, ?    | 2, ?    | ?       | 2       | ?       | ?, 1    | 1       | 2
Student 13   | 1       | ?       |         |         | 2       | 1       | 2       |         | 1       |
Student 14   |         |         |         |         |         |         | 1       |         |         |
Student 15   |         |         |         |         |         |         |         |         |         |
Student 16   | 1       | 1       | 1       | 1, ?    | 1       | 2       | 2       |         | 2       | ?
Student 17   | ?       | ?       | ?       | ?       | ?, ?    | ?       | ?, ?    | ?, ?    | ?, ?    | ?
Student 18   | ?       | ?       | ?       | ?       | 2       | 2       | 2       | 2       | 2       |
```

### Self vs non-self precedence

For the same evaluation request/group:

- If a non-self evaluation exists (`coach`, `assessor`, etc.), only non-self is shown.
- Self-evaluation is shown only when no non-self evaluation exists for that same request/group.

## Debug Artifacts

When enabled:

- `--debug-api` -> `api_debug_log.jsonl`
	- One JSON object per request
	- Includes URL, params, status code, error, response body
- `--debug-pending` -> `pending_debug.json`
	- Include/skip decisions and rendered values per evaluation

## Security

- Do not commit live bearer tokens to version control.
- Prefer environment variables or prompt input for tokens in shared repos.
