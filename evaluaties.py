#!/usr/bin/env python3
"""evaluaties.py — Openstaande evaluatieverzoeken voor de beoordelaar.

Toont een genummerde tabel van alle evaluatieverzoeken die op beoordeling
wachten.  Via het keuzemenunummer kun je de details van één evaluatie opvragen.

Gebruikt de directe Drieam-endpoint:
  GET /api/v1/portfolios/{eigen_portfolio_id}/progress-review/invitations/received
    ?status=not_submitted&order_by=created_at&order_direction=desc

Het eigen portfolio-ID wordt opgeslagen in .env als PORTFLOW_OWN_PORTFOLIO_ID.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# .env loader  (identiek aan portflow.py)
# ---------------------------------------------------------------------------

def load_env_file(env_path: Path | None = None) -> None:
    env_file = env_path or (Path(__file__).resolve().parent / ".env")
    if not env_file.exists():
        return
    raw_lines = env_file.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in ("[", "{"):
            depth = (value.count("[") + value.count("{")
                     - value.count("]") - value.count("}"))
            while depth > 0 and i < len(raw_lines):
                chunk = raw_lines[i].strip()
                i += 1
                value += chunk
                depth += (chunk.count("[") + chunk.count("{")
                          - chunk.count("]") - chunk.count("}"))
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------

load_env_file()

BASE_URL          = "https://portfolio.drieam.app/api/v1"
PER_PAGE          = 50
BEARER_TOKEN      = os.getenv("PORTFLOW_BEARER_TOKEN", "").strip()
OWN_PORTFOLIO_ID  = os.getenv("PORTFLOW_OWN_PORTFOLIO_ID", "").strip()

_MONTH_NL = [
    "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
]

_STATUS_NL = {
    "not_started": "Niet begonnen",
    "in_progress":  "In uitvoering",
    "submitted":    "Ingediend",
    "scored":       "Beoordeeld",
    "denied":       "Geweigerd",
}

# Bekende afkortingen die studenten in titels gebruiken → canonieke code
# SM wordt soms gebruikt voor Samenwerken, RF voor Reflecteren
_ABBREV_MAP: dict[str, str] = {
    "oc":  "OC",
    "ko":  "KO",
    "jko": "JKO",
    "kpm": "KPM",
    "pl":  "PL",
    "bd":  "BD",
    "sw":  "SW",
    "sm":  "SW",
    "fo":  "FO",
    "ph":  "PH",
    "re":  "RE",
    "rf":  "RE",
}

# Bekende volledige namen (lowercase prefix volstaat) → canonieke code
_NAME_MAP: list[tuple[str, str]] = [
    ("overzicht cre",          "OC"),
    ("kritisch oordelen",      "KO"),
    ("juiste kennis",          "JKO"),
    ("kwalitatief product",    "KPM"),
    ("boodschap delen",        "BD"),
    ("samenwerken",            "SW"),
    ("flexibel opstellen",     "FO"),
    ("pro-actief",             "PH"),
    ("pro actief",             "PH"),
    ("reflecteren",            "RE"),
    ("reflectie",              "RE"),
    # "plannen" bewust als laatste omdat het een substring is van andere woorden
    ("plannen",                "PL"),
]

_SKILL_ORDER = ["OC", "KO", "JKO", "KPM", "PL", "BD", "SW", "FO", "PH", "RE"]


def format_date_nl(raw: str | None) -> str:
    """'2026-06-03T12:00:00Z' → '3 jun 2026'"""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"{dt.day} {_MONTH_NL[dt.month - 1]} {dt.year}"
    except Exception:
        return (raw or "")[:10]


def strip_html(html: str) -> str:
    """Verwijdert HTML-tags en normaliseert witruimte."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _level_after(text: str, pos: int) -> str | None:
    """
    Zoekt een niveauaanduiding in de ~25 tekens na positie pos.
    Herkent:  'niv 2', 'niveau 2', '(niv2)', ' 2 ', ' 2$'
    Negeert:  '1ste', '2de', '3e'  (rangtelwoorden)
    """
    window = text[pos: pos + 25]
    # Expliciet "niv(eau) N"
    m = re.search(r"niv(?:eau)?\s*\.?\s*(\d)", window, re.IGNORECASE)
    if m:
        return m.group(1)
    # Losse digit, maar niet gevolgd door rangtelwoordsuffix (ste/de/e)
    m = re.search(r"(?<!\d)(\d)(?!\d)(?!\s*(?:ste|de|e)\b)", window)
    if m:
        return m.group(1)
    return None


def parse_skills_from_title(title: str, note: str = "") -> str:
    """
    Probeert vaardigheidscodes en niveaus te extraheren uit titel en optionele
    toelichting (note).  De titel is leidend voor het vinden van de skill;
    als het niveau daar ontbreekt, wordt de note doorzocht.
    Voorbeelden:
      'Flexibel opstellen niv 2'        → FO(2)
      'OC & KO 1ste evaluatie'          → OC, KO
      'PH, FO en Reflecteren'           → PH, FO, RE
      'PL 2'                            → PL(2)
      'Plannen, Samenwerken, Reflecteren ronde 2' → PL, SW, RE
    """
    t = title.lower()
    n = (note or "").lower()
    found: dict[str, str | None] = {}  # code → level (of None)

    # Fase 1: zoek bekende afkortingen als losstaand woord
    for abbrev, code in _ABBREV_MAP.items():
        if code in found:
            continue
        for m in re.finditer(r"\b" + re.escape(abbrev) + r"\b", t):
            found[code] = _level_after(t, m.end())
            break  # eerste match volstaat

    # Fase 2: zoek volledige namen (alleen als code nog niet gevonden)
    for name, code in _NAME_MAP:
        if code in found:
            continue
        idx = t.find(name)
        if idx != -1:
            found[code] = _level_after(t, idx + len(name))

    # Fase 3: verrijk ontbrekende niveaus via de note
    if n:
        for abbrev, code in _ABBREV_MAP.items():
            if code not in found or found[code] is not None:
                continue  # niet gevonden, of al een niveau
            for m in re.finditer(r"\b" + re.escape(abbrev) + r"\b", n):
                lvl = _level_after(n, m.end())
                if lvl is not None:
                    found[code] = lvl
                    break
        for name, code in _NAME_MAP:
            if code not in found or found[code] is not None:
                continue
            idx = n.find(name)
            if idx != -1:
                lvl = _level_after(n, idx + len(name))
                if lvl is not None:
                    found[code] = lvl
        # Laatste redmiddel: één skill zonder niveau + expliciete 'niv(eau) N' ergens in note
        skills_without_level = [c for c, l in found.items() if l is None]
        if len(skills_without_level) == 1:
            m = re.search(r"niv(?:eau)?\s*\.?\s*(\d)", n, re.IGNORECASE)
            if m:
                found[skills_without_level[0]] = m.group(1)

    if not found:
        return ""

    parts = []
    for code in _SKILL_ORDER:
        if code in found:
            lvl = found[code]
            parts.append(f"{code}({lvl})" if lvl else code)
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# CLI argumenten
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Toon openstaande evaluatieverzoeken voor de beoordelaar."
    )
    parser.add_argument(
        "--dump-raw",
        action="store_true",
        help="Schrijf de ruwe API-respons naar evaluaties_debug.json voor inspectie.",
    )
    return parser.parse_args()


ARGS = parse_args()

# ---------------------------------------------------------------------------
# JWT & token helpers
# ---------------------------------------------------------------------------

def _jwt_is_valid(token: str) -> bool:
    try:
        payload_b64 = token.split(".")[1]
        padding = (4 - len(payload_b64) % 4) % 4
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + "=" * padding)
        )
        exp = payload.get("exp")
        if exp is None:
            return True
        return time.time() < exp
    except Exception:
        return False


def _save_env_key(key: str, value: str) -> None:
    """Schrijft of overschrijft een KEY=value-regel in .env."""
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if re.search(rf"^{re.escape(key)}=", content, flags=re.MULTILINE):
            content = re.sub(
                rf"^{re.escape(key)}=.*$",
                f"{key}={value}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content = f"{key}={value}\n" + content
    else:
        content = f"{key}={value}\n"
    env_path.write_text(content, encoding="utf-8")


def _try_browser_token(browser: str) -> str | None:
    if sys.platform != "darwin":
        return None
    js = (
        "(function(){"
        "var s=[sessionStorage,localStorage];"
        "for(var i=0;i<s.length;i++){"
        "for(var j=0;j<s[i].length;j++){"
        "var v=s[i].getItem(s[i].key(j));"
        "if(!v)continue;"
        "if(v.split('.').length===3&&v.length>100)return v;"
        "try{var p=JSON.parse(v);"
        "var t=p&&(p.token||p.access_token||p.jwt);"
        "if(t&&t.split('.').length===3&&t.length>100)return t;"
        "}catch(e){}"
        "}}"
        "return '';"
        "})()"
    )
    scripts = {
        "chrome": (
            'if application "Google Chrome" is running then\n'
            '    tell application "Google Chrome"\n'
            '        repeat with w in windows\n'
            '            repeat with t in tabs of w\n'
            '                if URL of t contains "portfolio.drieam.app" then\n'
            f'                    set tok to execute t javascript "{js}"\n'
            '                    if tok is not "" then return tok\n'
            '                end if\n'
            '            end repeat\n'
            '        end repeat\n'
            '    end tell\n'
            'end if\n'
            'return ""'
        ),
        "safari": (
            'if application "Safari" is running then\n'
            '    tell application "Safari"\n'
            '        repeat with w in windows\n'
            '            repeat with t in tabs of w\n'
            '                if URL of t contains "portfolio.drieam.app" then\n'
            f'                    set tok to do JavaScript "{js}" in t\n'
            '                    if tok is not "" then return tok\n'
            '                end if\n'
            '            end repeat\n'
            '        end repeat\n'
            '    end tell\n'
            'end if\n'
            'return ""'
        ),
    }
    script = scripts.get(browser.lower())
    if not script:
        return None
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=8,
        )
        token = (result.stdout or "").strip()
        if token and len(token) > 100 and token.count(".") == 2:
            return token
    except Exception:
        pass
    return None


def get_bearer_token(force_prompt: bool = False) -> str:
    token_from_var = (globals().get("BEARER_TOKEN") or "").strip()
    if token_from_var and not force_prompt:
        if _jwt_is_valid(token_from_var):
            print("Gebruik bearer token uit .env")
            return token_from_var
        print("Bearer token uit .env is verlopen.")
    elif token_from_var and force_prompt:
        print("Bearer token uit .env is verlopen.")

    print()
    print("Hoe wil je een nieuw token ophalen?")
    print("  1) Probeer token op te halen uit geopende tab in Chrome")
    print("  2) Probeer token op te halen uit geopende tab in Safari")
    print("  3) Handmatig plakken")
    print()
    keuze = input("Keuze [1]: ").strip() or "1"

    if keuze in ("1", "2"):
        browser = "chrome" if keuze == "1" else "safari"
        print(f"Token zoeken in {browser.capitalize()}...", end=" ", flush=True)
        token = _try_browser_token(browser)
        if not token:
            print("niet gevonden.")
            token = input("Plak bearer token (of Enter om af te breken): ").strip()
            if not token:
                raise SystemExit("Geen token opgegeven, script gestopt.")
        else:
            print("gevonden!")
    else:
        token = input("Plak bearer token: ").strip()
        if not token:
            raise SystemExit("Geen token opgegeven, script gestopt.")

    save = input("Token opslaan in .env? [J/n] ").strip().lower()
    if save in ("", "j", "y", "yes", "ja"):
        _save_env_key("PORTFLOW_BEARER_TOKEN", token)
        globals()["BEARER_TOKEN"] = token
    return token


def get_own_portfolio_id() -> str:
    """
    Geeft het eigen portfolio-ID terug (coach/beoordelaar).
    Leest uit .env (PORTFLOW_OWN_PORTFOLIO_ID); vraagt eenmalig als het ontbreekt.
    Tip: het ID staat in de Portflow-URL als je je eigen portfolio opent,
         bijv. https://portfolio.drieam.app/portfolio/23588/...
    """
    pid = (globals().get("OWN_PORTFOLIO_ID") or "").strip()
    if pid:
        return pid

    print()
    print("Jouw eigen portfolio-ID is nodig voor de API-aanroep.")
    print("Je vindt het in de URL als je je eigen portfolio opent, bijv.:")
    print("  https://portfolio.drieam.app/portfolio/23588/...")
    pid = input("Voer jouw portfolio-ID in: ").strip()
    if not pid:
        raise SystemExit("Geen portfolio-ID opgegeven, script gestopt.")

    save = input("Portfolio-ID opslaan in .env? [J/n] ").strip().lower()
    if save in ("", "j", "y", "yes", "ja"):
        _save_env_key("PORTFLOW_OWN_PORTFOLIO_ID", pid)
        globals()["OWN_PORTFOLIO_ID"] = pid
    return pid


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _headers(token: str) -> dict:
    return {
        "accept": "*/*",
        "authorization": f"Bearer {token}",
        "user-agent": "Mozilla/5.0",
    }


def api_get(
    token: str,
    path: str,
    params: dict | None = None,
) -> requests.Response | None:
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=_headers(token), params=params, timeout=15)
        if resp.status_code == 401:
            print("\nToken verlopen (401). Herstart het script.")
            raise SystemExit(1)
        return resp
    except requests.exceptions.RequestException as e:
        print(f"\nRequest fout ({url}): {e}")
        return None


# ---------------------------------------------------------------------------
# Ophalen evaluatieverzoeken
# ---------------------------------------------------------------------------

def get_pending_invitations(token: str, portfolio_id: str) -> list[dict]:
    """
    Haalt alle openstaande evaluatieverzoeken op via:
      GET /api/v1/portfolios/{portfolio_id}/progress-review/invitations/received
        ?status=not_submitted&order_by=created_at&order_direction=desc
    """
    all_items: list = []
    page = 1
    while True:
        r = api_get(token, f"/portfolios/{portfolio_id}/progress-review/invitations/received", {
            "order_by":        "created_at",
            "order_direction": "desc",
            "page":            page,
            "per_page":        PER_PAGE,
            "status":          "not_submitted",
        })
        if r is None or not r.ok:
            print(f"API-fout: {r.status_code if r else 'geen respons'}")
            break
        data = r.json()

        if ARGS.dump_raw:
            _dump_raw({"page": page, "response": data}, f"invitations_p{page}")

        if not isinstance(data, list) or not data:
            break
        all_items.extend(data)
        if len(data) < PER_PAGE:
            break
        page += 1
    return all_items


def map_invitation(item: dict) -> dict:
    requester = item.get("requester") or {}
    title      = item.get("review_request_title") or ""
    created_at = item.get("created_at") or ""
    note_html  = item.get("review_request_note") or ""
    note_text  = strip_html(note_html)

    return {
        "id":               item.get("id"),
        "review_request_id": item.get("review_request_id"),
        "portfolio_id":     item.get("portfolio_id"),
        "title":            title,
        "student":          requester.get("name") or "",
        "date":             format_date_nl(created_at),
        "due_date":         format_date_nl(item.get("suggested_due_date")),
        "status":           item.get("status") or "",
        "note":             note_text,
        "skills":           parse_skills_from_title(title, note_text),
        "raw":              item,
    }


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------

def _dump_raw(data: object, label: str) -> None:
    path = Path("evaluaties_debug.json")
    existing: list = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not isinstance(existing, list):
        existing = []
    existing.append({"label": label, "data": data})
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Weergave
# ---------------------------------------------------------------------------

def print_evaluations_table(evaluations: list[dict]) -> None:
    if not evaluations:
        print("\nGeen openstaande evaluatieverzoeken gevonden.")
        return

    nr_w        = len(str(len(evaluations)))
    max_title   = max(max(len(e["title"])   for e in evaluations), len("Titel"))
    max_student = max(max(len(e["student"]) for e in evaluations), len("Student"))
    max_date    = max(max(len(e["date"])    for e in evaluations), len("Aangemaakt op"))
    max_skills  = max(max(len(e["skills"])  for e in evaluations), len("Vaardigheden"))

    header = (
        f"{'Nr':>{nr_w}}  "
        f"{'Titel':<{max_title}}  "
        f"{'Student':<{max_student}}  "
        f"{'Aangemaakt op':<{max_date}}  "
        f"{'Vaardigheden':<{max_skills}}"
    )
    print()
    print(header)
    print("-" * len(header))

    for i, ev in enumerate(evaluations, 1):
        print(
            f"{i:>{nr_w}}  "
            f"{ev['title']:<{max_title}}  "
            f"{ev['student']:<{max_student}}  "
            f"{ev['date']:<{max_date}}  "
            f"{ev['skills']:<{max_skills}}"
        )
    print()
    print(f"  {len(evaluations)} openstaande evaluatie(s)")
    print()


def show_details(ev: dict) -> None:
    status_label = _STATUS_NL.get(ev["status"], ev["status"])
    print()
    print(f"  Titel       : {ev['title']}")
    print(f"  Student     : {ev['student']}")
    print(f"  Aangemaakt  : {ev['date']}")
    if ev["due_date"]:
        print(f"  Deadline    : {ev['due_date']}")
    print(f"  Status      : {status_label}")
    print(f"  Vaardigh.   : {ev['skills'] or '(niet herkend uit titel)'}")
    if ev["note"]:
        note = ev["note"]
        if len(note) > 200:
            note = note[:200] + "…"
        print(f"  Toelichting : {note}")
    rid = ev.get("review_request_id")
    if rid:
        print(f"  Portflow    : https://portfolio.drieam.app/portfolio/access-and-requests/progress-reviews")
    print()


# ---------------------------------------------------------------------------
# Interactief keuzenmenu
# ---------------------------------------------------------------------------

def run_menu(evaluations: list[dict]) -> None:
    print_evaluations_table(evaluations)
    while True:
        raw = input(
            f"Voer een nummer in (1–{len(evaluations)}) voor details,"
            " of druk Enter om te stoppen: "
        ).strip()
        if not raw:
            break
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(evaluations):
                show_details(evaluations[idx - 1])
            else:
                print(f"  Ongeldig nummer. Kies tussen 1 en {len(evaluations)}.")
        else:
            print("  Voer een geldig getal in.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

try:
    token        = get_bearer_token()
    portfolio_id = get_own_portfolio_id()

    print(f"Openstaande evaluatieverzoeken ophalen...", end="  ", flush=True)
    raw_items    = get_pending_invitations(token, portfolio_id)
    evaluations  = [map_invitation(item) for item in raw_items]
    print(f"{len(evaluations)} gevonden.")

    if not evaluations:
        print("\nGeen openstaande evaluatieverzoeken gevonden.")
    else:
        run_menu(evaluations)

except KeyboardInterrupt:
    print("\nAfgebroken. Tot ziens!")

