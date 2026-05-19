import argparse
import csv
import json
import mimetypes
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests


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


load_env_file()

BASE_URL = "https://portfolio.drieam.app/api/v1"
PER_PAGE = 200
BEARER_TOKEN = os.getenv("PORTFLOW_BEARER_TOKEN", "").strip()
SECTION_ID = "72086"
COACH_STUDENTS_JSON = os.getenv("PORTFLOW_COACH_STUDENTS_JSON", "").strip()
SEMESTER_START_DEFAULT = "2026-02-12"
SEMESTER_END_DEFAULT = "2026-06-30"

GOAL_COLUMNS = [
    ("Overzicht creëren", "OC"),
    ("Kritisch oordelen", "KO"),
    ("Juiste kennis ontwikkelen", "JKO"),
    ("Kwalitatief Product Maken", "KPM"),
    ("Plannen", "PL"),
    ("Boodschap Delen", "BD"),
    ("Samenwerken", "SW"),
    ("Flexibel opstellen", "FO"),
    ("Pro-actief handelen", "PH"),
    ("Reflecteren", "RE"),
]


def normalize_goal_name(value: str) -> str:
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text)


GOAL_CODE_MAP = {normalize_goal_name(name): code for name, code in GOAL_COLUMNS}


def parse_date_yyyy_mm_dd(value: str):
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_coach_students(raw_json: str):
    if not raw_json.strip():
        return []

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

        name = str(entry.get("name") or "").strip()
        if not name:
            continue

        start_date_raw = str(entry.get("start_date") or "").strip()
        start_date = parse_date_yyyy_mm_dd(start_date_raw) if start_date_raw else None
        if start_date_raw and start_date is None:
            print(
                "Waarschuwing: ongeldige startdatum '",
                start_date_raw,
                "' voor student ",
                name,
                ". Verwacht YYYY-MM-DD.",
                sep="",
            )

        students.append((name, start_date))

    return students


SEMESTER_START_DATE = parse_date_yyyy_mm_dd(
    os.getenv("PORTFLOW_SEMESTER_START", SEMESTER_START_DEFAULT)
) or datetime.strptime(SEMESTER_START_DEFAULT, "%Y-%m-%d").date()

SEMESTER_END_DATE = parse_date_yyyy_mm_dd(
    os.getenv("PORTFLOW_SEMESTER_END", SEMESTER_END_DEFAULT)
) or datetime.strptime(SEMESTER_END_DEFAULT, "%Y-%m-%d").date()

COACH_STUDENT_START_DATES = {
    name: start
    for name, start in parse_coach_students(COACH_STUDENTS_JSON)
    if start is not None
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Exporteer volledige Portflow collectie van 1 student naar mappenstructuur."
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Basismap voor export (standaard: huidige map)",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Bearer token (optioneel; anders via variabele of prompt)",
    )
    parser.add_argument(
        "--section-id",
        default="",
        help="Section id voor dashboard studentlijst (optioneel)",
    )
    parser.add_argument(
        "--students-source",
        choices=["section", "shared"],
        default="section",
        help="Bron voor studentenlijst: section (default) of shared",
    )
    return parser.parse_args()


def sanitize_name(value: str, fallback: str = "onbekend") -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    cleaned = re.sub(r"[\\/:*?\"<>|]", "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" .")
    return cleaned or fallback


def parse_datetime_value(raw_value):
    if raw_value in (None, ""):
        return None

    if isinstance(raw_value, datetime):
        return raw_value

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

    candidates = [text]
    if text.endswith("Z"):
        candidates.insert(0, text[:-1] + "+00:00")

    for candidate in candidates:
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


def format_date_ddmmyyyy(raw_value) -> str:
    dt = parse_datetime_value(raw_value)
    if dt is None:
        return "onbekende-datum"
    return dt.strftime("%d-%m-%Y")


def resolve_entry_date(item, evaluation=None):
    date_keys = (
        "submitted_at",
        "evaluated_at",
        "evaluation_date",
        "graded_at",
        "reviewed_at",
        "assessed_at",
        "created_at",
        "updated_at",
        "date",
    )

    for source in (evaluation, item):
        if not isinstance(source, dict):
            continue
        for key in date_keys:
            value = source.get(key)
            parsed = parse_datetime_value(value)
            if parsed is not None:
                return parsed

    return None


def request_with_retries(url, headers, params=None, max_attempts=3):
    attempt = 0
    while attempt < max_attempts:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=20)
            if response.status_code == 401:
                return "TOKEN_EXPIRED"
            if response.status_code == 404:
                return response
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            attempt += 1
            print(f"Request failed ({attempt}/{max_attempts}): {exc}")
            if attempt < max_attempts:
                print("Retrying in 5 seconds...")
                time.sleep(5)
            else:
                print("Maximum retries reached.")
                return None


def get_bearer_token(cli_token: str) -> str:
    token = (cli_token or "").strip() or (BEARER_TOKEN or "").strip()
    if token:
        return token
    return input("Enter Bearer token: ").strip()


def get_section_id(cli_section_id: str) -> str:
    section = (cli_section_id or "").strip() or (SECTION_ID or "").strip()
    if section:
        return section
    return input("Enter section_id: ").strip()


def headers_for_token(token):
    return {
        "accept": "*/*",
        "authorization": f"Bearer {token}",
        "user-agent": "Mozilla/5.0",
    }


def get_shared_collections(token):
    headers = headers_for_token(token)
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
                "per_page": PER_PAGE,
            },
        )

        if response in (None, "TOKEN_EXPIRED"):
            return response

        data = response.json()
        if not data:
            break

        all_items.extend(data)

        if len(data) < PER_PAGE:
            break

        page += 1

    return all_items


def extract_students_from_shared(shared_items):
    students = {}
    for item in shared_items:
        inviter = item.get("inviter")
        if not inviter or inviter.get("current_role") != "student":
            continue

        name = inviter.get("name")
        portfolio_id = item.get("portfolio_id")
        if not name or not portfolio_id:
            continue

        if name not in students:
            students[name] = {
                "student_id": inviter.get("id"),
                "portfolio_ids": set(),
            }
        students[name]["portfolio_ids"].add(portfolio_id)

    return students


def get_students_from_section(token, section_id):
    headers = headers_for_token(token)
    students = {}
    page = 1

    while True:
        response = request_with_retries(
            f"{BASE_URL}/dashboard",
            headers,
            params={
                "section_id": section_id,
                "page": page,
                "per_page": PER_PAGE,
            },
        )

        if response in (None, "TOKEN_EXPIRED"):
            return response

        data = response.json()
        page_students = data.get("students", [])
        if not page_students:
            break

        for student in page_students:
            name = student.get("name")
            portfolio_id = student.get("portfolio_id")
            if not name or not portfolio_id:
                continue

            if name not in students:
                students[name] = {
                    "student_id": student.get("id"),
                    "portfolio_ids": set(),
                }
            students[name]["portfolio_ids"].add(portfolio_id)

        if len(page_students) < PER_PAGE:
            break

        page += 1

    return students


def get_goals(token, portfolio_id):
    response = request_with_retries(
        f"{BASE_URL}/portfolios/{portfolio_id}/goals",
        headers_for_token(token),
        params={"page": 1, "per_page": PER_PAGE},
    )
    if response in (None, "TOKEN_EXPIRED"):
        return response
    if isinstance(response, requests.Response) and response.status_code == 404:
        return "NOT_FOUND"
    return response.json()


def get_feedback_items(token, portfolio_id, goal_id):
    headers = headers_for_token(token)
    items = []
    page = 1

    while True:
        response = request_with_retries(
            f"{BASE_URL}/portfolios/{portfolio_id}/goals/{goal_id}/feedback-items",
            headers,
            params={"page": page, "per_page": PER_PAGE},
        )

        if response in (None, "TOKEN_EXPIRED"):
            return response
        if isinstance(response, requests.Response) and response.status_code == 404:
            return "NOT_FOUND"

        data = response.json()
        if not data:
            break

        items.extend(data)
        if len(data) < PER_PAGE:
            break
        page += 1

    return items


def resolve_level_label(evaluation):
    if not isinstance(evaluation, dict):
        return None

    level_id = evaluation.get("level")
    if not level_id:
        return None

    for level_entry in evaluation.get("level_set", []):
        if level_entry.get("id") == level_id:
            label = level_entry.get("label")
            if label is not None:
                return str(label)

    return str(level_id)


def resolve_person_name(item, evaluation=None):
    candidates = [
        (item.get("user") or {}).get("name"),
        (item.get("author") or {}).get("name"),
        (item.get("creator") or {}).get("name"),
        (item.get("reviewer") or {}).get("name"),
        (item.get("evaluator") or {}).get("name"),
        item.get("user_name"),
        item.get("author_name"),
        item.get("creator_name"),
        item.get("reviewer_name"),
        item.get("evaluator_name"),
        (evaluation or {}).get("author_name"),
        (evaluation or {}).get("reviewer_name"),
    ]

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    role = item.get("role")
    if isinstance(role, str) and role.strip():
        return role.strip()

    return "onbekend"


def extract_text_content(item, evaluation=None):
    keys = [
        "comment",
        "comments",
        "body",
        "content",
        "text",
        "message",
        "description",
        "toelichting",
        "feedback",
        "reflection",
        "self_evaluation",
        "self_evaluation_text",
    ]

    values = []
    for source in (evaluation, item):
        if not isinstance(source, dict):
            continue
        for key in keys:
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                values.append(val.strip())

    # Keep order, remove duplicates.
    unique = []
    seen = set()
    for text in values:
        if text not in seen:
            seen.add(text)
            unique.append(text)

    return "\n\n".join(unique)


def extract_evidence_mentions(text):
    if not isinstance(text, str) or not text.strip():
        return []

    pattern = re.compile(
        r'data-id="(?P<id>\d+)"[^>]*data-label="(?P<label>[^"]+)"[^>]*data-mention-type="evidence"[^>]*data-portfolio-id="(?P<portfolio_id>\d+)"'
    )

    mentions = []
    seen = set()
    for match in pattern.finditer(text):
        mention = {
            "id": match.group("id"),
            "label": match.group("label"),
            "portfolio_id": match.group("portfolio_id"),
        }
        key = (mention["portfolio_id"], mention["id"])
        if key in seen:
            continue
        seen.add(key)
        mentions.append(mention)

    return mentions


def derive_extension(url, fallback=".bin"):
    try:
        path = urlparse(url).path
        suffix = Path(path).suffix
        if suffix and len(suffix) <= 10:
            return suffix
    except Exception:
        pass
    return fallback


def extension_from_content_type(content_type, fallback=".bin"):
    if not isinstance(content_type, str) or not content_type.strip():
        return fallback

    mime = content_type.split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(mime)
    if guessed == ".jpe":
        return ".jpg"
    return guessed or fallback


def filename_from_content_disposition(content_disposition):
    if not isinstance(content_disposition, str) or not content_disposition.strip():
        return None

    match_star = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, flags=re.IGNORECASE)
    if match_star:
        return sanitize_name(match_star.group(1))

    match_plain = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
    if match_plain:
        return sanitize_name(match_plain.group(1))

    return None


def gather_attachments(node):
    results = []
    seen = set()

    def maybe_add_attachment(obj):
        if not isinstance(obj, dict):
            return

        url = None
        for key in ("download_url", "file_url", "url", "href", "link"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("http"):
                url = value
                break

        if not url:
            return

        name = (
            obj.get("filename")
            or obj.get("name")
            or obj.get("original_filename")
            or obj.get("title")
            or ""
        )
        if isinstance(name, str):
            name = name.strip()
        else:
            name = ""

        key = (url, name)
        if key in seen:
            return
        seen.add(key)

        results.append({"url": url, "name": name})

    def walk(value):
        if isinstance(value, dict):
            maybe_add_attachment(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for entry in value:
                walk(entry)

    walk(node)
    return results


def resolve_download_url_from_json(payload):
    attachments = gather_attachments(payload)
    if attachments:
        return attachments[0]
    return None


def download_file(url, target_file: Path, token: str) -> bool:
    headers = {"authorization": f"Bearer {token}"}
    try:
        with requests.get(url, headers=headers, timeout=30, stream=True) as response:
            if response.status_code == 401:
                return False
            response.raise_for_status()
            with target_file.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        handle.write(chunk)
        return True
    except requests.exceptions.RequestException:
        return False


def download_evidence_mention(mention, target_dir: Path, evidence_nr: int, token: str) -> bool:
    label = sanitize_name(mention.get("label") or f"evidence-{mention.get('id')}")
    portfolio_id = mention.get("portfolio_id")
    evidence_id = mention.get("id")
    endpoint = f"{BASE_URL}/portfolios/{portfolio_id}/evidence/{evidence_id}"
    headers = {
        "authorization": f"Bearer {token}",
        "accept": "*/*",
        "user-agent": "Mozilla/5.0",
    }

    try:
        response = requests.get(endpoint, headers=headers, timeout=30, stream=True)
    except requests.exceptions.RequestException:
        response = None

    if response is None:
        fallback_name = sanitize_name(f"bewijsstuk {evidence_nr} - {label} - download-mislukt.txt")
        (target_dir / fallback_name).write_text(
            "Download mislukt\n"
            + f"Evidence id: {evidence_id}\n"
            + f"Portfolio id: {portfolio_id}\n"
            + f"Label: {mention.get('label')}\n"
            + f"Endpoint: {endpoint}\n",
            encoding="utf-8",
        )
        return False

    with response:
        if response.status_code >= 400:
            fallback_name = sanitize_name(f"bewijsstuk {evidence_nr} - {label} - metadata.txt")
            (target_dir / fallback_name).write_text(
                "Evidence niet gedownload\n"
                + f"HTTP status: {response.status_code}\n"
                + f"Evidence id: {evidence_id}\n"
                + f"Portfolio id: {portfolio_id}\n"
                + f"Label: {mention.get('label')}\n"
                + f"Endpoint: {endpoint}\n"
                + f"Response: {response.text[:2000]}\n",
                encoding="utf-8",
            )
            return False

        content_type = (response.headers.get("content-type") or "").lower()
        content_disposition = response.headers.get("content-disposition") or ""

        if "application/json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = None

            attachment = resolve_download_url_from_json(payload)
            if attachment and attachment.get("url"):
                original_name = sanitize_name(attachment.get("name") or label)
                if Path(original_name).suffix:
                    file_name = sanitize_name(f"bewijsstuk {evidence_nr} - {original_name}")
                else:
                    ext = derive_extension(attachment["url"])
                    file_name = sanitize_name(f"bewijsstuk {evidence_nr} - {original_name}{ext}")

                if download_file(attachment["url"], target_dir / file_name, token):
                    return True

            fallback_name = sanitize_name(f"bewijsstuk {evidence_nr} - {label} - metadata.txt")
            (target_dir / fallback_name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return False

        if "text/html" in content_type:
            fallback_name = sanitize_name(f"bewijsstuk {evidence_nr} - {label} - metadata.txt")
            (target_dir / fallback_name).write_text(
                "Evidence endpoint gaf HTML terug in plaats van een bestand.\n"
                + f"Endpoint: {endpoint}\n"
                + f"Content-Type: {content_type}\n",
                encoding="utf-8",
            )
            return False

        file_name = filename_from_content_disposition(content_disposition)
        if not file_name:
            ext = extension_from_content_type(content_type)
            file_name = sanitize_name(f"bewijsstuk {evidence_nr} - {label}{ext}")
        else:
            file_name = sanitize_name(f"bewijsstuk {evidence_nr} - {file_name}")

        with (target_dir / file_name).open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)
        return True


def choose_student(students):
    ordered_names = sorted(students.keys())
    if not ordered_names:
        return None

    width = max(2, len(str(len(ordered_names))))
    print("\nStap 1: kies een student")
    for idx, name in enumerate(ordered_names, start=1):
        print(f"- {idx:0{width}d} - {name}")

    while True:
        selection = input("Kies studentnummer of exacte naam: ").strip()
        if not selection:
            print("Geen keuze ingevoerd. Probeer opnieuw.")
            continue

        if selection.isdigit():
            index = int(selection)
            if 1 <= index <= len(ordered_names):
                return ordered_names[index - 1]

        if selection in students:
            return selection

        print("Student niet gevonden. Gebruik nummer of exacte naam.")


def ensure_mention_subdir(evaluation_dir: Path, mention, subdirs_by_key: dict):
    mention_key = (str(mention.get("portfolio_id")), str(mention.get("id")))
    if mention_key in subdirs_by_key:
        return subdirs_by_key[mention_key]

    label = sanitize_name(mention.get("label") or f"evidence-{mention.get('id')}")
    base_name = sanitize_name(f"gelinkt bestand {label}")
    candidate = evaluation_dir / base_name
    counter = 2
    while candidate.exists():
        candidate = evaluation_dir / sanitize_name(f"{base_name} ({counter})")
        counter += 1

    candidate.mkdir(parents=True, exist_ok=True)
    state = {
        "path": candidate,
        "downloaded": False,
        "reference_count": 0,
    }
    subdirs_by_key[mention_key] = state
    return state


def write_mention_reference_file(mention_dir_state, kind: str, author: str, date_text: str, text_payload: str):
    mention_dir_state["reference_count"] += 1
    reference_nr = mention_dir_state["reference_count"]
    file_name = sanitize_name(
        f"vermelding {reference_nr} - {kind} - {author} - {date_text}.txt"
    )
    (mention_dir_state["path"] / file_name).write_text(text_payload, encoding="utf-8")


def semester_folder_name(semester_start, semester_end) -> str:
    return sanitize_name(
        f"Semester {semester_start.strftime('%d-%m-%Y')} {semester_end.strftime('%d-%m-%Y')}"
    )


def make_export_structure(base_output_dir: Path, student_name: str, semester_start, semester_end):
    exports_root = base_output_dir / "exports"
    student_dir = exports_root / sanitize_name(student_name)
    semester_dir = student_dir / semester_folder_name(semester_start, semester_end)

    evaluations_dir = semester_dir / "Evaluaties"
    skills_dir = semester_dir / "Vaardigheden"
    products_dir = semester_dir / "Beroepsproducten"

    evaluations_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    products_dir.mkdir(parents=True, exist_ok=True)

    return {
        "exports_root": exports_root,
        "student_dir": student_dir,
        "semester_dir": semester_dir,
        "evaluations_dir": evaluations_dir,
        "skills_dir": skills_dir,
        "products_dir": products_dir,
    }


def build_evaluation_title(item, evaluation, goal_name):
    raw_title = None
    for source in (evaluation, item):
        if isinstance(source, dict):
            raw_title = (
                source.get("review_request_title")
                or source.get("title")
                or source.get("name")
            )
            if isinstance(raw_title, str) and raw_title.strip():
                break

    title = sanitize_name(raw_title or goal_name or "evaluatie")
    date_part = format_date_ddmmyyyy(resolve_entry_date(item, evaluation))
    return date_part, title


def date_in_semester(raw_value, semester_start, semester_end) -> bool:
    dt = parse_datetime_value(raw_value)
    if dt is None:
        return False
    return semester_start <= dt.date() <= semester_end


def event_kind(item, evaluation):
    item_type = (item.get("type") or "").strip().lower()
    role = (item.get("role") or "").strip().lower()

    if item_type == "criterion_evaluation":
        if role == "self":
            return "zelfevaluatie"
        return "beoordeling"

    return "opmerking"


def build_event_text(goal_name, item, evaluation):
    payload = {
        "goal": goal_name,
        "type": item.get("type"),
        "role": item.get("role"),
        "status": item.get("status"),
        "review_request_title": (evaluation or {}).get("review_request_title"),
        "review_request_scored": (evaluation or {}).get("review_request_scored"),
        "niveau": resolve_level_label(evaluation),
        "tekst": extract_text_content(item, evaluation),
    }

    # Compact en leesbaar, met ruwe context eronder voor volledigheid.
    lines = [
        f"Vaardigheid: {goal_name}",
        f"Type: {payload['type']}",
        f"Rol: {payload['role']}",
        f"Status: {payload['status']}",
        f"Reviewverzoek: {payload['review_request_title']}",
        f"Scored: {payload['review_request_scored']}",
        f"Niveau: {payload['niveau']}",
        "",
        "Tekst:",
        payload["tekst"] or "(geen tekst gevonden)",
        "",
        "Ruwe data:",
        json.dumps(item, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_skill_summaries(skill_entries, skills_root: Path):
    # Zorg voor vaste 1..10 mappen met code + naam.
    for idx, (goal_name, code) in enumerate(GOAL_COLUMNS, start=1):
        folder_name = sanitize_name(f"Vaardigheid {idx} - {code} - {goal_name}")
        skill_dir = skills_root / folder_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        summary_file = skill_dir / "overzicht.txt"
        entries = skill_entries.get(normalize_goal_name(goal_name), [])
        lines = [
            f"Vaardigheid: {goal_name}",
            f"Code: {code}",
            f"Aantal gekoppelde items: {len(entries)}",
            "",
        ]

        for entry in entries:
            lines.append(
                "- "
                + f"{entry['kind']} | {entry['date']} | {entry['author']} | "
                + f"{entry['evaluation_folder']}"
            )
            if entry.get("level"):
                lines.append(f"  Niveau: {entry['level']}")
            if entry.get("text_preview"):
                lines.append(f"  Tekst: {entry['text_preview']}")

        summary_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_beroepsproducten_overviews(product_entries, products_root: Path):
    software_realiseren_dir = products_root / "Software Realiseren"
    software_realiseren_dir.mkdir(parents=True, exist_ok=True)
    summary_file = software_realiseren_dir / "overzicht.txt"

    total_items = sum(len(entries) for entries in product_entries.values())
    lines = [
        "Beroepsproducten (alle niet-vaardigheidsdoelen)",
        f"Aantal gekoppelde items: {total_items}",
        "",
    ]

    for goal_name in sorted(product_entries.keys(), key=lambda x: x.lower()):
        entries = product_entries[goal_name]
        lines.append(f"Doel: {goal_name}")
        lines.append(f"Aantal items: {len(entries)}")
        for entry in entries:
            lines.append(
                "- "
                + f"{entry['kind']} | {entry['date']} | {entry['author']} | "
                + f"{entry['evaluation_folder']}"
            )
        lines.append("")

    summary_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_index_csv(index_rows, semester_dir: Path):
    index_file = semester_dir / "index.csv"
    with index_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "evaluatie_map",
                "vaardigheid",
                "code",
                "auteur",
                "datum",
                "type",
                "niveau",
                "aantal_bijlagen",
            ],
            delimiter=";",
        )
        writer.writeheader()
        for row in index_rows:
            writer.writerow(row)


def export_student_collection(
    token,
    student_name,
    student_data,
    structure,
    semester_start,
    semester_end,
):
    print("\nStap 2: exporteren van evaluaties, feedback, zelfevaluaties, beoordelingen en bewijzen...")

    overview_lines = [
        f"Student: {student_name}",
        f"Exportdatum: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
        f"Portfolio ids: {', '.join(str(pid) for pid in sorted(student_data['portfolio_ids']))}",
        f"Semester start: {semester_start.strftime('%d-%m-%Y')}",
        f"Semester eind: {semester_end.strftime('%d-%m-%Y')}",
        "",
        "Evaluaties:",
    ]

    skill_entries = {}
    product_entries = {}
    index_rows = []
    evaluation_dirs = {}
    evaluation_counter = 0

    evaluations_dir = structure["evaluations_dir"]
    skills_dir = structure["skills_dir"]
    products_dir = structure["products_dir"]
    semester_dir = structure["semester_dir"]

    for portfolio_id in sorted(student_data["portfolio_ids"]):
        goals = get_goals(token, portfolio_id)
        if goals == "TOKEN_EXPIRED":
            return "TOKEN_EXPIRED"
        if goals in (None, "NOT_FOUND"):
            continue

        for goal in goals:
            goal_id = goal.get("id")
            goal_name = str(goal.get("name") or "onbekende vaardigheid")
            goal_name_key = normalize_goal_name(goal_name)

            feedback_items = get_feedback_items(token, portfolio_id, goal_id)
            if feedback_items == "TOKEN_EXPIRED":
                return "TOKEN_EXPIRED"
            if feedback_items in (None, "NOT_FOUND"):
                continue

            for item_index, item in enumerate(feedback_items, start=1):
                evaluation = item.get("evaluation") if isinstance(item, dict) else None
                entry_date = resolve_entry_date(item, evaluation)
                if not date_in_semester(entry_date, semester_start, semester_end):
                    continue

                date_part, eval_title = build_evaluation_title(item, evaluation, goal_name)
                eval_key = f"{date_part}|{eval_title}"

                if eval_key not in evaluation_dirs:
                    evaluation_counter += 1
                    folder_name = sanitize_name(
                        f"Evaluatie {evaluation_counter} - {date_part} - {eval_title}"
                    )
                    eval_dir = evaluations_dir / folder_name
                    eval_dir.mkdir(parents=True, exist_ok=True)
                    evaluation_dirs[eval_key] = {
                        "folder_name": folder_name,
                        "path": eval_dir,
                        "comment_count": 0,
                        "evidence_count": 0,
                        "self_count": 0,
                        "assessment_count": 0,
                        "mention_subdirs": {},
                    }
                    overview_lines.append(f"- {folder_name}")
                else:
                    eval_dir = evaluation_dirs[eval_key]["path"]

                folder_name = evaluation_dirs[eval_key]["folder_name"]

                kind = event_kind(item, evaluation)
                author = sanitize_name(resolve_person_name(item, evaluation), fallback="onbekend")
                date_text = format_date_ddmmyyyy(resolve_entry_date(item, evaluation))
                level_text = sanitize_name(resolve_level_label(evaluation) or "", fallback="")

                text_payload = build_event_text(goal_name, item, evaluation)
                if kind == "opmerking":
                    evaluation_dirs[eval_key]["comment_count"] += 1
                    nr = evaluation_dirs[eval_key]["comment_count"]
                    event_file_name = sanitize_name(f"opmerking {nr} - {author} - {date_text}.txt")
                elif kind == "zelfevaluatie":
                    evaluation_dirs[eval_key]["self_count"] += 1
                    event_file_name = sanitize_name(f"zelfevaluatie - {author} - {date_text}.txt")
                else:
                    evaluation_dirs[eval_key]["assessment_count"] += 1
                    if level_text:
                        event_file_name = sanitize_name(
                            f"beoordeling - {author} - {date_text} - {level_text}.txt"
                        )
                    else:
                        event_file_name = sanitize_name(f"beoordeling - {author} - {date_text}.txt")

                event_file = eval_dir / event_file_name
                event_file.write_text(text_payload, encoding="utf-8")

                text_content = extract_text_content(item, evaluation)
                evidence_mentions = extract_evidence_mentions(text_content)
                skill_entries.setdefault(goal_name_key, []).append(
                    {
                        "kind": kind,
                        "date": date_text,
                        "author": author,
                        "level": resolve_level_label(evaluation),
                        "evaluation_folder": folder_name,
                        "text_preview": (text_content[:140] + "...") if len(text_content) > 140 else text_content,
                    }
                )

                attachments = gather_attachments(item)
                attachment_count = len(attachments)
                for attachment in attachments:
                    evaluation_dirs[eval_key]["evidence_count"] += 1
                    evidence_nr = evaluation_dirs[eval_key]["evidence_count"]

                    original_name = sanitize_name(attachment.get("name") or "")
                    if original_name and Path(original_name).suffix:
                        file_name = sanitize_name(f"bewijsstuk {evidence_nr} - {original_name}")
                    else:
                        ext = derive_extension(attachment.get("url", ""))
                        file_name = sanitize_name(f"bewijsstuk {evidence_nr}{ext}")

                    file_path = eval_dir / file_name
                    ok = download_file(attachment["url"], file_path, token)
                    if not ok:
                        fallback_name = sanitize_name(f"bewijsstuk {evidence_nr} - download-mislukt.txt")
                        (eval_dir / fallback_name).write_text(
                            "Download mislukt\n"
                            + f"URL: {attachment['url']}\n",
                            encoding="utf-8",
                        )

                for mention in evidence_mentions:
                    mention_dir_state = ensure_mention_subdir(
                        eval_dir,
                        mention,
                        evaluation_dirs[eval_key]["mention_subdirs"],
                    )
                    write_mention_reference_file(
                        mention_dir_state,
                        kind,
                        author,
                        date_text,
                        text_payload,
                    )
                    if not mention_dir_state["downloaded"]:
                        evaluation_dirs[eval_key]["evidence_count"] += 1
                        evidence_nr = evaluation_dirs[eval_key]["evidence_count"]
                        if download_evidence_mention(mention, mention_dir_state["path"], evidence_nr, token):
                            attachment_count += 1
                        mention_dir_state["downloaded"] = True

                # Maak extra maplabels zichtbaar in overzicht per vaardigheid.
                goal_code = GOAL_CODE_MAP.get(goal_name_key)
                if goal_code:
                    overview_lines.append(
                        f"  - vaardigheid {goal_code}: {goal_name} | {kind} | {author} | {date_text}"
                    )
                else:
                    overview_lines.append(
                        f"  - beroepsproduct: {goal_name} | {kind} | {author} | {date_text}"
                    )
                    product_entries.setdefault(goal_name, []).append(
                        {
                            "kind": kind,
                            "date": date_text,
                            "author": author,
                            "evaluation_folder": folder_name,
                        }
                    )

                index_rows.append(
                    {
                        "evaluatie_map": folder_name,
                        "vaardigheid": goal_name,
                        "code": goal_code or "",
                        "auteur": author,
                        "datum": date_text,
                        "type": kind,
                        "niveau": resolve_level_label(evaluation) or "",
                        "aantal_bijlagen": attachment_count,
                    }
                )

    overview_file = semester_dir / "overzicht.txt"
    overview_file.write_text("\n".join(overview_lines).rstrip() + "\n", encoding="utf-8")

    write_skill_summaries(skill_entries, skills_dir)
    write_beroepsproducten_overviews(product_entries, products_dir)
    write_index_csv(index_rows, semester_dir)
    return "OK"


def resolve_semester_bounds(student_name: str):
    student_start = COACH_STUDENT_START_DATES.get(student_name)
    semester_start = student_start or SEMESTER_START_DATE
    return semester_start, SEMESTER_END_DATE


def load_students(token, source, section_id):
    if source == "shared":
        shared = get_shared_collections(token)
        if shared in (None, "TOKEN_EXPIRED"):
            return shared
        return extract_students_from_shared(shared)

    section_students = get_students_from_section(token, section_id)
    if section_students == "TOKEN_EXPIRED":
        return "TOKEN_EXPIRED"
    if isinstance(section_students, dict) and section_students:
        return section_students

    print("Geen studenten via section gevonden, fallback naar shared collections.")
    shared = get_shared_collections(token)
    if shared in (None, "TOKEN_EXPIRED"):
        return shared
    return extract_students_from_shared(shared)


def main():
    args = parse_args()
    token = get_bearer_token(args.token)

    section_id = ""
    if args.students_source == "section":
        section_id = get_section_id(args.section_id)

    students = load_students(token, args.students_source, section_id)
    if students == "TOKEN_EXPIRED":
        print("Token expired. Start opnieuw met een geldig token.")
        return

    if not students:
        print("Geen studenten gevonden.")
        return

    selected_name = choose_student(students)
    if not selected_name:
        print("Geen student geselecteerd.")
        return

    output_base = Path(args.output_dir).resolve()
    output_base.mkdir(parents=True, exist_ok=True)

    semester_start, semester_end = resolve_semester_bounds(selected_name)
    structure = make_export_structure(output_base, selected_name, semester_start, semester_end)

    status = export_student_collection(
        token,
        selected_name,
        students[selected_name],
        structure,
        semester_start,
        semester_end,
    )
    if status == "TOKEN_EXPIRED":
        print("Token expired tijdens export.")
        return

    print("\nExport gereed")
    print(f"Map: {structure['semester_dir']}")
    print("Bestanden:")
    print("- overzicht.txt")
    print("- index.csv")
    print("- Evaluaties/Evaluatie N - datum - naam")
    print("- Vaardigheden/Vaardigheid 1..10 - code - naam")
    print("- Beroepsproducten/Software Realiseren")


if __name__ == "__main__":
    main()
