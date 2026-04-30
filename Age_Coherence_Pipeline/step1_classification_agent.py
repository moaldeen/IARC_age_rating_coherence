# import argparse
# import json
# import logging
# import re
# import time
# from pathlib import Path
# from typing import Dict, Iterable, List, Optional

# import requests
# import torch
# from bs4 import BeautifulSoup
# from requests import Response, Session
# from transformers import AutoModelForCausalLM, AutoTokenizer


# LOGGER = logging.getLogger(__name__)

# DEFAULT_MODEL_NAME = "Qwen/Qwen3-14B"
# DEFAULT_INPUT_JSON = (
#     "new_age/gplay_enUS_top1000_games_with_external_documents_privacy_filtered.json"
# )
# DEFAULT_OUTPUT_JSON = "new_age/qwen_child_safety_statements.json"

# REQUEST_TIMEOUT_SEC = 25
# MAX_MODEL_INPUT_TOKENS = 800
# MODEL_CHUNK_OVERLAP = 120

# COOKIE_KEYWORDS = {
#     "cookie",
#     "cookies",
#     "consent",
#     "gdpr",
#     "advert",
#     "privacy settings",
# }

# SYSTEM_PROMPT = (
#     "You are a meticulous research assistant."
#     " Only return sentences copied verbatim from the provided excerpt."
#     " Focus exclusively on statements about children, age suitability, parental"
#     " guidance, or instructions directed to parents or guardians."
#     " Do not rely on prior knowledge or paraphrase the source."
#     " If the excerpt contains no relevant sentences, respond with ONLY the word"
#     " 'NONE'."
# )


# def parse_args() -> argparse.Namespace:
#     parser = argparse.ArgumentParser(description="Extract age guidance statements with Qwen")
#     parser.add_argument(
#         "--input-json",
#         default=DEFAULT_INPUT_JSON,
#         help="Path to the JSON file containing app metadata.",
#     )
#     parser.add_argument(
#         "--output-json",
#         default=DEFAULT_OUTPUT_JSON,
#         help="Destination JSON path for extracted statements.",
#     )
#     parser.add_argument(
#         "--model-name",
#         default=DEFAULT_MODEL_NAME,
#         help="Hugging Face model identifier for Qwen.",
#     )
#     parser.add_argument(
#         "--max-apps",
#         type=int,
#         default=None,
#         help="Optional limit on number of apps to process (debugging aid).",
#     )
#     parser.add_argument(
#         "--sleep-seconds",
#         type=float,
#         default=0.0,
#         help="Optional delay between external HTTP requests.",
#     )
#     return parser.parse_args()


# def configure_logging() -> None:
#     logging.basicConfig(
#         level=logging.INFO,
#         format="%(asctime)s %(levelname)s %(name)s: %(message)s",
#     )


# def load_apps(json_path: str) -> List[Dict]:
#     with open(json_path, "r", encoding="utf-8") as handle:
#         data = json.load(handle)
#     if not isinstance(data, list):
#         raise ValueError("Expected the input JSON to contain a list of app objects")
#     return data


# def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
#     seen = set()
#     result: List[str] = []
#     for item in items:
#         normalized = item.strip()
#         if not normalized:
#             continue
#         key = normalized.lower()
#         if key in seen:
#             continue
#         seen.add(key)
#         result.append(normalized)
#     return result


# def create_session() -> Session:
#     try:
#         import cloudscraper

#         session = cloudscraper.create_scraper(
#             browser={"browser": "chrome", "platform": "windows", "mobile": False}
#         )
#     except ImportError:
#         session = requests.Session()
#     session.headers.update(
#         {
#             "User-Agent": (
#                 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
#                 "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
#             ),
#             "Accept": (
#                 "text/html,application/xhtml+xml,application/xml;q=0.9," "image/avif,image/webp,image/apng,*/*;q=0.8"
#             ),
#             "Accept-Language": "en-US,en;q=0.9",
#             "Cache-Control": "no-cache",
#             "Pragma": "no-cache",
#             "Upgrade-Insecure-Requests": "1",
#             "Connection": "keep-alive",
#             "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
#             "sec-ch-ua-mobile": "?0",
#             "sec-ch-ua-platform": '"Windows"',
#             "Sec-Fetch-Dest": "document",
#             "Sec-Fetch-Mode": "navigate",
#             "Sec-Fetch-Site": "none",
#             "Sec-Fetch-User": "?1",
#         }
#     )
#     return session


# def fetch_url(url: str, session: Session, sleep_seconds: float = 0.0) -> Optional[Response]:
#     if not url:
#         return None
#     try:
#         from urllib.parse import urlparse

#         parsed = urlparse(url)
#         base_referrer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else None
#     except Exception:
#         base_referrer = None

#     attempt_headers: List[Dict[str, str]] = [
#         {},
#         {"Referer": "https://play.google.com/"},
#     ]
#     if base_referrer:
#         attempt_headers.append({"Referer": base_referrer})

#     for attempt, extra_headers in enumerate(attempt_headers, start=1):
#         try:
#             response = session.get(
#                 url,
#                 headers=extra_headers,
#                 timeout=REQUEST_TIMEOUT_SEC,
#                 allow_redirects=True,
#             )
#             if sleep_seconds:
#                 time.sleep(sleep_seconds)
#             if response.status_code == 403 and attempt < len(attempt_headers):
#                 LOGGER.warning(
#                     "403 response for %s (attempt %d/%d); retrying with adjusted headers",
#                     url,
#                     attempt,
#                     len(attempt_headers),
#                 )
#                 continue
#             if response.status_code != 200:
#                 LOGGER.warning("Non-200 response %s for %s", response.status_code, url)
#                 return None
#             content_type = response.headers.get("content-type", "").lower()
#             if "text/html" not in content_type:
#                 LOGGER.info("Skipping non-HTML content at %s (%s)", url, content_type)
#                 return None
#             return response
#         except requests.RequestException as exc:
#             LOGGER.warning("Request failed for %s: %s", url, exc)
#     return None


# def clean_html_text(html: str) -> str:
#     soup = BeautifulSoup(html, "html.parser")
#     for element in soup([
#         "script",
#         "style",
#         "noscript",
#         "template",
#         "svg",
#         "iframe",
#         "header",
#         "footer",
#         "nav",
#         "form",
#     ]):
#         element.decompose()

#     text_lines: List[str] = []
#     for fragment in soup.stripped_strings:
#         cleaned_fragment = re.sub(r"\s+", " ", fragment.strip())
#         if len(cleaned_fragment.split()) < 5:
#             continue
#         lowered = cleaned_fragment.lower()
#         if any(keyword in lowered for keyword in COOKIE_KEYWORDS):
#             continue
#         text_lines.append(cleaned_fragment)

#     return "\n".join(text_lines)


# def chunk_text(
#     text: str,
#     tokenizer: AutoTokenizer,
#     max_tokens: int = MAX_MODEL_INPUT_TOKENS,
#     overlap: int = MODEL_CHUNK_OVERLAP,
# ) -> List[str]:
#     tokens = tokenizer.encode(text, add_special_tokens=False)
#     if len(tokens) <= max_tokens:
#         return [text]

#     overlap = max(0, min(overlap, max_tokens - 1))
#     chunks: List[str] = []
#     start = 0
#     while start < len(tokens):
#         end = min(len(tokens), start + max_tokens)
#         chunk_ids = tokens[start:end]
#         chunk_text = tokenizer.decode(chunk_ids, skip_special_tokens=True)
#         chunks.append(chunk_text.strip())
#         if end == len(tokens):
#             break
#         start = max(0, end - overlap)
#     return chunks


# def apply_chat_template(
#     messages: List[Dict[str, str]], tokenizer: AutoTokenizer
# ) -> Dict[str, torch.Tensor]:
#     text = tokenizer.apply_chat_template(
#         messages,
#         tokenize=False,
#         add_generation_prompt=True,
#         enable_thinking=False,
#     )
#     return tokenizer([text], return_tensors="pt")


# def parse_model_response(response: str) -> List[str]:
#     cleaned_response = response.strip()
#     if not cleaned_response:
#         return []

#     lines = re.split(r"[\r\n]+", cleaned_response)
#     statements: List[str] = []
#     for line in lines:
#         normalized = line.strip().lstrip("-•·•").strip()
#         if not normalized:
#             continue
#         if normalized.upper() == "NONE":
#             return []
#         statements.append(normalized)
#     return statements


# def query_qwen(
#     tokenizer: AutoTokenizer,
#     model: AutoModelForCausalLM,
#     excerpt: str,
#     source_description: str,
# ) -> List[str]:
#     if not excerpt.strip():
#         return []

#     user_message = (
#         "You will receive an excerpt from {source}.\n"
#         "Instructions:\n"
#         "1. Identify sentences referencing children, teens, age restrictions,"
#         " parental guidance, or instructions for guardians.\n"
#         "2. Quote each sentence exactly as it appears.\n"
#         "3. Place each sentence on its own line.\n"
#         "4. If nothing is relevant, respond with ONLY 'NONE'.\n\n"
#         "Excerpt:\n"
#         "-----BEGIN EXCERPT-----\n"
#         "{excerpt}\n"
#         "-----END EXCERPT-----"
#     ).format(source=source_description, excerpt=excerpt)

#     messages = [
#         {"role": "system", "content": SYSTEM_PROMPT},
#         {"role": "user", "content": user_message},
#     ]

#     model_inputs = apply_chat_template(messages, tokenizer)
#     model_inputs = {k: v.to(model.device) for k, v in model_inputs.items()}

#     with torch.no_grad():
#         output = model.generate(
#             **model_inputs,
#             max_new_tokens=256,
#             do_sample=False,
#         )

#     generated_ids = output[0][model_inputs["input_ids"].shape[-1]:]
#     response_text = tokenizer.decode(generated_ids, skip_special_tokens=True)
#     return parse_model_response(response_text)


# def collect_statements(
#     source_text: str,
#     source_label: str,
#     tokenizer: AutoTokenizer,
#     model: AutoModelForCausalLM,
# ) -> List[str]:
#     if not source_text:
#         return []

#     normalized_text = re.sub(r"\s+", " ", source_text.strip())
#     if not normalized_text:
#         return []

#     chunks = chunk_text(normalized_text, tokenizer)
#     extracted: List[str] = []
#     for index, chunk in enumerate(chunks, start=1):
#         chunk_label = f"{source_label} (chunk {index}/{len(chunks)})" if len(chunks) > 1 else source_label
#         statements = query_qwen(tokenizer, model, chunk, chunk_label)
#         extracted.extend(statements)
#     return dedupe_preserve_order(extracted)


# def main() -> None:
#     args = parse_args()
#     configure_logging()

#     LOGGER.info("Loading app metadata from %s", args.input_json)
#     apps = load_apps(args.input_json)
#     if args.max_apps is not None:
#         apps = apps[: max(0, args.max_apps)]
#     LOGGER.info("Loaded %d apps", len(apps))

#     LOGGER.info("Loading model %s", args.model_name)
#     tokenizer = AutoTokenizer.from_pretrained(args.model_name)
#     model = AutoModelForCausalLM.from_pretrained(
#         args.model_name,
#         torch_dtype="auto",
#         device_map="auto",
#     )

#     session = create_session()

#     output_path = Path(args.output_json)
#     output_path.parent.mkdir(parents=True, exist_ok=True)

#     results: List[Dict[str, object]] = []

#     for idx, app in enumerate(apps, start=1):
#         title = app.get("title") or app.get("appId") or "UNKNOWN"
#         LOGGER.info("Processing app %d/%d: %s", idx, len(apps), title)

#         description_text = app.get("description", "")
#         description_statements = collect_statements(
#             description_text,
#             "the Google Play store description",
#             tokenizer,
#             model,
#         )

#         privacy_text = ""
#         privacy_url = app.get("privacyPolicy")
#         if privacy_url:
#             response = fetch_url(privacy_url, session, args.sleep_seconds)
#             if response is not None:
#                 privacy_text = clean_html_text(response.text)
#         privacy_statements = collect_statements(
#             privacy_text,
#             "the privacy policy",
#             tokenizer,
#             model,
#         )

#         external_statements: List[str] = []
#         external_link_details: List[Dict[str, object]] = []
#         for link_idx, url in enumerate(app.get("external_documents", []) or [], start=1):
#             response = fetch_url(url, session, args.sleep_seconds)
#             if response is None:
#                 external_link_details.append({"url": url, "statements": []})
#                 continue
#             page_text = clean_html_text(response.text)
#             label = f"external link #{link_idx}"
#             statements = collect_statements(page_text, label, tokenizer, model)
#             external_statements.extend(statements)
#             external_link_details.append({"url": url, "statements": statements})
#         external_statements = dedupe_preserve_order(external_statements)

#         results.append(
#             {
#                 "app_name": title,
#                 "age_rating": app.get("contentRating", ""),
#                 "description_statements": description_statements,
#                 "privacy_policy_statements": privacy_statements,
#                 "external_links_statements": external_statements,
#                 "privacy_policy_url": privacy_url,
#                 "external_links": external_link_details,
#             }
#         )

#     with output_path.open("w", encoding="utf-8") as output_file:
#         json.dump(results, output_file, ensure_ascii=False, indent=2)

#     LOGGER.info("Finished writing results to %s", output_path)


# if __name__ == "__main__":
#     main()

import argparse
import hashlib
import json
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Set

import requests
import torch
from bs4 import BeautifulSoup
from requests import Response, Session
from transformers import AutoModelForCausalLM, AutoTokenizer

try:
    import fcntl
except ImportError:
    fcntl = None


LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "Qwen/Qwen3-14B"
DEFAULT_INPUT_JSON = (
    "/home/mshujaa/Desktop/mshujaa/llava_hugingface/XRZOO/new_age/family_apps_strict_enriched.json"
)
DEFAULT_OUTPUT_JSON = "new_age/qwen_child_safety_statements_family.json"

REQUEST_TIMEOUT_SEC = 25
MAX_MODEL_INPUT_TOKENS = 800
MODEL_CHUNK_OVERLAP = 120

COOKIE_KEYWORDS = {
    "cookie",
    "cookies",
    "consent",
    "gdpr",
    "advert",
    "privacy settings",
}

SYSTEM_PROMPT = (
    "You are a meticulous research assistant."
    " Only return sentences copied verbatim from the provided excerpt."
    " Focus exclusively on statements about children, age suitability, parental"
    " guidance, or instructions directed to parents or guardians."
    " Do not rely on prior knowledge or paraphrase the source."
    " If the excerpt contains no relevant sentences, respond with ONLY the word"
    " 'NONE'."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract age guidance statements with Qwen (resumable)")
    parser.add_argument(
        "--input-json",
        default=DEFAULT_INPUT_JSON,
        help="Path to the JSON file containing app metadata.",
    )
    parser.add_argument(
        "--output-json",
        default=DEFAULT_OUTPUT_JSON,
        help="Destination JSON path for extracted statements.",
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Hugging Face model identifier for Qwen.",
    )
    parser.add_argument(
        "--max-apps",
        type=int,
        default=None,
        help="Optional limit on number of apps to process (debugging aid).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional delay between external HTTP requests.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Disable resume behavior and overwrite output from scratch.",
    )
    parser.add_argument(
        "--app-lock-dir",
        default="new_age/_app_locks_qwen_prediction_final",
        help="Directory for per-app lock files (multi-worker safe).",
    )
    parser.add_argument(
        "--app-done-dir",
        default="new_age/_app_done_qwen_prediction_final",
        help="Directory for per-app done markers (resume across workers).",
    )
    parser.add_argument(
        "--lock-ttl-seconds",
        type=int,
        default=21600,
        help="Treat lock files older than this as stale (0 disables staleness).",
    )
    parser.add_argument(
        "--app-timeout-seconds",
        type=int,
        default=0,
        help="Optional per-app timeout in seconds (0 disables).",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_apps(json_path: str) -> List[Dict]:
    with open(json_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("Expected the input JSON to contain a list of app objects")
    return data


def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def create_session() -> Session:
    try:
        import cloudscraper

        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    except ImportError:
        session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
            "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    )
    return session


def fetch_url(url: str, session: Session, sleep_seconds: float = 0.0) -> Optional[Response]:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        base_referrer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else None
    except Exception:
        base_referrer = None

    attempt_headers: List[Dict[str, str]] = [
        {},
        {"Referer": "https://play.google.com/"},
    ]
    if base_referrer:
        attempt_headers.append({"Referer": base_referrer})

    for attempt, extra_headers in enumerate(attempt_headers, start=1):
        try:
            response = session.get(
                url,
                headers=extra_headers,
                timeout=REQUEST_TIMEOUT_SEC,
                allow_redirects=True,
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)
            if response.status_code == 403 and attempt < len(attempt_headers):
                LOGGER.warning(
                    "403 response for %s (attempt %d/%d); retrying with adjusted headers",
                    url,
                    attempt,
                    len(attempt_headers),
                )
                continue
            if response.status_code != 200:
                LOGGER.warning("Non-200 response %s for %s", response.status_code, url)
                return None
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type:
                LOGGER.info("Skipping non-HTML content at %s (%s)", url, content_type)
                return None
            return response
        except requests.RequestException as exc:
            LOGGER.warning("Request failed for %s: %s", url, exc)
    return None


def clean_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup([
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "iframe",
        "header",
        "footer",
        "nav",
        "form",
    ]):
        element.decompose()

    text_lines: List[str] = []
    for fragment in soup.stripped_strings:
        cleaned_fragment = re.sub(r"\s+", " ", fragment.strip())
        if len(cleaned_fragment.split()) < 5:
            continue
        lowered = cleaned_fragment.lower()
        if any(keyword in lowered for keyword in COOKIE_KEYWORDS):
            continue
        text_lines.append(cleaned_fragment)

    return "\n".join(text_lines)


def chunk_text(
    text: str,
    tokenizer: AutoTokenizer,
    max_tokens: int = MAX_MODEL_INPUT_TOKENS,
    overlap: int = MODEL_CHUNK_OVERLAP,
) -> List[str]:
    tokens = tokenizer.encode(text, add_special_tokens=False)
    if len(tokens) <= max_tokens:
        return [text]

    overlap = max(0, min(overlap, max_tokens - 1))
    chunks: List[str] = []
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + max_tokens)
        chunk_ids = tokens[start:end]
        chunk_text = tokenizer.decode(chunk_ids, skip_special_tokens=True)
        chunks.append(chunk_text.strip())
        if end == len(tokens):
            break
        start = max(0, end - overlap)
    return chunks


def apply_chat_template(
    messages: List[Dict[str, str]], tokenizer: AutoTokenizer
) -> Dict[str, torch.Tensor]:
    text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    return tokenizer([text], return_tensors="pt")


def parse_model_response(response: str) -> List[str]:
    cleaned_response = response.strip()
    if not cleaned_response:
        return []

    lines = re.split(r"[\r\n]+", cleaned_response)
    statements: List[str] = []
    for line in lines:
        normalized = line.strip().lstrip("-•·•").strip()
        if not normalized:
            continue
        if normalized.upper() == "NONE":
            return []
        statements.append(normalized)
    return statements


def query_qwen(
    tokenizer: AutoTokenizer,
    model: AutoModelForCausalLM,
    excerpt: str,
    source_description: str,
) -> List[str]:
    if not excerpt.strip():
        return []

    user_message = (
        "You will receive an excerpt from {source}.\n"
        "Instructions:\n"
        "1. Identify sentences referencing children, teens, age restrictions,"
        " parental guidance, or instructions for guardians.\n"
        "2. Quote each sentence exactly as it appears.\n"
        "3. Place each sentence on its own line.\n"
        "4. If nothing is relevant, respond with ONLY 'NONE'.\n\n"
        "Excerpt:\n"
        "-----BEGIN EXCERPT-----\n"
        "{excerpt}\n"
        "-----END EXCERPT-----"
    ).format(source=source_description, excerpt=excerpt)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    model_inputs = apply_chat_template(messages, tokenizer)
    model_inputs = {k: v.to(model.device) for k, v in model_inputs.items()}

    with torch.no_grad():
        output = model.generate(
            **model_inputs,
            max_new_tokens=256,
            do_sample=False,
        )

    generated_ids = output[0][model_inputs["input_ids"].shape[-1]:]
    response_text = tokenizer.decode(generated_ids, skip_special_tokens=True)
    return parse_model_response(response_text)


def collect_statements(
    source_text: str,
    source_label: str,
    tokenizer: AutoTokenizer,
    model: AutoModelForCausalLM,
) -> List[str]:
    if not source_text:
        return []

    normalized_text = re.sub(r"\s+", " ", source_text.strip())
    if not normalized_text:
        return []

    chunks = chunk_text(normalized_text, tokenizer)
    extracted: List[str] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_label = f"{source_label} (chunk {index}/{len(chunks)})" if len(chunks) > 1 else source_label
        statements = query_qwen(tokenizer, model, chunk, chunk_label)
        extracted.extend(statements)
    return dedupe_preserve_order(extracted)


# ---------- Resume helpers ----------

def atomic_write_json(path: Path, data: object) -> None:
    """Write JSON atomically: write to temp and then replace the target."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def read_existing_results(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        LOGGER.warning("Existing output is not a list; ignoring.")
        return []
    except Exception as exc:
        LOGGER.warning("Failed to read existing output (%s). Starting fresh.", exc)
        return []


def build_processed_keyset(existing_results: List[Dict]) -> Set[Tuple[str, str]]:
    """
    Build a set of processed keys:
      ('id', app_id) is preferred if present; otherwise ('name', app_name).

    This makes the run resumable even if older runs lack 'app_id'.
    """
    processed: Set[Tuple[str, str]] = set()
    for r in existing_results:
        app_id = r.get("app_id")
        app_name = r.get("app_name")
        if isinstance(app_id, str) and app_id.strip():
            processed.add(("id", app_id.strip()))
        elif isinstance(app_name, str) and app_name.strip():
            processed.add(("name", app_name.strip()))
    return processed


def make_app_key(app: Dict) -> Tuple[str, str]:
    """
    Construct the resume key for an app: prefer appId; otherwise the display name.
    """
    app_id = (app.get("appId") or "").strip()
    if app_id:
        return ("id", app_id)
    display_name = (app.get("title") or app.get("appId") or "UNKNOWN").strip()
    return ("name", display_name)


def make_app_key_str(app: Dict) -> str:
    key_type, key_value = make_app_key(app)
    return f"{key_type}:{key_value}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lock_file_paths(lock_dir: Path, done_dir: Path, app_key_str: str) -> Tuple[Path, Path]:
    h = sha256_text(app_key_str)
    return lock_dir / f"{h}.lock", done_dir / f"{h}.done"


def try_acquire_app_lock(
    lock_dir: Path,
    done_dir: Path,
    app_key_str: str,
    owner: str,
    ttl_seconds: int,
) -> Tuple[bool, Path, Path]:
    lock_path, done_path = _lock_file_paths(lock_dir, done_dir, app_key_str)
    lock_dir.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(parents=True, exist_ok=True)

    if done_path.exists():
        return False, lock_path, done_path

    def lock_is_stale(p: Path) -> bool:
        if ttl_seconds <= 0:
            return False
        try:
            age = time.time() - p.stat().st_mtime
            return age > ttl_seconds
        except Exception:
            return False

    for _ in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"owner": owner, "ts": time.time(), "app_key": app_key_str},
                        ensure_ascii=False,
                    )
                )
            return True, lock_path, done_path
        except FileExistsError:
            if lock_is_stale(lock_path):
                try:
                    lock_path.unlink()
                except Exception:
                    pass
                continue
            return False, lock_path, done_path
    return False, lock_path, done_path


def release_app_lock(lock_path: Path, owner: str) -> None:
    try:
        if not lock_path.exists():
            return
        txt = lock_path.read_text(encoding="utf-8").strip()
        obj = json.loads(txt) if txt.startswith("{") else {}
        if obj.get("owner") == owner:
            lock_path.unlink()
    except Exception:
        pass


def write_app_done(done_path: Path, app_key_str: str, owner: str, count: int) -> None:
    try:
        done_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = done_path.with_suffix(done_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"app_key": app_key_str, "owner": owner, "ts": time.time(), "records": count},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(done_path)
    except Exception:
        pass


class OutputFileLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fp = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fp = self.lock_path.open("a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.fp and fcntl is not None:
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
        finally:
            if self.fp:
                self.fp.close()
            self.fp = None


def append_result_record_safely(output_path: Path, record: Dict[str, Any]) -> int:
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")
    with OutputFileLock(lock_path):
        existing = read_existing_results(output_path)
        existing.append(record)
        atomic_write_json(output_path, existing)
        return len(existing)


def process_single_app(
    app: Dict,
    tokenizer: AutoTokenizer,
    model: AutoModelForCausalLM,
    session: Session,
    sleep_seconds: float,
) -> Dict[str, object]:
    title_display = (app.get("title") or app.get("appId") or "UNKNOWN")

    description_text = app.get("description", "")
    description_statements = collect_statements(
        description_text,
        "the Google Play store description",
        tokenizer,
        model,
    )

    privacy_text = ""
    privacy_url = app.get("privacyPolicy")
    if privacy_url:
        response = fetch_url(privacy_url, session, sleep_seconds)
        if response is not None:
            privacy_text = clean_html_text(response.text)
    privacy_statements = collect_statements(
        privacy_text,
        "the privacy policy",
        tokenizer,
        model,
    )

    external_statements: List[str] = []
    external_link_details: List[Dict[str, object]] = []
    for link_idx, url in enumerate(app.get("external_documents", []) or [], start=1):
        response = fetch_url(url, session, sleep_seconds)
        if response is None:
            external_link_details.append({"url": url, "statements": []})
            continue
        page_text = clean_html_text(response.text)
        label = f"external link #{link_idx}"
        statements = collect_statements(page_text, label, tokenizer, model)
        external_statements.extend(statements)
        external_link_details.append({"url": url, "statements": statements})
    external_statements = dedupe_preserve_order(external_statements)

    result_record: Dict[str, object] = {
        "app_id": app.get("appId", ""),
        "app_name": title_display,
        "age_rating": app.get("contentRating", ""),
        "description_statements": description_statements,
        "privacy_policy_statements": privacy_statements,
        "external_links_statements": external_statements,
        "privacy_policy_url": privacy_url,
        "external_links": external_link_details,
    }
    return result_record


def main() -> None:
    args = parse_args()
    configure_logging()

    LOGGER.info("Loading app metadata from %s", args.input_json)
    apps = load_apps(args.input_json)
    if args.max_apps is not None:
        apps = apps[: max(0, args.max_apps)]
    LOGGER.info("Loaded %d apps", len(apps))

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock_dir = Path(args.app_lock_dir)
    done_dir = Path(args.app_done_dir)
    owner = f"{socket.gethostname()}:{os.getpid()}"

    # Load existing results if resuming (backward-compatible with previous output-only resume)
    existing_results: List[Dict[str, Any]] = []
    processed_keys: Set[Tuple[str, str]] = set()
    if not args.no_resume:
        existing_results = read_existing_results(output_path)
        processed_keys = build_processed_keyset(existing_results)
        if existing_results:
            LOGGER.info(
                "Resume enabled: found %d existing results (%s).",
                len(existing_results),
                output_path,
            )
        else:
            LOGGER.info("Resume enabled: no existing results found; starting fresh.")
    else:
        LOGGER.warning(
            "--no-resume specified. DONE markers will be ignored; "
            "for a full reset across workers, clear output and done/lock dirs manually."
        )

    # Load model after we know we have work to do
    LOGGER.info("Loading model %s", args.model_name)
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        torch_dtype="auto",
        device_map="auto",
    )

    session = create_session()

    total = len(apps)
    for idx, app in enumerate(apps, start=1):
        key = make_app_key(app)
        app_key_str = make_app_key_str(app)
        title_display = (app.get("title") or app.get("appId") or "UNKNOWN")

        if key in processed_keys:
            LOGGER.info(
                "Skipping app %d/%d (already processed): %s",
                idx, total, title_display
            )
            continue

        if not args.no_resume:
            _, done_path = _lock_file_paths(lock_dir, done_dir, app_key_str)
            if done_path.exists():
                LOGGER.info("Skipping app %d/%d (done marker): %s", idx, total, title_display)
                processed_keys.add(key)
                continue

        acquired, lock_path, done_path = try_acquire_app_lock(
            lock_dir=lock_dir,
            done_dir=done_dir,
            app_key_str=app_key_str,
            owner=owner,
            ttl_seconds=args.lock_ttl_seconds,
        )
        if not acquired:
            LOGGER.info("Skipping app %d/%d (locked or done): %s", idx, total, title_display)
            continue

        try:
            LOGGER.info("Processing app %d/%d: %s", idx, total, title_display)
            app_start = time.time()
            result_record = process_single_app(
                app=app,
                tokenizer=tokenizer,
                model=model,
                session=session,
                sleep_seconds=args.sleep_seconds,
            )
            if args.app_timeout_seconds > 0 and (time.time() - app_start) > args.app_timeout_seconds:
                LOGGER.warning("Per-app timeout exceeded for %s; not marking done.", title_display)
                continue

            total_written = append_result_record_safely(output_path, result_record)
            write_app_done(done_path, app_key_str, owner, 1)
            processed_keys.add(key)
            LOGGER.info(
                "Wrote progress: %d results total -> %s",
                total_written, output_path
            )
        except Exception as exc:
            LOGGER.error("Failed to write progress JSON: %s", exc)
        finally:
            release_app_lock(lock_path, owner)

    final_results = read_existing_results(output_path)
    LOGGER.info("All done. Final results count: %d -> %s", len(final_results), output_path)


if __name__ == "__main__":
    main()
