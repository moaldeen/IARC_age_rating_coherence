#!/usr/bin/env python3
"""
qwen_relabel_sentence_age_codes.py

Relabels per-sentence age/audience restriction signals using Qwen and writes JSONL safely.

UPDATED for new input format (strings in lists):
- Automatically handles sources that are lists of strings (e.g. "privacy_policy_statements").
- Maps "age_rating" to "store_age_rating".
- Preserves exact output format (missing source fields like 'min_age' become null).

Install:
  pip install -U transformers accelerate torch

Run:
  python qwen_relabel_sentence_age_codes.py \
    --model Qwen/Qwen2.5-7B-Instruct \
    --input-json qwen_child_safety_statements.json \
    --output relabeled.jsonl
"""

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.generation.stopping_criteria import StoppingCriteria, StoppingCriteriaList

try:  # Optional; best-effort POSIX locking
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore


# -----------------------------
# OUR PROMPT (full; includes "none")
# -----------------------------
PROMPT_TEMPLATE = r"""
You are an extraction + classification engine. Your ONLY job is to detect and label **AUDIENCE RESTRICTION / AGE-RESTRICTION** language from the provided text.

READ THIS TWICE (ANTI-HALLUCINATION):
- DO NOT GUESS.
- DO NOT INFER.
- DO NOT HALLUCINATE.
- If it is NOT EXPLICITLY stated in the text, you MUST NOT claim it.
- You MUST copy evidence EXACTLY from the input (verbatim). No paraphrase in evidence.
- If you cannot quote it exactly, DO NOT include it.

IMPORTANT:
- POLICY_TEXT below is a SINGLE INPUT SENTENCE (not a full policy). Label it.
- Output MUST be STRICT JSON ONLY. No markdown. No code fences. No "Human:" / "User:" / extra text.

========================
DEFINITION (what counts)
========================
We are looking for statements that say the service/app is **NOT for everyone** because of age/audience limits OR that children/minors have restricted access or require parental consent.

You must label EACH sentence with EXACTLY ONE of the following scopes:

1) access_restriction
2) conditional_access_parental_consent
3) child_blocking_mechanism
4) targeting_not_directed
5) jurisdiction_majority
6) data_handling_only
7) none  (NONE means: irrelevant to age/audience restriction, parental consent, child gating, legal majority, or age-related data rules.)

CRITICAL:
- If unsure: label "none".
- Do NOT invent ages.

========================
TASK
========================
Label POLICY_TEXT (one sentence). Output one sentence_labels item.

========================
OUTPUT FORMAT (STRICT JSON ONLY)
========================
Return ONLY valid JSON. No markdown. No explanation.

{
  "has_any_audience_restriction": true/false,
  "sentence_labels": [
    {
      "sentence_verbatim": "...exact sentence from input...",
      "restriction_scope": "access_restriction" | "conditional_access_parental_consent" | "child_blocking_mechanism" | "targeting_not_directed" | "jurisdiction_majority" | "data_handling_only" | "none",
      "age_min_explicit": integer_or_null,
      "age_max_explicit": integer_or_null,
      "mentions_minor_without_number": true/false,
      "mentions_parent_or_guardian": true/false,
      "mentions_age_majority_or_legal_majority": true/false,
      "mentions_age_gate_or_restricted_version_or_limited_features": true/false
    }
  ]
}

NOW PROCESS THIS INPUT:

POLICY_TEXT:
<<<{POLICY_TEXT}>>>
""".strip()


def build_prompt(policy_text: str) -> str:
    return PROMPT_TEMPLATE.replace("{POLICY_TEXT}", policy_text)


# -----------------------------
# Robust JSON parsing (balanced braces)
# -----------------------------
def _iter_json_object_spans(text: str) -> Iterable[Tuple[int, int]]:
    """
    Yields (start, end_exclusive) spans of top-level JSON objects found in text
    using a balanced-brace scan that respects string literals.
    """
    if not text:
        return
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield start, j + 1
                        i = j + 1
                        break
            j += 1
        else:
            # no closing brace
            return


def try_parse_json(raw: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    raw = (raw or "").strip()
    if not raw:
        return None, "Empty model output."

    # First try strict.
    try:
        obj = json.loads(raw)
        return (obj if isinstance(obj, dict) else None), None
    except Exception:
        pass

    # Then try balanced JSON objects inside.
    for a, b in _iter_json_object_spans(raw):
        candidate = raw[a:b].strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj, None
        except Exception:
            continue

    return None, "No valid JSON object found in model output."


# -----------------------------
# Enforce NONE/OTHER behavior (never drop)
# -----------------------------
ALLOWED_SCOPES = {
    "access_restriction",
    "conditional_access_parental_consent",
    "child_blocking_mechanism",
    "targeting_not_directed",
    "jurisdiction_majority",
    "data_handling_only",
    "none",
}

SCOPE_PRIORITY = [
    "access_restriction",
    "conditional_access_parental_consent",
    "child_blocking_mechanism",
    "jurisdiction_majority",
    "targeting_not_directed",
    "data_handling_only",
    "none",
]

NEW_TO_OLD = {
    "access_restriction": "Audience_target",
    "targeting_not_directed": "Audience_target",
    "conditional_access_parental_consent": "conditional_access_parental_consent",
    "data_handling_only": "child_enforcement",
    "child_blocking_mechanism": "child_enforcement",
    "jurisdiction_majority": "none",
    "none": "none",
}


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else False


def normalize_sentence_label(item: Dict[str, Any], fallback_sentence: str) -> Dict[str, Any]:
    sentence = item.get("sentence_verbatim")
    if not isinstance(sentence, str) or not sentence.strip():
        sentence = fallback_sentence

    scope = item.get("restriction_scope")
    if scope not in ALLOWED_SCOPES:
        scope = "none"

    age_min = item.get("age_min_explicit")
    age_max = item.get("age_max_explicit")
    age_min = age_min if isinstance(age_min, int) else None
    age_max = age_max if isinstance(age_max, int) else None

    return {
        "sentence_verbatim": sentence,
        "restriction_scope": scope,
        "age_min_explicit": age_min,
        "age_max_explicit": age_max,
        "mentions_minor_without_number": _bool(item.get("mentions_minor_without_number")),
        "mentions_parent_or_guardian": _bool(item.get("mentions_parent_or_guardian")),
        "mentions_age_majority_or_legal_majority": _bool(item.get("mentions_age_majority_or_legal_majority")),
        "mentions_age_gate_or_restricted_version_or_limited_features": _bool(
            item.get("mentions_age_gate_or_restricted_version_or_limited_features")
        ),
    }


def compute_statement_label(sentence_labels: List[Dict[str, Any]]) -> str:
    scopes = {sl.get("restriction_scope", "none") for sl in sentence_labels}
    for s in SCOPE_PRIORITY:
        if s in scopes and s != "none":
            return s
    return "none"


def normalize_label(label: Any) -> str:
    if isinstance(label, str) and label in NEW_TO_OLD:
        return NEW_TO_OLD[label]
    return "none"


def apply_legacy_normalization(model_result: Dict[str, Any]) -> Dict[str, Any]:
    statement_label = normalize_label(model_result.get("statement_label"))
    model_result["statement_label"] = statement_label
    model_result["normalized_statement_label"] = statement_label

    sentence_labels = model_result.get("sentence_labels")
    if isinstance(sentence_labels, list):
        for item in sentence_labels:
            if not isinstance(item, dict):
                continue
            normalized_scope = normalize_label(item.get("restriction_scope"))
            item["restriction_scope"] = normalized_scope
            item["normalized_restriction_scope"] = normalized_scope

    extractions = model_result.get("extractions")
    if isinstance(extractions, list):
        for item in extractions:
            if not isinstance(item, dict):
                continue
            item["restriction_scope"] = normalize_label(item.get("restriction_scope"))

    model_result["has_any_audience_restriction"] = statement_label != "none"
    return model_result


def enforce_result_structure(
    input_text: str, model_obj: Optional[Dict[str, Any]], parse_error: Optional[str]
) -> Dict[str, Any]:
    if not isinstance(model_obj, dict):
        model_obj = {}

    raw_sentence_labels = model_obj.get("sentence_labels")
    if not isinstance(raw_sentence_labels, list) or len(raw_sentence_labels) == 0:
        raw_sentence_labels = [{"sentence_verbatim": input_text, "restriction_scope": "none"}]

    normalized = [
        normalize_sentence_label(x if isinstance(x, dict) else {}, input_text) for x in raw_sentence_labels
    ]

    # Anti-hallucination enforcement: sentence_verbatim should be verbatim from input.
    # If not, downgrade to none and replace with input.
    for sl in normalized:
        sv = sl.get("sentence_verbatim", "")
        if isinstance(sv, str) and sv and (sv not in input_text):
            sl["sentence_verbatim"] = input_text
            if sl.get("restriction_scope") != "none":
                sl["restriction_scope"] = "none"
                parse_error = (parse_error + " | " if parse_error else "") + "Non-verbatim evidence; forced to none."

    has_any = any(sl["restriction_scope"] != "none" for sl in normalized)
    statement_label = compute_statement_label(normalized)

    extractions = []
    for sl in normalized:
        if sl["restriction_scope"] != "none":
            extractions.append(
                {
                    "verbatim_quote": sl["sentence_verbatim"],
                    "restriction_scope": sl["restriction_scope"],
                    "age_min_explicit": sl["age_min_explicit"],
                    "age_max_explicit": sl["age_max_explicit"],
                    "mentions_minor_without_number": sl["mentions_minor_without_number"],
                    "mentions_parent_or_guardian": sl["mentions_parent_or_guardian"],
                    "mentions_age_majority_or_legal_majority": sl["mentions_age_majority_or_legal_majority"],
                    "mentions_age_gate_or_restricted_version_or_limited_features": sl[
                        "mentions_age_gate_or_restricted_version_or_limited_features"
                    ],
                }
            )

    out = {
        "statement_label": statement_label,
        "has_any_audience_restriction": has_any,
        "sentence_labels": normalized,
        "extractions": extractions,
    }
    if parse_error:
        out["_error"] = parse_error
    return out


# -----------------------------
# Stop runaway generations
# -----------------------------
class StopOnSubstrings(StoppingCriteria):
    def __init__(self, tokenizer, stop_strings: List[str], prompt_len: int, min_new_tokens: int = 0):
        super().__init__()
        self.tokenizer = tokenizer
        self.stop_strings = [s for s in stop_strings if s]
        self.prompt_len = int(prompt_len)
        self.min_new_tokens = int(min_new_tokens)

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor, **kwargs) -> bool:
        # input_ids shape: (batch, seq_len)
        seq_len = input_ids.shape[-1]
        new_len = seq_len - self.prompt_len
        if new_len < self.min_new_tokens:
            return False
        gen_ids = input_ids[0, self.prompt_len:]
        text = self.tokenizer.decode(gen_ids, skip_special_tokens=True)
        return any(ss in text for ss in self.stop_strings)


def truncate_at_stop_strings(text: str, stop_strings: List[str]) -> str:
    if not text:
        return text
    cut = None
    for s in stop_strings:
        if not s:
            continue
        idx = text.find(s)
        if idx != -1:
            cut = idx if cut is None else min(cut, idx)
    if cut is not None:
        return text[:cut].strip()
    return text.strip()


# -----------------------------
# Qwen runner
# -----------------------------
@dataclass
class QwenConfig:
    model_id: str
    max_new_tokens: int = 256
    temperature: float = 0.0
    top_p: float = 1.0
    stop_strings: Optional[List[str]] = None


class QwenRunner:
    def __init__(self, cfg: QwenConfig):
        self.cfg = cfg
        self.tokenizer = AutoTokenizer.from_pretrained(cfg.model_id, trust_remote_code=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            cfg.model_id,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            device_map="auto",
            trust_remote_code=True,
        )
        self.model.eval()

        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id

        if not self.cfg.stop_strings:
            self.cfg.stop_strings = ["\nHuman:", "\nUser:", "\nAssistant:", "```", "</think>"]

    @torch.inference_mode()
    def infer_raw(self, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
        prompt = build_prompt(text)

        messages = [
            {"role": "system", "content": "Return ONLY valid JSON. No markdown. No explanation."},
            {"role": "user", "content": prompt},
        ]

        if hasattr(self.tokenizer, "apply_chat_template"):
            enc = self.tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
            )
            if isinstance(enc, torch.Tensor):
                input_ids = enc
                attention_mask = None
            else:
                input_ids = enc["input_ids"]
                attention_mask = enc.get("attention_mask")
        else:
            enc2 = self.tokenizer(prompt, return_tensors="pt")
            input_ids = enc2["input_ids"]
            attention_mask = enc2.get("attention_mask")

        input_ids = input_ids.to(self.model.device)
        if attention_mask is not None:
            attention_mask = attention_mask.to(self.model.device)

        prompt_len = int(input_ids.shape[-1])
        stopping = StoppingCriteriaList(
            [StopOnSubstrings(self.tokenizer, self.cfg.stop_strings or [], prompt_len=prompt_len, min_new_tokens=10)]
        )

        gen_kwargs: Dict[str, Any] = dict(
            max_new_tokens=int(self.cfg.max_new_tokens),
            eos_token_id=self.tokenizer.eos_token_id,
            pad_token_id=self.tokenizer.pad_token_id,
            use_cache=True,
            stopping_criteria=stopping,
        )
        if self.cfg.temperature and self.cfg.temperature > 0.0:
            gen_kwargs.update(
                dict(
                    do_sample=True,
                    temperature=float(self.cfg.temperature),
                    top_p=float(self.cfg.top_p),
                )
            )
        else:
            gen_kwargs.update(dict(do_sample=False))

        if attention_mask is not None:
            gen_ids = self.model.generate(input_ids=input_ids, attention_mask=attention_mask, **gen_kwargs)
        else:
            gen_ids = self.model.generate(input_ids=input_ids, **gen_kwargs)

        new_tokens = gen_ids[0, prompt_len:]
        decoded = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
        decoded = truncate_at_stop_strings(decoded, self.cfg.stop_strings or [])

        parsed, err = try_parse_json(decoded)
        return parsed, err, decoded


# -----------------------------
# Input loading + iteration
# -----------------------------
def load_apps(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list of app objects.")
    return data


def safe_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def iter_sentence_objects(app: Dict[str, Any], sources: List[str]) -> Iterable[Tuple[str, int, Dict[str, Any]]]:
    """
    Yields sentence objects.
    Modified to handle list of strings (new format) by wrapping them in a dict.
    """
    for src in sources:
        items = safe_list(app.get(src))
        for i, item in enumerate(items):
            if isinstance(item, str):
                # New format: list of strings. Wrap to match old format.
                yield src, i, {"sentence": item}
            elif isinstance(item, dict):
                # Old format: list of dicts.
                yield src, i, item


# -----------------------------
# Resume + locking helpers
# -----------------------------
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def app_key_from_fields(app_id: Optional[Any], app_name: Optional[Any]) -> str:
    if isinstance(app_id, str) and app_id.strip():
        return "app_id:" + app_id.strip()
    if isinstance(app_name, str) and app_name.strip():
        return "app_name:" + app_name.strip()
    return "unknown:" + sha256_text(
        json.dumps({"app_id": app_id, "app_name": app_name}, sort_keys=True, ensure_ascii=False)
    )


def app_key_from_app(app: Dict[str, Any]) -> str:
    return app_key_from_fields(app.get("app_id") or app.get("appId"), app.get("app_name") or app.get("title"))


def sentence_key(app_id: Any, app_name: Any, source: str, index: int) -> str:
    return f"{app_key_from_fields(app_id, app_name)}|{source}|{index}"


def load_processed_keys(output_path: Path) -> Set[str]:
    processed: Set[str] = set()
    if not output_path.exists():
        return processed
    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            source = obj.get("source")
            idx = obj.get("sentence_index")
            if source is None or idx is None:
                continue
            try:
                key = sentence_key(obj.get("app_id"), obj.get("app_name"), str(source), int(idx))
                processed.add(key)
            except Exception:
                continue
    return processed


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


def _lock_file_paths(lock_dir: Path, done_dir: Path, app_key: str) -> Tuple[Path, Path]:
    h = sha256_text(app_key)
    return lock_dir / f"{h}.lock", done_dir / f"{h}.done"


def try_acquire_app_lock(
    lock_dir: Path,
    done_dir: Path,
    app_key: str,
    owner: str,
    ttl_seconds: int,
) -> Tuple[bool, Path, Path]:
    lock_path, done_path = _lock_file_paths(lock_dir, done_dir, app_key)
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
                f.write(json.dumps({"owner": owner, "ts": time.time(), "app_key": app_key}, ensure_ascii=False))
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
        txt = lock_path.read_text(encoding="utf-8").strip()
        obj = json.loads(txt) if txt.startswith("{") else {}
        if obj.get("owner") == owner:
            lock_path.unlink()
    except Exception:
        pass


def write_app_done(done_path: Path, app_key: str, owner: str, num_records: int) -> None:
    try:
        done_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = done_path.with_suffix(done_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"app_key": app_key, "owner": owner, "ts": time.time(), "records": num_records}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(done_path)
    except Exception:
        pass


def age_code_is_none(sent_obj: Dict[str, Any]) -> bool:
    age_code = sent_obj.get("age_code")
    return isinstance(age_code, str) and age_code.strip().upper() == "NONE"


# -----------------------------
# Main
# -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF model id e.g. Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument(
        "--input-json",
        default="qwen_child_safety_statements.json",
        help="Path to input JSON.",
    )
    ap.add_argument("--output", default="qwen_relabels.jsonl", help="Output JSONL (append/resume-safe).")
    ap.add_argument("--max-apps", type=int, default=None)
    ap.add_argument("--max-sentences", type=int, default=None, help="Stop after writing this many new sentences (durably written).")
    ap.add_argument(
        "--sources",
        default="privacy_policy_statements,description_statements,external_links_statements",
        help="Comma-separated sentence sources to include from each app object",
    )
    ap.add_argument("--max-new-tokens", type=int, default=256)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument(
        "--stop-strings",
        default=r"\nHuman:,\nUser:,\nAssistant:,```,</think>",
        help=r"Comma-separated stop strings (use literal \n sequences).",
    )
    ap.add_argument("--flush-every", type=int, default=25, help="Append to JSONL every N records for durability.")
    ap.add_argument("--apps-per-load", type=int, default=25, help="Reload model after this many processed apps.")
    ap.add_argument("--no-resume", action="store_true", help="Disable resume; overwrite output at startup.")
    ap.add_argument("--app-lock-dir", default="./_app_locks", help="Directory for per-app lock files.")
    ap.add_argument("--app-done-dir", default="./_app_done", help="Directory for per-app done markers.")
    ap.add_argument("--lock-ttl-seconds", type=int, default=6 * 60 * 60, help="Break stale app locks after this many seconds (0 disables).")
    ap.add_argument(
        "--app-timeout-seconds",
        type=int,
        default=10 * 60,
        help="Skip an app if processing exceeds this wall-clock time (0 disables).",
    )
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    def _decode_stop(s: str) -> str:
        # allow user to pass literal "\n"
        return s.encode("utf-8").decode("unicode_escape")

    stop_strings = [_decode_stop(s.strip()) for s in args.stop_strings.split(",") if s.strip()]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.no_resume:
        print("--no-resume specified: output will be overwritten; do not use with multiple workers.", file=sys.stderr)
        output_path.write_text("", encoding="utf-8")
        processed: Set[str] = set()
    else:
        processed = load_processed_keys(output_path)
        if processed:
            print(f"Resume enabled: found {len(processed)} previously processed sentences.", file=sys.stderr)

    apps = load_apps(args.input_json)
    if args.max_apps is not None:
        apps = apps[: max(0, args.max_apps)]

    lock_dir = Path(args.app_lock_dir)
    done_dir = Path(args.app_done_dir)
    owner = f"{socket.gethostname()}:{os.getpid()}"

    runner: Optional[QwenRunner] = None
    apps_since_reload = 0
    total_written = 0
    max_hit = False

    def ensure_runner() -> QwenRunner:
        nonlocal runner
        if runner is None:
            runner = QwenRunner(
                QwenConfig(
                    model_id=args.model,
                    max_new_tokens=args.max_new_tokens,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    stop_strings=stop_strings,
                )
            )
        return runner

    def release_runner() -> None:
        nonlocal runner, apps_since_reload
        if runner is not None:
            try:
                del runner
            except Exception:
                pass
            runner = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        apps_since_reload = 0

    for app_idx, app in enumerate(apps, start=1):
        app_key = app_key_from_app(app)
        _, done_path = _lock_file_paths(lock_dir, done_dir, app_key)
        if done_path.exists():
            print(f"Skipping app {app_idx}/{len(apps)} (done): {app_key}", file=sys.stderr)
            continue

        acquired, lock_path, done_path = try_acquire_app_lock(
            lock_dir=lock_dir,
            done_dir=done_dir,
            app_key=app_key,
            owner=owner,
            ttl_seconds=args.lock_ttl_seconds,
        )
        if not acquired:
            print(f"Skipping app {app_idx}/{len(apps)} (locked by another worker): {app_key}", file=sys.stderr)
            continue

        print(f"Processing app {app_idx}/{len(apps)}: {app_key}", file=sys.stderr)
        runner_instance = ensure_runner()

        # Per-app timeout (wall clock)
        app_start_ts = time.time()
        app_deadline_ts = (
            app_start_ts + args.app_timeout_seconds
            if args.app_timeout_seconds and args.app_timeout_seconds > 0
            else None
        )

        app_id = app.get("app_id")
        app_name = app.get("app_name")
        # Handle both old and new field names
        store_age_rating = app.get("store_age_rating") or app.get("age_rating")

        # Buffer for durability: write every N records; only then update processed/total_written.
        pending_records: List[Dict[str, Any]] = []
        pending_keys: List[str] = []
        app_written = 0
        completed_app = True
        timed_out = False

        def flush_pending() -> None:
            nonlocal total_written, app_written
            if not pending_records:
                return
            added = append_jsonl_records_safely(output_path, pending_records)
            # Only now mark processed.
            for k in pending_keys:
                processed.add(k)
            total_written += added
            app_written += added
            pending_records.clear()
            pending_keys.clear()

        try:
            for source_key, idx, sent_obj in iter_sentence_objects(app, sources):
                # Per-app timeout check
                if app_deadline_ts is not None and time.time() >= app_deadline_ts:
                    timed_out = True
                    completed_app = False
                    break

                if age_code_is_none(sent_obj):
                    continue

                # Enforce max-sentences based on DURABLY WRITTEN count + pending buffer
                if args.max_sentences is not None and (total_written + len(pending_records)) >= args.max_sentences:
                    max_hit = True
                    completed_app = False
                    break

                sent_key = sentence_key(app_id, app_name, source_key, idx)
                if sent_key in processed:
                    continue

                text = sent_obj.get("sentence")
                if not isinstance(text, str):
                    text = ""

                parsed, err, raw = runner_instance.infer_raw(text)
                enforced = apply_legacy_normalization(enforce_result_structure(text, parsed, err))

                record = {
                    "app_name": app_name,
                    "app_id": app_id,
                    "store_age_rating": store_age_rating,
                    "source": source_key,
                    "sentence_index": idx,
                    "age_code": sent_obj.get("age_code"),  # Will be None in new format
                    "min_age": sent_obj.get("min_age"),    # Will be None in new format
                    "parent_permission_required": sent_obj.get("parent_permission_required"), # Will be None
                    "sentence": text,
                    "model_result": enforced,
                }
                if err:
                    record["_raw_model_output"] = (raw or "")[:8000]

                pending_records.append(record)
                pending_keys.append(sent_key)

                if len(pending_records) >= max(1, args.flush_every):
                    flush_pending()

            # flush leftovers for this app
            flush_pending()

            if timed_out:
                print(f"Skipping app due to timeout (> {args.app_timeout_seconds}s): {app_key}", file=sys.stderr)

            print(f"App write: added {app_written} record(s) -> {output_path}", file=sys.stderr)

            if completed_app and (not max_hit):
                write_app_done(done_path, app_key=app_key, owner=owner, num_records=app_written)
                print(f"Marked app done: {app_key}", file=sys.stderr)

        finally:
            release_app_lock(lock_path, owner=owner)

        if max_hit:
            release_runner()
            break

        apps_since_reload += 1
        if apps_since_reload >= max(1, args.apps_per_load):
            release_runner()

    if max_hit:
        print(
            f"Reached --max-sentences={args.max_sentences}. Wrote {total_written} new records to {output_path}",
            file=sys.stderr,
        )
    else:
        print(f"Done. Wrote {total_written} new records to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
