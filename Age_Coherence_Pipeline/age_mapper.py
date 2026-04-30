import argparse
import hashlib
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

# Attempt to import fcntl for POSIX locking (Linux/Mac); handle missing on Windows
try:
    import fcntl
except ImportError:
    fcntl = None

# -------------------------------------------------------------------------
# CONFIGURATION & LOGGING
# -------------------------------------------------------------------------
LOGGER = logging.getLogger(__name__)

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

# -------------------------------------------------------------------------
# PROMPTS & CLASSIFICATION LOGIC
# -------------------------------------------------------------------------
ALLOWED_CODES = {">18", ">17+", ">13", "<13", "<10", "NONE"}

CLASSIFY_PROMPT = (
    "You convert a single sentence about who may use an app or feature into a normalized age code.\n"
    "Use only the given text; do not guess beyond it.\n\n"
    "Allowed age codes (choose EXACTLY one):\n"
    '  ">18"    (minimum age 18 or older; e.g., "18+" or "not for under 18")\n'
    '  ">17+"   (minimum age 17 or older; e.g., "Mature 17+")\n'
    '  ">13"    (minimum age 13 or older; e.g., "13+" or "no under-13")\n'
    '  "<13"    (allows under-13; e.g., refers to "children/kids", or lacks a strict minimum age)\n'
    '  "<10"    (explicitly for young children under 10)\n'
    '  "NONE"   (no age guidance present in the sentence)\n\n'
    "Rules:\n"
    '  " "not for under 18" or "18+" � ">18".\n'
    '  " "17+" or "Mature 17+" � ">17+".\n'
    '  " "13+" or forbids under-13 � ">13".\n'
    '  " Clearly about children/under-13 (or no strict minimum but implies kids) � "<13".\n'
    '  " Specifically under 10 � "<10".\n'
    "If parent/guardian permission or supervision is required, set parent_permission_required = true.\n"
    "If a sentence excludes children/under-13 (e.g., 'not for children', 'not intended for kids'), choose \">13\".\n"
    "If no numbers, anchor on the audience group explicitly mentioned in the sentence even if mentioned "
    "negatively (\"not for minors\").\n\n"
    "RESPOND ONLY WITH JSON matching this schema (no extra words):\n"
    '{{"age_code": "one of >18, >17+, >13, <13, <10, NONE", '
    '"min_age": <integer or null>, '
    '"parent_permission_required": true/false, '
    '"reason": "short justification using only the sentence"}}\n\n'
    "Sentence: {sentence}\n"
)

JSON_FIX_PROMPT_SENTENCE = (
    "You will be given a model response that failed to produce JSON. Convert it into JSON"
    " matching this exact schema (no commentary):\n"
    '{{"age_code": "one of >18, >17+, >13, <13, <10, NONE", "min_age": <integer or null>, '
    '"parent_permission_required": true/false, "reason": "short justification"}}\n\n'
    "Original response:\n<<<\n{response}\n>>>\n"
)

def run_prompt(tokenizer, model, prompt: str, max_new_tokens: int) -> str:
    inputs = tokenizer(prompt, return_tensors="pt")
    inputs = {k: v.to(model.device) for k, v in inputs.items()}
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            eos_token_id=getattr(tokenizer, "eos_token_id", None),
        )
    generated = output[0][inputs["input_ids"].shape[-1]:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()

def extract_first_json_value(text: str) -> Optional[object]:
    text = text.strip()
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        if text[idx] in "[{":
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                return obj
            except json.JSONDecodeError:
                pass
        idx += 1
    return None

def coerce_min_age_from_code(age_code: str) -> Optional[int]:
    if age_code == ">18": return 18
    if age_code == ">17+": return 17
    if age_code == ">13": return 13
    return None

def normalize_result(raw: Dict, fallback_reason: str) -> Dict:
    age_code = (raw.get("age_code") or "").strip()
    if age_code not in ALLOWED_CODES:
        age_code = "NONE"

    parent_perm = raw.get("parent_permission_required")
    if not isinstance(parent_perm, bool):
        if isinstance(parent_perm, str):
            parent_perm = parent_perm.strip().lower() in {"true", "yes", "y", "1"}
        else:
            parent_perm = False

    min_age = raw.get("min_age")
    if not (isinstance(min_age, int) or min_age is None):
        min_age = None
    if min_age is None:
        min_age = coerce_min_age_from_code(age_code)

    reason = raw.get("reason")
    if not isinstance(reason, str):
        reason = fallback_reason

    return {
        "age_code": age_code,
        "min_age": min_age,
        "parent_permission_required": parent_perm,
        "reason": reason.strip() or fallback_reason,
    }

def force_json_object(
    response_text: str,
    tokenizer: AutoTokenizer,
    model: AutoModelForCausalLM,
    max_new_tokens: int,
) -> Optional[Dict]:
    fix_prompt = JSON_FIX_PROMPT_SENTENCE.format(response=response_text)
    fixed = run_prompt(tokenizer, model, fix_prompt, max_new_tokens)
    parsed = extract_first_json_value(fixed)
    return parsed if isinstance(parsed, dict) else None

def classify_sentence(
    sentence: str,
    tokenizer,
    model,
    max_new_tokens: int,
) -> Dict:
    clean_sentence = sentence.strip()
    if not clean_sentence:
        return {
            "sentence": sentence,
            "age_code": "NONE",
            "min_age": None,
            "parent_permission_required": False,
            "reason": "empty sentence",
            "raw_model_text": "",
        }

    prompt = CLASSIFY_PROMPT.format(sentence=clean_sentence)
    raw_text = run_prompt(tokenizer, model, prompt, max_new_tokens)
    obj = extract_first_json_value(raw_text)
    
    if obj is None or not isinstance(obj, dict):
        LOGGER.warning("Model did not return a JSON object. Attempting fix...")
        fixed = force_json_object(raw_text, tokenizer, model, max_new_tokens)
        if isinstance(fixed, dict):
            obj = fixed
        else:
            obj = {"age_code": "NONE", "reason": raw_text, "parent_permission_required": False}

    normalized = normalize_result(obj, fallback_reason=raw_text)
    return normalized

# -------------------------------------------------------------------------
# LOCKING & RESUME HELPERS
# -------------------------------------------------------------------------
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
        if ttl_seconds <= 0: return False
        try:
            age = time.time() - p.stat().st_mtime
            return age > ttl_seconds
        except Exception:
            return False

    for _ in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as f:
                f.write(json.dumps({"owner": owner, "ts": time.time(), "app_key": app_key_str}, ensure_ascii=False))
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
        if not lock_path.exists(): return
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
            json.dumps({"app_key": app_key_str, "owner": owner, "ts": time.time(), "records": count}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(done_path)
    except Exception:
        pass

# -------------------------------------------------------------------------
# FILE I/O HELPERS (JSONL)
# -------------------------------------------------------------------------
class OutputFileLock:
    """Exclusive lock for appending to JSONL safely across workers."""
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

def append_jsonl_records_safely(output_path: Path, records: List[Dict[str, Any]]) -> int:
    if not records:
        return 0
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")
    with OutputFileLock(lock_path):
        with output_path.open("a", encoding="utf-8") as out:
            for rec in records:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)

def load_jsonl(path: str) -> List[Dict]:
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError:
                    LOGGER.warning("Skipping invalid JSON line")
    return data

def group_records_by_app(records: List[Dict]) -> Dict[str, List[Dict]]:
    """Groups flat sentence records by app_id to facilitate per-app locking."""
    grouped = {}
    for rec in records:
        app_id = (rec.get("app_id") or rec.get("appId") or "UNKNOWN_ID").strip()
        app_name = (rec.get("app_name") or rec.get("title") or "UNKNOWN_NAME").strip()
        # Create a unique key for the app grouping
        key = f"id:{app_id}" 
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(rec)
    return grouped

# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qwen Sentence Age Classification (JSONL + Locking)")
    parser.add_argument("--input-json", default="relabeled_merged_final_Audience_target.jsonl", help="Input JSONL file.")
    parser.add_argument("--output-json", default="relabeled_sentence_age_codes.jsonl", help="Output JSONL file.")
    parser.add_argument("--model-name", default="Qwen/Qwen3-14B", help="Model ID")
    parser.add_argument("--device-map", default="auto", help="Device map")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--app-lock-dir", default="./_app_locks", help="Directory for locks")
    parser.add_argument("--app-done-dir", default="./_app_done", help="Directory for done markers")
    parser.add_argument("--lock-ttl-seconds", type=int, default=21600, help="Lock TTL")
    parser.add_argument("--app-timeout-seconds", type=int, default=600, help="Per-app timeout")
    parser.add_argument("--no-resume", action="store_true", help="Start fresh (ignore done files)")
    return parser.parse_args()

def load_model(model_name: str, device_map: str):
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype="auto",
        device_map=device_map,
    )
    return tokenizer, model

def main() -> None:
    args = parse_args()
    configure_logging()

    LOGGER.info("Loading records from %s...", args.input_json)
    records = load_jsonl(args.input_json)
    LOGGER.info("Loaded %d records.", len(records))

    # Group records by App so we can process one App at a time with locking
    app_groups = group_records_by_app(records)
    LOGGER.info("Grouped into %d unique apps.", len(app_groups))

    lock_dir = Path(args.app_lock_dir)
    done_dir = Path(args.app_done_dir)
    output_path = Path(args.output_json)
    owner = f"{socket.gethostname()}:{os.getpid()}"

    # If no-resume, we don't clear files automatically to avoid accidents with concurrent workers,
    # but we warn. Realistically, user should clear output/locks manually if they want a hard reset.
    if args.no_resume:
        LOGGER.warning("--no-resume flag set. Note: This script checks DONE files. If you want a full re-run, clear the done directory manually.")

    LOGGER.info("Loading model %s...", args.model_name)
    tokenizer, model = load_model(args.model_name, args.device_map)

    processed_apps = 0
    
    for app_key, app_records in app_groups.items():
        # 1. Check Done
        if not args.no_resume:
            _, done_path = _lock_file_paths(lock_dir, done_dir, app_key)
            if done_path.exists():
                LOGGER.info("Skipping %s (Done)", app_key)
                continue

        # 2. Acquire Lock
        acquired, lock_path, done_path = try_acquire_app_lock(
            lock_dir, done_dir, app_key, owner, args.lock_ttl_seconds
        )
        if not acquired:
            LOGGER.info("Skipping %s (Locked)", app_key)
            continue

        try:
            LOGGER.info("Processing %s (%d records)...", app_key, len(app_records))
            start_ts = time.time()
            modified_records = []
            
            # 3. Process records for this app
            for rec in app_records:
                # Check timeout
                if args.app_timeout_seconds > 0 and (time.time() - start_ts) > args.app_timeout_seconds:
                    LOGGER.warning("Timeout processing %s. Aborting app.", app_key)
                    modified_records = None # Signal abort
                    break

                # FILTER: Only process "Audience_target"
                stmt_label = rec.get("model_result", {}).get("statement_label")
                
                if stmt_label == "Audience_target":
                    # Extract sentence and classify
                    sentence_text = rec.get("sentence", "")
                    classification = classify_sentence(
                        sentence_text, 
                        tokenizer, 
                        model, 
                        args.max_new_tokens
                    )
                    
                    # UPDATE RECORD: Add new fields
                    rec["age_code"] = classification["age_code"]
                    rec["min_age"] = classification["min_age"]
                    rec["parent_permission_required"] = classification["parent_permission_required"]
                    rec["reason"] = classification["reason"]
                
                # If NOT Audience_target, we leave existing fields as-is (likely null) 
                # and still add to output to preserve file length/content per instructions.
                modified_records.append(rec)

            # 4. Write Output & Mark Done
            if modified_records is not None:
                append_jsonl_records_safely(output_path, modified_records)
                write_app_done(done_path, app_key, owner, len(modified_records))
                processed_apps += 1
                LOGGER.info("Finished %s. Appended %d records.", app_key, len(modified_records))

        finally:
            release_app_lock(lock_path, owner)

    LOGGER.info("All done. Processed %d apps in this run.", processed_apps)

if __name__ == "__main__":
    main()