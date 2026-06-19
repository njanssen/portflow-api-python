#!/usr/bin/env python3
"""
graph.py — ASCII tijdlijn grafiek van een student in Portflow.

Gebruik:
    python graph.py --student <voornaam>
    python graph.py --student <voornaam> --token <bearer>
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import requests


# ─── Semester defaults ─────────────────────────────────────────────────────────

SEMESTER_START_DEFAULT = "2026-02-12"
SEMESTER_END_DEFAULT   = "2026-06-30"
BASE_URL               = "https://portfolio.drieam.app/api/v1"
PER_PAGE               = 200


# ─── ANSI kleuren ──────────────────────────────────────────────────────────────

def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()

USE_COLOR = _use_color()

_ANSI = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "grey":   "\033[90m",
    "yellow": "\033[33m",
    "green":  "\033[32m",
    "blue":   "\033[34m",
    "red":    "\033[31m",
    "cyan":   "\033[36m",
}

GOAL_COLUMNS = [
    ("Overzicht creëren",         "OC"),
    ("Kritisch oordelen",         "KO"),
    ("Juiste kennis ontwikkelen",  "JKO"),
    ("Kwalitatief Product Maken",  "KPM"),
    ("Plannen",                    "PL"),
    ("Boodschap Delen",            "BD"),
    ("Samenwerken",                "SW"),
    ("Flexibel opstellen",         "FO"),
    ("Pro-actief handelen",        "PH"),
    ("Reflecteren",                "RE"),
]


def normalize_goal_name(value: str) -> str:
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text)


GOAL_CODE_MAP = {normalize_goal_name(name): code for name, code in GOAL_COLUMNS}


def _col(text: str, color: str) -> str:
    if not USE_COLOR or color not in _ANSI:
        return text
    return f"{_ANSI[color]}{text}{_ANSI['reset']}"


def _bold(text: str) -> str:
    if not USE_COLOR:
        return text
    return f"{_ANSI['bold']}{text}{_ANSI['reset']}"


# ─── .env loader ───────────────────────────────────────────────────────────────

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
        key   = key.strip()
        value = value.strip()
        if not key:
            continue

        if value and value[0] in ("[", "{"):
            depth = value.count("[") + value.count("{") - value.count("]") - value.count("}")
            while depth > 0 and i < len(raw_lines):
                chunk = raw_lines[i].strip()
                i += 1
                value += chunk
                depth += chunk.count("[") + chunk.count("{") - chunk.count("]") - chunk.count("}")
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ[key] = value


def _parse_coach_students_json(raw_json: str) -> list[str]:
    """Haal studentnamen op uit een PORTFLOW_*_STUDENTS_JSON string."""
    if not raw_json.strip():
        return []
    raw_json = re.sub(r",\s*([\]\}])", r"\1", raw_json)
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    names = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("separator") is True:
            continue
        name = str(entry.get("name") or "").strip()
        if name:
            names.append(name)
    return names


# ─── Student lookup ─────────────────────────────────────────────────────────────

def _all_configured_students() -> list[str]:
    """Gesorteerde lijst van alle studentnamen uit coach + tribe JSON configs."""
    seen: set[str] = set()
    result: list[str] = []
    for env_key in ("PORTFLOW_COACH_STUDENTS_JSON", "PORTFLOW_TRIBE_STUDENTS_JSON"):
        for name in _parse_coach_students_json(os.getenv(env_key, "")):
            if name not in seen:
                seen.add(name)
                result.append(name)
    return sorted(result)


def resolve_student_name(query: str) -> str:
    """
    Zoek volledige naam op basis van voornaam of gedeeltelijke naam.
    Bij 0 of meerdere matches: interactief keuzemenu.
    """
    all_names = _all_configured_students()
    q = query.strip().lower()

    # Exacte match op voornaam (eerste woord), of substring in volledige naam
    matches = [
        n for n in all_names
        if n.strip().split()[0].lower() == q or q in n.lower()
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) == 0:
        print(f"Geen student gevonden voor '{query}'.")
        if not all_names:
            print("Geen studenten geconfigureerd in .env (PORTFLOW_COACH_STUDENTS_JSON / PORTFLOW_TRIBE_STUDENTS_JSON).")
            sys.exit(1)
        print("Kies een student uit de lijst:")
        candidates = all_names
    else:
        print(f"Meerdere studenten gevonden voor '{query}'. Kies er één:")
        candidates = matches

    for i, name in enumerate(candidates, 1):
        print(f"  {i:>3}. {name}")

    while True:
        try:
            raw = input("Voer een nummer in: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
        except (ValueError, EOFError):
            pass
        print(f"Ongeldig. Kies tussen 1 en {len(candidates)}.")


# ─── API helpers ────────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {"accept": "*/*", "authorization": f"Bearer {token}", "user-agent": "Mozilla/5.0"}


def _get(url: str, token: str, params: dict | None = None, max_attempts: int = 3):
    """GET met retry. Geeft response, None (netwerk fout), of 'TOKEN_EXPIRED'."""
    headers = _headers(token)
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 401:
                return "TOKEN_EXPIRED"
            if resp.status_code == 404:
                return resp
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            print(f"  Request mislukt ({attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                time.sleep(5)
    return None


def fetch_portfolio_ids(token: str, student_name: str) -> list[int]:
    """Alle portfolio_ids voor een student via /shares/shared-with-me."""
    ids: set[int] = set()
    page = 1
    while True:
        resp = _get(
            f"{BASE_URL}/shares/shared-with-me", token,
            params={"order_by": "created_at", "order_direction": "desc",
                    "page": page, "per_page": PER_PAGE},
        )
        if resp in (None, "TOKEN_EXPIRED") or not isinstance(resp, requests.Response):
            break
        items = resp.json()
        if not items:
            break
        for item in items:
            inviter = item.get("inviter") or {}
            if inviter.get("current_role") == "student" and inviter.get("name") == student_name:
                pid = item.get("portfolio_id")
                if pid:
                    ids.add(pid)
        if len(items) < 20:
            break
        page += 1
    return list(ids)


def fetch_goals(token: str, portfolio_id: int) -> list | str:
    resp = _get(f"{BASE_URL}/portfolios/{portfolio_id}/goals", token,
                params={"page": 1, "per_page": PER_PAGE})
    if resp in (None, "TOKEN_EXPIRED"):
        return resp or "ERROR"
    if isinstance(resp, requests.Response) and resp.status_code == 404:
        return "NOT_FOUND"
    return resp.json()


def fetch_feedback_items(token: str, portfolio_id: int, goal_id) -> list | str:
    all_items: list[dict] = []
    page = 1
    while True:
        resp = _get(
            f"{BASE_URL}/portfolios/{portfolio_id}/goals/{goal_id}/feedback-items",
            token,
            params={"page": page, "per_page": PER_PAGE},
        )
        if resp == "TOKEN_EXPIRED":
            return "TOKEN_EXPIRED"
        if resp is None:
            break
        if isinstance(resp, requests.Response) and resp.status_code == 404:
            break
        data = resp.json()
        if not data:
            break
        all_items.extend(data)
        if len(data) < PER_PAGE:
            break
        page += 1
    return all_items


# ─── Datum helpers ──────────────────────────────────────────────────────────────

_DATE_KEYS = (
    "submitted_at", "evaluated_at", "evaluation_date",
    "graded_at", "reviewed_at", "assessed_at",
    "created_at", "updated_at",
)


def _parse_dt(raw) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    candidates = [text[:-1] + "+00:00", text] if text.endswith("Z") else [text]
    for c in candidates:
        try:
            return datetime.fromisoformat(c)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def resolve_date(item: dict, evaluation: dict | None = None) -> datetime | None:
    for source in (evaluation, item):
        if not isinstance(source, dict):
            continue
        for key in _DATE_KEYS:
            dt = _parse_dt(source.get(key))
            if dt:
                return dt
    return None


# ─── Level helper ───────────────────────────────────────────────────────────────

def resolve_level(evaluation: dict | None) -> str | None:
    if not evaluation:
        return None
    level_id = evaluation.get("level")
    if not level_id:
        return None
    for lvl in evaluation.get("level_set", []):
        if lvl.get("id") == level_id:
            label = str(lvl.get("label", ""))
            return "S" if label in ("Startniveau", "0") else label
    return None


# ─── Evidence mentions ──────────────────────────────────────────────────────────

_EVIDENCE_RE = re.compile(r'data-id="(\d+)"[^>]*data-mention-type="evidence"', re.IGNORECASE)
_TEXT_KEYS   = ("comment", "comments", "body", "content", "text", "feedback",
                "reflection", "self_evaluation_text", "toelichting")


def extract_evidence_ids(item: dict, evaluation: dict | None) -> set[str]:
    ids: set[str] = set()
    for source in (item, evaluation or {}):
        for key in _TEXT_KEYS:
            val = source.get(key) or ""
            if isinstance(val, str):
                ids.update(_EVIDENCE_RE.findall(val))
    return ids


# ─── Grafiek rendering ──────────────────────────────────────────────────────────

def _build_weeks(start, end) -> list:
    """Maandagochtend van elke week van semesterstart tot en met einddatum."""
    weeks  = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        weeks.append(monday)
        monday += timedelta(weeks=1)
    return weeks


def _week_index(dt: datetime, weeks: list) -> int | None:
    d = dt.date()
    for i, ws in enumerate(weeks):
        if ws <= d < ws + timedelta(weeks=1):
            return i
    return None


def render_graph(student_name: str, items: list[dict], sem_start, sem_end) -> None:
    today     = datetime.now().date()
    graph_end = min(sem_end, today + timedelta(days=6))
    weeks     = _build_weeks(sem_start, graph_end)
    n_weeks   = len(weeks)
    skill_codes = [code for _, code in GOAL_COLUMNS]

    # ── Grid opbouwen ─────────────────────────────────────────────────────────
    # grid[code][week_index] = [(symbool, kleur), ...]
    grid: dict[str, list[list[tuple[str, str]]]] = {
        code: [[] for _ in weeks] for code in skill_codes
    }
    # evidence[code][week_index] = set van unieke evidence-IDs
    evidence: dict[str, list[set]] = {
        code: [set() for _ in weeks] for code in skill_codes
    }

    for item in items:
        code = item.get("_goal_code")
        if code not in grid:
            continue  # beroepsproduct of ander niet-vaardigheids-doel

        evaluation = item.get("evaluation") or {}
        dt = resolve_date(item, evaluation)
        if dt is None:
            continue
        wi = _week_index(dt, weeks)
        if wi is None:
            continue

        # Evidence-mentions voor Documenten
        ev_ids = extract_evidence_ids(item, evaluation)
        evidence[code][wi].update(ev_ids)

        # Symbool: alleen tonen als er een niveau is
        level = resolve_level(evaluation)
        if level is not None:
            role = str(item.get("role") or "").lower()
            if role == "self":
                grid[code][wi].append((f"Z{level}", "grey"))
            else:
                grid[code][wi].append((f"C{level}", "blue"))

    # Voeg D-symbolen toe voor documenten (vóór evaluatiesymbolen)
    for code in skill_codes:
        for wi, ev_ids in enumerate(evidence[code]):
            n = len(ev_ids)
            if n == 1:
                grid[code][wi].insert(0, ("D", "green"))
            elif n > 1:
                grid[code][wi].insert(0, (f"D{n}", "green"))

    # Totalen voor koptekst
    total_coach = sum(
        1 for item in items
        if item.get("_goal_code") in grid
        and resolve_level(item.get("evaluation") or {}) is not None
        and str(item.get("role") or "").lower() != "self"
    )
    total_self = sum(
        1 for item in items
        if item.get("_goal_code") in grid
        and resolve_level(item.get("evaluation") or {}) is not None
        and str(item.get("role") or "").lower() == "self"
    )
    total_docs = sum(
        len(ev_ids)
        for code in skill_codes
        for ev_ids in evidence[code]
    )

    # ── Breedte berekenen ─────────────────────────────────────────────────────
    term_w      = shutil.get_terminal_size((120, 40)).columns
    ROW_LABEL_W = 26
    CELL_W      = max(6, (term_w - ROW_LABEL_W - 1 - n_weeks) // n_weeks)

    # ── Koptekst ──────────────────────────────────────────────────────────────
    print()
    print(_bold(f"  {student_name}"))
    print(f"  Semester: {sem_start.strftime('%d-%m-%Y')} t/m {sem_end.strftime('%d-%m-%Y')}")
    print()
    summary = (
        f"Coach-evaluaties: {_bold(str(total_coach))}    "
        f"Zelfevaluaties: {_bold(str(total_self))}    "
        f"Documenten: {_bold(str(total_docs))}"
    )
    print(f"  {summary}")
    print()

    # ── Weekkoptekst ──────────────────────────────────────────────────────────
    lbl_pad  = " " * ROW_LABEL_W
    sep_line = "─" * ROW_LABEL_W

    week_header = lbl_pad + "│"
    week_sep    = sep_line + "┼"
    for w in weeks:
        label        = w.strftime("%d/%m")
        week_header += label.center(CELL_W) + "│"
        week_sep    += "─" * CELL_W         + "┼"

    print(_bold(week_header))
    print(week_sep)

    # ── Vaardigheidrijrijen ────────────────────────────────────────────────────
    for goal_name, code in GOAL_COLUMNS:
        max_name = ROW_LABEL_W - 6  # 2 indent + 3 code + 1 spatie
        short    = goal_name if len(goal_name) <= max_name else goal_name[:max_name - 1] + "…"
        label    = f"  {code:<3} {short}"
        row      = f"{label:<{ROW_LABEL_W}}│"

        for wi in range(n_weeks):
            entries = grid[code][wi]

            if not entries:
                row += " " * CELL_W + "│"
                continue

            plain_parts   = [sym for sym, _ in entries]
            colored_parts = [_col(sym, clr) for sym, clr in entries]
            plain_cell    = " ".join(plain_parts)
            colored_cell  = " ".join(colored_parts)

            if len(plain_cell) > CELL_W:
                # Te breed: compact samenvatten
                n_docs  = sum(1 for sym, _ in entries if sym[0] == "D")
                n_evals = sum(1 for sym, _ in entries if sym[0] in ("Z", "C"))
                parts, colored = [], []
                if n_docs:
                    s = f"D{n_docs}" if n_docs > 1 else "D"
                    parts.append(s)
                    colored.append(_col(s, "green"))
                if n_evals:
                    s = f"×{n_evals}"
                    parts.append(s)
                    colored.append(s)
                plain_cell   = " ".join(parts)
                colored_cell = " ".join(colored)
                if len(plain_cell) > CELL_W:
                    plain_cell   = f"×{len(entries)}"
                    colored_cell = plain_cell

            pad  = CELL_W - len(plain_cell)
            lpad = pad // 2
            rpad = pad - lpad
            row += " " * lpad + colored_cell + " " * rpad + "│"

        print(row)

    print(week_sep)
    print()

    # ── Legenda ───────────────────────────────────────────────────────────────
    legend_items = [
        ("Z1", "grey",  "Zelfevaluatie + niveau"),
        ("C1", "blue",  "Coach/assessor + niveau"),
        ("D",  "green", "Document (evidence)"),
    ]
    legend = "  " + "   ".join(_col(sym, clr) + f" = {label}" for sym, clr, label in legend_items)
    print(legend)
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="ASCII tijdlijn grafiek van een student in Portflow.")
    p.add_argument("--student", required=True, help="Voornaam (of gedeelte) van de student")
    p.add_argument("--token", default="", help="Bearer token (optioneel; anders via .env)")
    return p.parse_args()


def main() -> None:
    load_env_file()
    args = parse_args()

    # ── Token ─────────────────────────────────────────────────────────────────
    token = (args.token or os.getenv("PORTFLOW_BEARER_TOKEN", "")).strip()
    if not token:
        try:
            token = input("Bearer token: ").strip()
        except EOFError:
            token = ""
    if not token:
        print("Geen token opgegeven. Stop.")
        sys.exit(1)

    # ── Student naam opzoeken ─────────────────────────────────────────────────
    student_name = resolve_student_name(args.student)
    print(f"\nStudent: {student_name}")

    # ── Semestergrenzen ───────────────────────────────────────────────────────
    def _parse_date(raw: str, fallback: str):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return datetime.strptime(fallback, "%Y-%m-%d").date()

    sem_start = _parse_date(os.getenv("PORTFLOW_SEMESTER_START", ""), SEMESTER_START_DEFAULT)
    sem_end   = _parse_date(os.getenv("PORTFLOW_SEMESTER_END",   ""), SEMESTER_END_DEFAULT)

    # ── Portfolio IDs ophalen ─────────────────────────────────────────────────
    print("Portfolios ophalen…")
    portfolio_ids = fetch_portfolio_ids(token, student_name)
    if not portfolio_ids:
        print(
            f"Geen portfolios gevonden voor '{student_name}'.\n"
            "Controleer of de student zijn portfolio heeft gedeeld en of het bearer token geldig is."
        )
        sys.exit(1)
    print(f"  {len(portfolio_ids)} portfolio(s) gevonden.")

    # ── Alle feedback-items ophalen ───────────────────────────────────────────
    all_items: list[dict] = []
    seen_ids:  set        = set()

    for pid in portfolio_ids:
        print(f"  Goals ophalen voor portfolio {pid}…")
        goals = fetch_goals(token, pid)
        if goals in (None, "TOKEN_EXPIRED", "NOT_FOUND", "ERROR"):
            continue

        for goal in goals:
            goal_id       = goal.get("id")
            goal_name_raw = str(goal.get("name") or "").strip()
            goal_code     = GOAL_CODE_MAP.get(normalize_goal_name(goal_name_raw))
            if not goal_id:
                continue
            items = fetch_feedback_items(token, pid, goal_id)
            if items in (None, "TOKEN_EXPIRED"):
                continue
            for item in items:
                iid = item.get("id")
                if iid not in seen_ids:
                    seen_ids.add(iid)
                    item["_goal_code"] = goal_code
                    all_items.append(item)

    print(f"  {len(all_items)} unieke feedback-items geladen.")

    # ── Filteren op semester ──────────────────────────────────────────────────
    semester_items = []
    for item in all_items:
        evaluation = item.get("evaluation") or {}
        dt = resolve_date(item, evaluation)
        if dt and sem_start <= dt.date() <= sem_end:
            semester_items.append(item)

    print(f"  {len(semester_items)} items binnen het semester.")

    # ── Grafiek renderen ──────────────────────────────────────────────────────
    render_graph(student_name, semester_items, sem_start, sem_end)


if __name__ == "__main__":
    main()
