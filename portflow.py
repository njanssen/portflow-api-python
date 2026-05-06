import requests
import csv
import time
import argparse
import json
from datetime import datetime

BASE_URL = "https://portfolio.drieam.app/api/v1"
PER_PAGE = 200
BEARER_TOKEN = ""

SECTION_ID = "72086"
CURRENT_SEMESTER_START = datetime(2026, 2, 3).date()
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

# (naam, optionele startdatum huidig semester)
# Vul een datum in als de student later is gestart dan CURRENT_SEMESTER_START.
CURRENT_COACH_STUDENTS = [
]

# Afgeleide lookups voor snel gebruik
COACH_STUDENT_NAMES = {name for name, _ in CURRENT_COACH_STUDENTS}
COACH_STUDENT_START_DATES = {name: start for name, start in CURRENT_COACH_STUDENTS if start is not None}


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
        "--students",
        choices=["coach", "all"],
        default="coach",
        help="Kies studentenset: coach (standaard) of all (alle gedeelde portfolios).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Shortcut voor --students=all",
    )
    return parser.parse_args()


ARGS = parse_args()
DUMP_SCHEMA = ARGS.dump_schema
DEBUG_API = ARGS.debug_api
DEBUG_PENDING = ARGS.debug_pending
STUDENT_MODE = "all" if ARGS.all else ARGS.students

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


def student_order_and_width(students: dict) -> tuple[list[str], int]:
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
            return lvl["label"]

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

def evaluation_group_key(goal_id, item, evaluation):
    if evaluation and evaluation.get("review_request_id"):
        return f"goal:{goal_id}|review_request_id:{evaluation.get('review_request_id')}"

    title = (evaluation or {}).get("review_request_title") or ""
    submitted_at = (evaluation or {}).get("submitted_at") or item.get("date") or ""
    return f"goal:{goal_id}|title:{title}|submitted_at:{submitted_at}"
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

    start = override_start if override_start is not None else CURRENT_SEMESTER_START
    return start <= dt.date() <= CURRENT_SEMESTER_END


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
    include_self_evaluations=False,
    self_evaluations_as_question=False,
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

            eligible_items = []

            for item in feedback_items:
                observe_schema(item, "portfolios.goals.feedback_items.item")
                if item.get("type") != "criterion_evaluation":
                    continue

                role = item.get("role")
                evaluation = item.get("evaluation")
                pending = pending_reason(item, evaluation)

                if role == "self" and not include_self_evaluations:
                    continue

                if not evaluation and not include_ungraded:
                    continue

                if evaluation:
                    observe_schema(evaluation, "portfolios.goals.feedback_items.item.evaluation")

                evaluation_datetime = resolve_evaluation_date(item, evaluation)
                if not date_in_selected_semester(evaluation_datetime, semester_scope, override_start):
                    continue

                details = []
                level = resolve_level(evaluation) if evaluation else None

                if role == "self":
                    # Always show self-evaluation as a question mark.
                    evaluation_text = "?"
                    details.append("self")
                    evaluation_date = format_short_date(evaluation_datetime)
                    if evaluation_date:
                        details.append(evaluation_date)
                else:
                    if pending is not None:
                        if not include_ungraded:
                            continue
                        evaluation_text = "?"
                    else:
                        if level is None:
                            continue
                        evaluation_text = str(level)
                        reviewer_name = resolve_reviewer_name(item, evaluation)
                        initials = name_to_initials(reviewer_name) if reviewer_name else ""
                        if initials:
                            details.append(initials)
                        evaluation_date = format_short_date(evaluation_datetime)
                        if evaluation_date:
                            details.append(evaluation_date)

                eligible_items.append({
                    "item": item,
                    "evaluation": evaluation,
                    "evaluation_text": evaluation_text,
                    "details": details,
                    "pending": pending,
                })

            # Prefer non-self evaluations over self evaluations per review request.
            grouped = {}
            for candidate in eligible_items:
                group_key = evaluation_group_key(
                    goal_id,
                    candidate["item"],
                    candidate["evaluation"],
                )
                grouped.setdefault(group_key, []).append(candidate)

            selected_items = []
            for group_candidates in grouped.values():
                non_self = [
                    c for c in group_candidates if c["item"].get("role") != "self"
                ]
                if non_self:
                    selected_items.extend(non_self)
                else:
                    selected_items.extend(group_candidates)

            for candidate in selected_items:
                item = candidate["item"]
                evaluation = candidate["evaluation"]
                pending = candidate["pending"]
                evaluation_text = candidate["evaluation_text"]
                details = candidate["details"]

                rendered = evaluation_text
                if details:
                    rendered = f"{evaluation_text} ({', '.join(details)})"

                results.append({
                    "student_name": student_name,
                    "goal_name": goal_name,
                    "evaluation": rendered,
                })

                log_pending_debug_event({
                    "student_name": student_name,
                    "portfolio_id": portfolio_id,
                    "goal_id": goal_id,
                    "goal_name": goal_name,
                    "role": item.get("role"),
                    "decision": "include",
                    "rendered": rendered,
                    "pending_reason": pending,
                    "review_request_scored": (evaluation or {}).get("review_request_scored"),
                    "level": (evaluation or {}).get("level"),
                    "review_request_title": (evaluation or {}).get("review_request_title"),
                    "submitted_at": (evaluation or {}).get("submitted_at"),
                })

    return results


def print_student_evaluations(token, student_name, student_data, semester_scope="current", override_start=None):
    results = collect_results(
        token,
        student_name,
        student_data,
        semester_scope,
        override_start=override_start,
        include_self_evaluations=True,
    )
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

    # Ensure all expected names are present (even with 0 evaluations)
    if all_names:
        for name in all_names:
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

    # Header
    header = f"{'Naam':<{name_width}}"
    for (_, abbrev), w in zip(GOAL_COLUMNS, col_widths):
        header += f" | {abbrev:<{w}}"
    print()
    print(header)
    print("-" * len(header))

    # Rows (sorted by name)
    for student_name in sorted(student_goals.keys()):
        goals = student_goals[student_name]
        row = f"{student_name:<{name_width}}"
        for (full_name, _), w in zip(GOAL_COLUMNS, col_widths):
            cell = ", ".join(goals.get(full_name, []))
            row += f" | {cell:<{w}}"
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
# One-shot main flow
# ------------------------

def main():
    semester_scope = "current"
    print(
        f"Current semester: {CURRENT_SEMESTER_START.isoformat()} to {CURRENT_SEMESTER_END.isoformat()}"
    )

    effective_student_mode = STUDENT_MODE
    if not CURRENT_COACH_STUDENTS and effective_student_mode != "all":
        effective_student_mode = "all"
        print(
            "CURRENT_COACH_STUDENTS is empty; showing all students with shared portfolios."
        )

    token = get_bearer_token()
    shared_items = get_shared_collections(token)
    if shared_items == "TOKEN_EXPIRED":
        print("Token expired, please enter a new one.")
        token = get_bearer_token(force_prompt=True)
        shared_items = get_shared_collections(token)

    if shared_items in (None, "TOKEN_EXPIRED"):
        print("Could not fetch shared collections.")
        return

    all_students = extract_students(shared_items)
    if effective_student_mode == "all":
        students = all_students
    else:
        students = {
            name: data
            for name, data in all_students.items()
            if name in COACH_STUDENT_NAMES
        }
        missing_names = [
            name for name in COACH_STUDENT_NAMES if name not in all_students
        ]
        if missing_names:
            print("These coach students were not found in shared collections:")
            for missing_name in missing_names:
                print(f"  - {missing_name}")

    if not students:
        print("No students found.")
        return

    ordered_names, number_width = student_order_and_width(students)

    print("\nStudents:")
    for idx, name in enumerate(ordered_names, start=1):
        print(f"- {idx:0{number_width}d} - {name}")
    if effective_student_mode != "all":
        print("Use --all to show all students that have shared portfolio.")

    print("\nChoose output method:")
    print("1) Single student")
    print("2) All students (export to CSV)")
    print("3) Students in table")

    choice = input("Enter 1, 2, or 3: ").strip()

    if choice == "1":
        selection = input(
            "Enter student number (e.g. 01) or exact name as shown: "
        ).strip()
        name = resolve_student_selection(selection, ordered_names, students)
        if not name:
            print("Student not found (use number or exact name).")
            return

        status = print_student_evaluations(
            token,
            name,
            students[name],
            semester_scope,
            override_start=COACH_STUDENT_START_DATES.get(name),
        )
        if status == "TOKEN_EXPIRED":
            print("Token expired.")

    elif choice == "2":
        all_results = []
        for idx, name in enumerate(ordered_names, start=1):
            print(f"Processing {idx:0{number_width}d} - {name}...")
            res = collect_results(
                token,
                name,
                students[name],
                semester_scope,
                override_start=COACH_STUDENT_START_DATES.get(name),
            )
            if res == "TOKEN_EXPIRED":
                print("Token expired.")
                break
            all_results.extend(res)
        else:
            export_csv_wide(all_results)

    elif choice == "3":
        table_names = ordered_names
        all_results = []
        for idx, name in enumerate(table_names, start=1):
            override = COACH_STUDENT_START_DATES.get(name)
            print(f"Processing {idx}/{len(table_names)} - {name}...")
            res = collect_results(
                token,
                name,
                students[name],
                semester_scope,
                override_start=override,
                include_ungraded=True,
                include_self_evaluations=True,
                self_evaluations_as_question=True,
            )
            if res == "TOKEN_EXPIRED":
                print("Token expired.")
                break
            all_results.extend(res)
        else:
            print_coach_table(all_results, all_names=table_names)

    else:
        print("Invalid option.")

    maybe_write_schema_report()
    maybe_write_pending_debug_report()


try:
    main()
except KeyboardInterrupt:
    print("\nKeyboard interrupt detected. Exiting gracefully. Goodbye!")
    exit()