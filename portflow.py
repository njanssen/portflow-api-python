import requests
import csv
import time
import argparse
import json
import os
from datetime import datetime
from pathlib import Path


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

        # Support multi-line values that start with [ or {
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


def parse_coach_students(raw_json: str):
    if not raw_json.strip():
        return []

    import re as _re
    raw_json = _re.sub(r",\s*([\]\}])", r"\1", raw_json)  # strip trailing commas
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        print("Waarschuwing: PORTFLOW_COACH_STUDENTS_JSON bevat ongeldige JSON. Gebruik lege lijst.")
        return []

    if not isinstance(data, list):
        print("Waarschuwing: PORTFLOW_COACH_STUDENTS_JSON moet een JSON lijst zijn. Gebruik lege lijst.")
        return []

    students = []
    for entry in data:
        if not isinstance(entry, dict):
            continue

        # Separator entry: {"separator": true}
        if entry.get("separator") is True:
            students.append((SEPARATOR_SENTINEL, None, "", "", ""))
            continue

        name = str(entry.get("name") or "").strip()
        if not name:
            continue

        start_date_raw = str(entry.get("start_date") or "").strip()
        start_date = None
        if start_date_raw:
            try:
                start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
            except ValueError:
                print(
                    "Waarschuwing: ongeldige startdatum '",
                    start_date_raw,
                    "' voor student ",
                    name,
                    ". Verwacht YYYY-MM-DD.",
                    sep="",
                )

        semester = str(entry.get("semester") or "").strip()
        tribe = str(entry.get("tribe") or "").strip()
        gilde = str(entry.get("gilde") or "").strip()

        students.append((name, start_date, semester, tribe, gilde))

    return students


load_env_file()

BASE_URL = "https://portfolio.drieam.app/api/v1"
PER_PAGE = 200
BEARER_TOKEN = os.getenv("PORTFLOW_BEARER_TOKEN", "").strip()

SECTION_ID = "72086"
CURRENT_SEMESTER_START = datetime(2026, 2, 12).date()
CURRENT_SEMESTER_END = datetime(2026, 6, 30).date()

GOALS = {
    "Overzicht creëren",
    "Kritisch oordelen",
    "Juiste kennis ontwikkelen",
    "Kwalitatief Product Maken",
    "Plannen",
    "Boodschap Delen",
    "Samenwerken",
    "Flexibel opstellen",
    "Pro-actief handelen",
    "Reflecteren"
}

GOAL_COLUMNS = [
    ("Overzicht creëren",           "OC"),
    ("Kritisch oordelen",           "KO"),
    ("Juiste kennis ontwikkelen",   "JKO"),
    ("Kwalitatief Product Maken",   "KPM"),
    ("Plannen",                     "PL"),
    ("Boodschap Delen",             "BD"),
    ("Samenwerken",                 "SW"),
    ("Flexibel opstellen",          "FO"),
    ("Pro-actief handelen",         "PH"),
    ("Reflecteren",                 "RE"),
]

# Sentinel waarde in .env die een horizontale scheidingslijn in de tabel veroorzaakt
SEPARATOR_SENTINEL = "---"

# JSON lijst met studentconfig uit .env.
# Voorbeeld item: {"name":"Student Naam","start_date":"2026-04-18"}
CURRENT_COACH_STUDENTS = parse_coach_students(
    os.getenv("PORTFLOW_COACH_STUDENTS_JSON", "").strip()
)

# Afgeleide lookups voor snel gebruik
COACH_STUDENT_NAMES = {name for name, *_ in CURRENT_COACH_STUDENTS}
COACH_STUDENT_START_DATES = {name: start for name, start, *_ in CURRENT_COACH_STUDENTS if start is not None}
COACH_STUDENT_SEMESTER = {name: sem for name, _, sem, *_ in CURRENT_COACH_STUDENTS if sem}
COACH_STUDENT_TRIBE = {name: tribe for name, _, _sem, tribe, *_ in CURRENT_COACH_STUDENTS if tribe}
COACH_STUDENT_GILDE = {name: gilde for name, _, _sem, _tribe, gilde in CURRENT_COACH_STUDENTS if gilde}

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dump-schema",
        action="store_true",
        help="Schrijf een overzicht van alle gevonden API-velden naar schema_inventory.txt",
    )
    parser.add_argument(
        "--debug-api",
        action="store_true",
        help="Log alle API-calls en responses naar api_debug_log.jsonl",
    )
    parser.add_argument(
        "--debug-pending",
        action="store_true",
        help="Log evaluatie-beslissingen (waarom wel/geen ?) naar pending_debug.json",
    )
    parser.add_argument(
        "--semester",
        choices=["1", "2", "3"],
        default=None,
        help="Semester scope: 1=huidig, 2=alles, 3=sep25-jan26",
    )
    parser.add_argument(
        "--students",
        choices=["1", "2", "3"],
        default=None,
        help="Student ophaal methode: 1=shared collection, 2=section, 3=coach array",
    )
    parser.add_argument(
        "--output",
        choices=["1", "2", "3"],
        default=None,
        help="Output optie: 1=enkele student, 2=CSV export, 3=tabel",
    )
    parser.add_argument(
        "--anoniem",
        action="store_true",
        help="Vervang alle studentnamen door ******* in de uitvoer",
    )
    return parser.parse_args()


ARGS = parse_args()
DUMP_SCHEMA = ARGS.dump_schema
DEBUG_API = ARGS.debug_api
DEBUG_PENDING = ARGS.debug_pending

API_DEBUG_FILE = "api_debug_log.jsonl"
PENDING_DEBUG_FILE = "pending_debug.json"

PENDING_DEBUG_EVENTS = []


def init_debug_files():
    if DEBUG_API:
        with open(API_DEBUG_FILE, "w", encoding="utf-8") as f:
            f.write("")
        print(f"API debug logging enabled: {API_DEBUG_FILE}")

    if DEBUG_PENDING:
        with open(PENDING_DEBUG_FILE, "w", encoding="utf-8") as f:
            f.write("[]\n")
        print(f"Pending debug logging enabled: {PENDING_DEBUG_FILE}")


def log_api_debug(url, params, status_code=None, body=None, error=None):
    if not DEBUG_API:
        return

    payload = {
        "timestamp": datetime.now().isoformat(),
        "url": url,
        "params": params,
        "status_code": status_code,
        "error": error,
        "response_body": body,
    }

    with open(API_DEBUG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def log_pending_debug_event(event):
    if not DEBUG_PENDING:
        return
    PENDING_DEBUG_EVENTS.append(event)


def maybe_write_pending_debug_report():
    if not DEBUG_PENDING:
        return
    with open(PENDING_DEBUG_FILE, "w", encoding="utf-8") as f:
        json.dump(PENDING_DEBUG_EVENTS, f, ensure_ascii=False, indent=2)
        f.write("\n")


class SchemaInventory:
    def __init__(self):
        self.path_types = {}
        self.path_samples = {}

    def _type_name(self, value):
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "dict"
        return type(value).__name__

    def _add_observation(self, path, value):
        t = self._type_name(value)
        self.path_types.setdefault(path, set()).add(t)

        if t in {"str", "int", "float", "bool", "null"}:
            sample = repr(value)
            samples = self.path_samples.setdefault(path, set())
            if len(samples) < 3:
                samples.add(sample)

    def observe(self, value, path):
        self._add_observation(path, value)

        if isinstance(value, dict):
            for k, v in value.items():
                self.observe(v, f"{path}.{k}")
            return

        if isinstance(value, list):
            for entry in value:
                self.observe(entry, f"{path}[]")

    def write_report(self, file_path="schema_inventory.txt"):
        lines = []
        lines.append("# API Schema Inventory (observed from live responses)")
        lines.append("")
        lines.append(
            "# Tip: run with option 'All students' for a more complete overview."
        )
        lines.append("")

        for path in sorted(self.path_types.keys()):
            types = ", ".join(sorted(self.path_types[path]))
            samples = sorted(self.path_samples.get(path, set()))
            if samples:
                lines.append(f"{path} | types={types} | samples={'; '.join(samples)}")
            else:
                lines.append(f"{path} | types={types}")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


SCHEMA = SchemaInventory() if DUMP_SCHEMA else None
init_debug_files()


def observe_schema(value, path):
    if SCHEMA is not None:
        SCHEMA.observe(value, path)


def maybe_write_schema_report():
    if SCHEMA is None:
        return
    SCHEMA.write_report("schema_inventory.txt")
    print("Schema inventory exported to schema_inventory.txt")


# ------------------------
# Helper functions
# ------------------------

def get_bearer_token(force_prompt: bool = False):
    token_from_var = (globals().get("BEARER_TOKEN") or "").strip()
    if token_from_var and not force_prompt:
        return token_from_var
    return input("Enter Bearer token: ").strip()


def get_section_id(force_prompt: bool = False) -> str:
    section_from_var = (globals().get("SECTION_ID") or "").strip()
    if section_from_var and not force_prompt:
        print(f"Using SECTION_ID from variable: {section_from_var}")
        return section_from_var
    if not section_from_var:
        print("No SECTION_ID set; prompting for section_id...")
    return input("Enter section_id: ").strip()


def name_to_initials(full_name: str) -> str:
    if not isinstance(full_name, str):
        return ""
    # Drop common Dutch tussenvoegsels.
    skip = {"van", "de", "der", "den", "ten", "ter", "te", "'t", "v", "d"}
    parts = [p for p in full_name.replace("-", " ").split() if p]
    letters = []
    for part in parts:
        lowered = part.lower()
        if lowered in skip:
            continue
        # Keep only alphabetic start.
        first = next((ch for ch in part if ch.isalpha()), "")
        if first:
            letters.append(first.upper())
    return "".join(letters)


def student_order_and_width(students: dict, preferred_order: list[str] | None = None) -> tuple[list[str], int]:
    if preferred_order:
        ordered_names = [n for n in preferred_order if n in students]
        # append any names not in preferred_order at the end
        ordered_names += sorted(n for n in students if n not in set(ordered_names))
    else:
        ordered_names = sorted(students.keys())
    width = max(2, len(str(len(ordered_names))))
    return ordered_names, width


def resolve_student_selection(selection: str, ordered_names: list[str], students: dict) -> str | None:
    s = (selection or "").strip()
    if not s:
        return None

    if s.isdigit():
        idx = int(s)
        if 1 <= idx <= len(ordered_names):
            return ordered_names[idx - 1]
        return None

    if s in students:
        return s

    return None


def choose_semester_scope() -> str:
    if ARGS.semester:
        if ARGS.semester == "2":
            return "all"
        if ARGS.semester == "3":
            return "sep25_jan26"
        return "current"
    print("\nVan welk semester wil je de evaluaties zien?")
    print("1) Huidig semester (vanaf 3-feb-26) [standaard]")
    print("2) Alle semesters")
    print("3) Semester sep25 t/m jan26 (1-sep-2025 t/m 31-jan-2026)")

    choice = input("Voer 1, 2 of 3 in (standaard 1): ").strip()
    if choice == "2":
        return "all"
    if choice == "3":
        return "sep25_jan26"
    return "current"

def request_with_retries(url, headers, params=None, max_attempts=3):
    attempt = 0
    while attempt < max_attempts:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response_body = response.text

            if response.status_code == 401:
                log_api_debug(url, params, status_code=401, body=response_body)
                return "TOKEN_EXPIRED"

            if response.status_code == 404:
                # Missing resources (e.g. student without linked portfolio) should not be retried.
                log_api_debug(url, params, status_code=404, body=response_body)
                return response

            response.raise_for_status()
            log_api_debug(url, params, status_code=response.status_code, body=response_body)
            return response

        except requests.exceptions.RequestException as e:
            attempt += 1
            log_api_debug(url, params, error=str(e))
            print(f"Request failed ({attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                print("Retrying in 5 seconds...")
                time.sleep(5)
            else:
                print("3 failed attempts. Continuing...")
                return None

# ------------------------
# Student fetching (paginated)
# ------------------------

def get_shared_collections(token):
    headers = {
        "accept": "*/*",
        "authorization": f"Bearer {token}",
        "user-agent": "Mozilla/5.0"
    }

    all_items = []
    page = 1

    while True:
        response = request_with_retries(
            f"{BASE_URL}/shares/shared-with-me",
            headers,
            params={
                "order_by": "created_at",
                "order_direction": "desc",
                "page": page,
                "per_page": PER_PAGE
            }
        )

        if response in (None, "TOKEN_EXPIRED"):
            return response

        data = response.json()
        observe_schema(data, "shares.shared_with_me.response")
        if not data:
            break

        all_items.extend(data)

        if len(data) < 20:
            break

        page += 1

    return all_items

def get_students_from_section(token, section_id):
    headers = {
        "accept": "*/*",
        "authorization": f"Bearer {token}",
        "user-agent": "Mozilla/5.0"
    }

    students = {}
    page = 1

    while True:
        response = request_with_retries(
            f"{BASE_URL}/dashboard",
            headers,
            params={
                "section_id": section_id,
                "page": page,
                "per_page": PER_PAGE
            }
        )

        if response in (None, "TOKEN_EXPIRED"):
            return response

        data = response.json()
        observe_schema(data, "dashboard.response")
        page_students = data.get("students", [])

        if not page_students:
            break

        for student in page_students:
            name = student["name"]
            portfolio_id = student["portfolio_id"]
            if name not in students:
                students[name] = {
                    "student_id": student["id"],
                    "portfolio_ids": set()
                }
            students[name]["portfolio_ids"].add(portfolio_id)

        if len(page_students) < PER_PAGE:
            break

        page += 1

    return students

def extract_students(shared_items):
    students = {}
    for item in shared_items:
        inviter = item.get("inviter")
        if not inviter or inviter.get("current_role") != "student":
            continue

        name = inviter["name"]
        portfolio_id = item["portfolio_id"]

        if name not in students:
            students[name] = {
                "student_id": inviter["id"],
                "portfolio_ids": set()
            }

        students[name]["portfolio_ids"].add(portfolio_id)

    return students

# ------------------------
# Portfolio & feedback fetching
# ------------------------

def get_goals(token, portfolio_id):
    headers = {"accept": "*/*", "authorization": f"Bearer {token}"}
    response = request_with_retries(
        f"{BASE_URL}/portfolios/{portfolio_id}/goals",
        headers,
        params={"page": 1, "per_page": PER_PAGE}
    )
    if response in (None, "TOKEN_EXPIRED"):
        return response
    if isinstance(response, requests.Response) and response.status_code == 404:
        return "NOT_FOUND"
    data = response.json()
    observe_schema(data, "portfolios.goals.response")
    return data

def get_feedback(token, portfolio_id, goal_id):
    headers = {"accept": "*/*", "authorization": f"Bearer {token}"}
    feedback_items = []
    page = 1

    while True:
        response = request_with_retries(
            f"{BASE_URL}/portfolios/{portfolio_id}/goals/{goal_id}/feedback-items",
            headers,
            params={"page": page, "per_page": PER_PAGE}
        )

        if isinstance(response, requests.Response) and response.status_code == 404:
            return "NOT_FOUND"

        if response is None:
            break

        if response == "TOKEN_EXPIRED":
            return "TOKEN_EXPIRED"

        data = response.json()
        observe_schema(data, "portfolios.goals.feedback_items.response")
        if not data:
            break

        feedback_items.extend(data)

        if len(data) < PER_PAGE:
            break

        page += 1

    return feedback_items

def resolve_level(evaluation):
    level_id = evaluation.get("level")
    if not level_id:
        return None

    for lvl in evaluation.get("level_set", []):
        if lvl["id"] == level_id:
            label = lvl["label"]
            return "0" if label == "Startniveau" else label

    return None


def pending_reason(item, evaluation):
    if not evaluation:
        return "missing_evaluation"

    review_request_scored = evaluation.get("review_request_scored")
    level = evaluation.get("level")
    role = item.get("role")

    if review_request_scored is False:
        return "review_request_scored_false"
    if level in (None, ""):
        return "missing_level"
    if role == "self" and review_request_scored is not True:
        return "self_not_confirmed_scored"
    return None

def resolve_reviewer_name(item, evaluation=None):
    # Best-effort extraction of the assessor/reviewer name from the API payload.
    candidates = [
        (item.get("user") or {}).get("name"),
        (item.get("user") or {}).get("full_name"),
        (item.get("author") or {}).get("name"),
        (item.get("creator") or {}).get("name"),
        (item.get("reviewer") or {}).get("name"),
        (item.get("evaluator") or {}).get("name"),
        (item.get("created_by") or {}).get("name"),
        (item.get("createdBy") or {}).get("name"),
        item.get("user_name"),
        item.get("author_name"),
        item.get("creator_name"),
        item.get("reviewer_name"),
        item.get("evaluator_name"),
        item.get("created_by_name"),
        item.get("createdByName"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    hint_tokens = (
        "review",
        "evaluator",
        "assessor",
        "author",
        "coach",
        "teacher",
        "created_by",
        "createdby",
        "creator",
        "user",
        "inviter",
        "sender",
        "owner",
        "by",
    )
    name_keys = ("name", "full_name", "fullname", "display_name", "displayname")

    def walk(value, in_context: bool = False):
        if isinstance(value, dict):
            for k, v in value.items():
                k_lower = str(k).lower()
                next_context = in_context or any(tok in k_lower for tok in hint_tokens)

                if k_lower in name_keys and next_context and isinstance(v, str) and v.strip():
                    return v.strip()

                if "name" in k_lower and next_context and isinstance(v, str) and v.strip():
                    return v.strip()

                found = walk(v, next_context)
                if found:
                    return found
        elif isinstance(value, list):
            for entry in value:
                found = walk(entry, in_context)
                if found:
                    return found
        return None

    found = walk(item)
    if found:
        return found
    if evaluation is not None:
        return walk(evaluation)
    return None


def parse_datetime_value(raw_value):
    if raw_value in (None, ""):
        return None

    if isinstance(raw_value, datetime):
        return raw_value

    # Handle numeric epoch values (seconds or milliseconds).
    if isinstance(raw_value, (int, float)):
        timestamp = float(raw_value)
        if timestamp > 1e12:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp)
        except (OverflowError, OSError, ValueError):
            return None

    if not isinstance(raw_value, str):
        return None

    text = raw_value.strip()
    if not text:
        return None

    parse_candidates = [text]
    if text.endswith("Z"):
        parse_candidates.insert(0, text[:-1] + "+00:00")

    for candidate in parse_candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass

    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue

    return None


def format_short_date(raw_value):
    dt = parse_datetime_value(raw_value)
    if dt is None:
        return None
    return f"{dt.day}-{dt.strftime('%b').lower()}-{dt.strftime('%y')}"


def date_in_selected_semester(raw_value, semester_scope: str, override_start=None) -> bool:
    if semester_scope == "all":
        return True

    if isinstance(raw_value, datetime):
        dt = raw_value
    else:
        dt = parse_datetime_value(raw_value)
    if dt is None:
        return False

    if semester_scope == "sep25_jan26":
        return SEMESTER_SEP25_JAN26_START <= dt.date() <= SEMESTER_SEP25_JAN26_END

    start = override_start if override_start is not None else CURRENT_SEMESTER_START
    return dt.date() >= start


def resolve_evaluation_date(item, evaluation=None):
    # Prefer explicit assessment timestamps before generic create/update fields.
    date_keys = (
        "submitted_at",
        "submittedat",
        "date",
        "evaluated_at",
        "evaluatedat",
        "evaluation_date",
        "evaluationdate",
        "graded_at",
        "gradedat",
        "reviewed_at",
        "reviewedat",
        "assessed_at",
        "assessedat",
        "created_at",
        "createdat",
        "updated_at",
        "updatedat",
    )

    for source in (evaluation, item):
        if not isinstance(source, dict):
            continue
        for raw_key, raw_value in source.items():
            normalized_key = str(raw_key).lower().replace("_", "")
            if normalized_key in date_keys:
                parsed = parse_datetime_value(raw_value)
                if parsed:
                    return parsed

    hint_tokens = ("evaluat", "grade", "review", "assess", "created", "updated", "submit")
    date_tokens = ("date", "time", "_at", "at")

    def walk(value, in_context=False):
        if isinstance(value, dict):
            for k, v in value.items():
                key = str(k).lower()
                next_context = in_context or any(tok in key for tok in hint_tokens)
                looks_like_date_key = any(tok in key for tok in date_tokens)

                if looks_like_date_key and (next_context or key in date_keys):
                    parsed = parse_datetime_value(v)
                    if parsed:
                        return parsed

                found = walk(v, next_context)
                if found:
                    return found

        elif isinstance(value, list):
            for entry in value:
                found = walk(entry, in_context)
                if found:
                    return found

        return None

    return walk(item) or walk(evaluation)

def collect_results(
    token,
    student_name,
    student_data,
    semester_scope="current",
    override_start=None,
    include_ungraded=False,
):
    results = []

    for portfolio_id in student_data["portfolio_ids"]:
        goals = get_goals(token, portfolio_id)
        if goals == "TOKEN_EXPIRED":
            return "TOKEN_EXPIRED"
        if goals == "NOT_FOUND":
            print(
                f"[404] No linked portfolio for {student_name} "
                f"(student_id={student_data.get('student_id')}, portfolio_id={portfolio_id}). Skipping."
            )
            continue
        if goals is None:
            print(
                f"[ERROR] Failed to fetch goals for {student_name} "
                f"(student_id={student_data.get('student_id')}, portfolio_id={portfolio_id}). Skipping."
            )
            continue

        for goal in goals:
            goal_id = goal["id"]
            goal_name = goal["name"]

            feedback_items = get_feedback(token, portfolio_id, goal_id)
            if feedback_items == "TOKEN_EXPIRED":
                return "TOKEN_EXPIRED"
            if feedback_items == "NOT_FOUND":
                print(
                    f"[404] Feedback endpoint not found for {student_name} "
                    f"(portfolio_id={portfolio_id}, goal_id={goal_id}). Skipping goal."
                )
                continue

            for item in feedback_items:
                observe_schema(item, "portfolios.goals.feedback_items.item")
                if item.get("type") != "criterion_evaluation":
                    continue

                evaluation = item.get("evaluation")
                pending = pending_reason(item, evaluation)

                if not evaluation and not include_ungraded:
                    log_pending_debug_event({
                        "student_name": student_name,
                        "portfolio_id": portfolio_id,
                        "goal_id": goal_id,
                        "goal_name": goal_name,
                        "role": item.get("role"),
                        "decision": "skip",
                        "reason": "missing_evaluation",
                    })
                    continue

                if evaluation:
                    observe_schema(evaluation, "portfolios.goals.feedback_items.item.evaluation")

                evaluation_datetime = resolve_evaluation_date(item, evaluation)
                if not date_in_selected_semester(evaluation_datetime, semester_scope, override_start):
                    log_pending_debug_event({
                        "student_name": student_name,
                        "portfolio_id": portfolio_id,
                        "goal_id": goal_id,
                        "goal_name": goal_name,
                        "role": item.get("role"),
                        "decision": "skip",
                        "reason": "outside_semester",
                        "pending_reason": pending,
                        "review_request_scored": (evaluation or {}).get("review_request_scored"),
                        "level": (evaluation or {}).get("level"),
                    })
                    continue

                # Keep historical behavior for non-table modes: self evaluations are hidden.
                if item.get("role") == "self" and not include_ungraded:
                    log_pending_debug_event({
                        "student_name": student_name,
                        "portfolio_id": portfolio_id,
                        "goal_id": goal_id,
                        "goal_name": goal_name,
                        "role": item.get("role"),
                        "decision": "skip",
                        "reason": "self_role_hidden",
                        "pending_reason": pending,
                    })
                    continue

                level = resolve_level(evaluation)
                is_pending = pending is not None

                if is_pending:
                    if not include_ungraded:
                        log_pending_debug_event({
                            "student_name": student_name,
                            "portfolio_id": portfolio_id,
                            "goal_id": goal_id,
                            "goal_name": goal_name,
                            "role": item.get("role"),
                            "decision": "skip",
                            "reason": "pending_hidden",
                            "pending_reason": pending,
                            "review_request_scored": (evaluation or {}).get("review_request_scored"),
                            "level": (evaluation or {}).get("level"),
                            "review_request_title": (evaluation or {}).get("review_request_title"),
                        })
                        continue

                    # In table mode, pending items are shown as '?'.
                    evaluation_text = "?"
                else:
                    # Avoid exposing scored self-evaluations in table mode.
                    if item.get("role") == "self":
                        log_pending_debug_event({
                            "student_name": student_name,
                            "portfolio_id": portfolio_id,
                            "goal_id": goal_id,
                            "goal_name": goal_name,
                            "role": item.get("role"),
                            "decision": "skip",
                            "reason": "self_scored_hidden",
                            "review_request_scored": (evaluation or {}).get("review_request_scored"),
                            "level": (evaluation or {}).get("level"),
                        })
                        continue

                    if level is None:
                        log_pending_debug_event({
                            "student_name": student_name,
                            "portfolio_id": portfolio_id,
                            "goal_id": goal_id,
                            "goal_name": goal_name,
                            "role": item.get("role"),
                            "decision": "skip",
                            "reason": "missing_level_unexpected",
                            "review_request_scored": (evaluation or {}).get("review_request_scored"),
                        })
                        continue

                    evaluation_text = str(level)

                reviewer_name = resolve_reviewer_name(item, evaluation)
                initials = name_to_initials(reviewer_name) if reviewer_name else ""

                details = []
                if (not is_pending) and initials:
                    details.append(initials)
                evaluation_date = format_short_date(evaluation_datetime)
                if (not is_pending) and evaluation_date:
                    details.append(evaluation_date)

                if details:
                    evaluation_text = f"{level} ({', '.join(details)})"

                results.append({
                    "student_name": student_name,
                    "goal_name": goal_name,
                    "evaluation": evaluation_text
                })

                log_pending_debug_event({
                    "student_name": student_name,
                    "portfolio_id": portfolio_id,
                    "goal_id": goal_id,
                    "goal_name": goal_name,
                    "role": item.get("role"),
                    "decision": "include",
                    "rendered": evaluation_text,
                    "pending_reason": pending,
                    "review_request_scored": (evaluation or {}).get("review_request_scored"),
                    "level": (evaluation or {}).get("level"),
                    "review_request_title": (evaluation or {}).get("review_request_title"),
                    "submitted_at": (evaluation or {}).get("submitted_at"),
                })

    return results


def print_student_evaluations(token, student_name, student_data, semester_scope="current", override_start=None):
    results = collect_results(token, student_name, student_data, semester_scope, override_start=override_start)
    if results == "TOKEN_EXPIRED":
        return "TOKEN_EXPIRED"

    print(f"\n{student_name}")
    goals = {}
    for r in results:
        goals.setdefault(r["goal_name"], []).append(r["evaluation"])

    for goal, evals in goals.items():
        print(f"{goal}: {', '.join(evals)}")

    return "OK"

# ------------------------
# CSV Export (wide format, ; separator)
# ------------------------

def extract_level_short(evaluation_text: str) -> str:
    """Strip reviewer/date details and return just the level value."""
    if " (" in evaluation_text:
        return evaluation_text.split(" (")[0]
    return evaluation_text


def print_coach_table(results, all_names=None):
    if not results and not all_names:
        print("No data to display.")
        return

    # Build student -> goal -> list of short levels
    student_goals = {}
    for r in results:
        s = r["student_name"]
        g = r["goal_name"]
        level = extract_level_short(r["evaluation"])
        student_goals.setdefault(s, {}).setdefault(g, []).append(level)

    # Ensure all expected names are present (even with 0 evaluations); skip separators
    if all_names:
        for name in all_names:
            if name != SEPARATOR_SENTINEL:
                student_goals.setdefault(name, {})

    # Determine column widths
    col_widths = []
    for full_name, abbrev in GOAL_COLUMNS:
        width = len(abbrev)
        for goals in student_goals.values():
            cell = ", ".join(goals.get(full_name, []))
            width = max(width, len(cell))
        col_widths.append(width)

    name_width = max(len("Naam"), max(len(s) for s in student_goals))
    sem_width = max(len("Semester"), max((len(f"Semester {COACH_STUDENT_SEMESTER.get(s, '')}".strip() if COACH_STUDENT_SEMESTER.get(s) else "") for s in student_goals), default=0))
    tribe_width = max(len("Tribe"), max((len(COACH_STUDENT_TRIBE.get(s, "")) for s in student_goals), default=0))
    gilde_width = max(len("Gilde"), max((len(COACH_STUDENT_GILDE.get(s, "")) for s in student_goals), default=0))

    # Header
    header = f"{'Naam':<{name_width}}"
    for (_, abbrev), w in zip(GOAL_COLUMNS, col_widths):
        header += f" | {abbrev:<{w}}"
    header += f" | {'Tribe':<{tribe_width}}"
    header += f" | {'Semester':<{sem_width}}"
    header += f" | {'Gilde':<{gilde_width}}"
    print()
    print(header)
    print("-" * len(header))

    # Rows (in .env order if available, otherwise alphabetical)
    row_order = all_names if all_names else sorted(student_goals.keys())
    for student_name in row_order:
        if student_name == SEPARATOR_SENTINEL:
            print("-" * len(header))
            continue
        if student_name not in student_goals:
            continue
        goals = student_goals[student_name]
        display_name = "*" * len(student_name) if ARGS.anoniem else student_name
        sem_raw = COACH_STUDENT_SEMESTER.get(student_name, "")
        sem = f"Semester {sem_raw}" if sem_raw else ""
        tribe = COACH_STUDENT_TRIBE.get(student_name, "")
        gilde = COACH_STUDENT_GILDE.get(student_name, "")
        _alert_goals = {"Overzicht creëren", "Kritisch oordelen", "Juiste kennis ontwikkelen", "Kwalitatief Product Maken"}
        _alert = all(not goals.get(fn) for fn, _ in GOAL_COLUMNS if fn in _alert_goals)
        _soft_goals = {"Plannen", "Boodschap Delen", "Samenwerken", "Flexibel opstellen", "Pro-actief handelen", "Reflecteren"}
        _soft_empty = sum(1 for fn, _ in GOAL_COLUMNS if fn in _soft_goals and not goals.get(fn))
        _soft_all   = _soft_empty == len(_soft_goals)   # alle 6 leeg → oranje
        _soft_most  = _soft_empty in (4, 5)             # 4 of 5 leeg → geel
        row = f"{display_name:<{name_width}}"
        for (full_name, _), w in zip(GOAL_COLUMNS, col_widths):
            cell = ", ".join(goals.get(full_name, []))
            if _alert and full_name in _alert_goals and not cell:
                row += f" | \033[41m{' ' * w}\033[0m"
            elif _soft_all and full_name in _soft_goals and not cell:
                row += f" | \033[43m{' ' * w}\033[0m"
            elif _soft_most and full_name in _soft_goals and not cell:
                row += f" | \033[33m{' ' * w}\033[0m"
            else:
                row += f" | {cell:<{w}}"
        row += f" | {tribe:<{tribe_width}}"
        row += f" | {sem:<{sem_width}}"
        row += f" | {gilde:<{gilde_width}}"
        print(row)

    print()


def export_csv_wide(results):
    if not results:
        print("No data to export.")
        return

    all_goals = sorted(set(r["goal_name"] for r in results))
    students = {}

    for r in results:
        s = r["student_name"]
        g = r["goal_name"]

        students.setdefault(s, {goal: "" for goal in all_goals})

        if students[s][g]:
            students[s][g] += f", {r['evaluation']}"
        else:
            students[s][g] = r["evaluation"]

    with open("results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Studentname"] + all_goals)

        for student, goal_data in students.items():
            writer.writerow([student] + [goal_data[g] for g in all_goals])

    print("CSV exported to results.csv")

# ------------------------
# Main loop (Ctrl+C safe)
# ------------------------


def _show_progress(current: int, total: int, label: str, bar_width: int = 25) -> None:
    filled = int(bar_width * current / total)
    arrow = ">" if filled < bar_width else "="
    bar = "=" * filled + arrow + " " * (bar_width - filled - 1)
    print(f"\r[{bar}] {current}/{total}  {label:<40}", end="", flush=True)


try:
    _cli_mode = bool(ARGS.semester and ARGS.students and ARGS.output)
    if _cli_mode:
        print(f"Voortgang portflow {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
    while True:
        semester_scope = choose_semester_scope()
        token = get_bearer_token()

        while True:
            if not _cli_mode:
                print("\nChoose student fetching method:")
                print("1) All students with shared collection")
                print("2) Students from section (coachingsdashboard)")
                print("3) Huidige coach studenten volgens array")
            fetch_choice = (ARGS.students or input("Enter 1, 2 or 3 (or 'q' to quit): ").strip())

            if fetch_choice.lower() == "q":
                print("Exiting gracefully. Goodbye!")
                exit()

            if fetch_choice == "1":
                shared_items = get_shared_collections(token)
                if shared_items == "TOKEN_EXPIRED":
                    print("Token expired, please enter a new one.")
                    token = get_bearer_token(force_prompt=True)
                    continue
                students = extract_students(shared_items)
                break

            elif fetch_choice == "2":
                section_id = get_section_id()
                students = get_students_from_section(token, section_id)
                if students == "TOKEN_EXPIRED":
                    print("Token expired, please enter a new one.")
                    token = get_bearer_token(force_prompt=True)
                    continue
                break

            elif fetch_choice == "3":
                shared_items = get_shared_collections(token)
                if shared_items == "TOKEN_EXPIRED":
                    print("Token expired, please enter a new one.")
                    token = get_bearer_token(force_prompt=True)
                    continue
                all_students = extract_students(shared_items)
                students = {
                    name: data
                    for name, data in all_students.items()
                    if name in COACH_STUDENT_NAMES
                }
                missing_names = [
                    name for name in COACH_STUDENT_NAMES
                    if name != SEPARATOR_SENTINEL and name not in all_students
                ]
                if missing_names:
                    print("These coach students were not found in shared collections:")
                    for missing_name in missing_names:
                        print(f"  - {missing_name}")
                break

            else:
                print("Invalid option, try again.")

        if not students:
            print("No students found.")
            continue

        env_order = [name for name, *_ in CURRENT_COACH_STUDENTS]
        ordered_names, number_width = student_order_and_width(students, preferred_order=env_order)

        if not _cli_mode:
            print("\nStudents:")
            for idx, name in enumerate(ordered_names, start=1):
                print(f"- {idx:0{number_width}d} - {name}")

            print("\nChoose output option:")
            print("1) Single student")
            print("2) All students (export to CSV)")
            print("3) Studenten in array weergeven als tabel")
            print("99) Enter student name or id")

        choice = (ARGS.output or input(
            "Enter 1, 2, 3, 99, a student number/name (or 'm' for main menu): "
        ).strip())

        if choice.lower() == "m":
            continue

        if choice == "1":
            selection = input(
                "Enter student number (e.g. 01) or exact name as shown: "
            ).strip()
            name = resolve_student_selection(selection, ordered_names, students)
            if not name:
                print("Student not found (use number or exact name).")
                continue

            status = print_student_evaluations(token, name, students[name], semester_scope, override_start=COACH_STUDENT_START_DATES.get(name))
            if status == "TOKEN_EXPIRED":
                print("Token expired, returning to main menu.")
                continue
            maybe_write_schema_report()
            maybe_write_pending_debug_report()

        elif choice == "99":
            selection = input(
                "Enter student number (e.g. 13 or 02) or exact name as shown: "
            ).strip()
            name = resolve_student_selection(selection, ordered_names, students)
            if not name:
                print("Student not found (use number or exact name).")
                continue

            status = print_student_evaluations(token, name, students[name], semester_scope, override_start=COACH_STUDENT_START_DATES.get(name))
            if status == "TOKEN_EXPIRED":
                print("Token expired, returning to main menu.")
                continue
            maybe_write_schema_report()
            maybe_write_pending_debug_report()

        elif choice == "2":
            all_results = []
            total_students = len(ordered_names)
            for idx, name in enumerate(ordered_names, start=1):
                _show_progress(idx, total_students, name)
                res = collect_results(token, name, students[name], semester_scope, override_start=COACH_STUDENT_START_DATES.get(name))
                if res == "TOKEN_EXPIRED":
                    print()
                    print("Token expired, returning to main menu.")
                    break
                all_results.extend(res)
            else:
                print()
                export_csv_wide(all_results)
                maybe_write_schema_report()
                maybe_write_pending_debug_report()

        elif choice == "3":
            coach_names = [
                name for name, *_ in CURRENT_COACH_STUDENTS
                if name == SEPARATOR_SENTINEL or name in students
            ]
            if not coach_names:
                print("No coach students found in current student list.")
                continue

            all_results = []
            real_names = [n for n in coach_names if n != SEPARATOR_SENTINEL]
            for idx, name in enumerate(real_names, start=1):
                override = COACH_STUDENT_START_DATES.get(name)
                _show_progress(idx, len(real_names), name)
                res = collect_results(
                    token,
                    name,
                    students[name],
                    semester_scope,
                    override_start=override,
                    include_ungraded=True,
                )
                if res == "TOKEN_EXPIRED":
                    print()
                    print("Token expired, returning to main menu.")
                    break
                all_results.extend(res)
            else:
                print()
                print_coach_table(all_results, all_names=coach_names)
                maybe_write_schema_report()
                maybe_write_pending_debug_report()

        else:
            # Shortcut: allow entering a student number or exact name directly.
            name = resolve_student_selection(choice, ordered_names, students)
            if name:
                status = print_student_evaluations(token, name, students[name], semester_scope, override_start=COACH_STUDENT_START_DATES.get(name))
                if status == "TOKEN_EXPIRED":
                    print("Token expired, returning to main menu.")
                else:
                    maybe_write_schema_report()
                    maybe_write_pending_debug_report()
                continue

            print("Invalid option, returning to main menu.")

        if _cli_mode:
            break

except KeyboardInterrupt:
    print("\nKeyboard interrupt detected. Exiting gracefully. Goodbye!")
    exit()