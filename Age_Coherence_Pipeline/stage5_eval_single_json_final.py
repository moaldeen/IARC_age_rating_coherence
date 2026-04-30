#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Family-only Stage 5 evaluator.

This variant changes the decision priority:
if an app has any direct consistent audience match to the store rating,
that app is labeled "consistent" even if it also contains parent/enforcement
signals that would otherwise produce "consistent_override".
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


INPUT_PATH = Path(
    "Age_Coherence_Pipeline/examples/step5_input/age_maping_family.jsonl"
)
OUTPUT_PATH = Path(
    "Age_Coherence_Pipeline/examples/step5_output/coherent_analysis_family_table_only.jsonl"
)

STORE_MAP = {
    "Everyone": 0,
    "Everyone 10+": 10,
    "Teen": 13,
    "Mature 17+": 17,
    "Adult Only": 18,
    "Adults Only": 18,
    "AO": 18,
    "M": 17,
    "T": 13,
    "E10+": 10,
    "E": 0,
}


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            obj["_line"] = i
            records.append(obj)
    return records


def safe_int(x: Any) -> Optional[int]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        return None if math.isnan(x) else int(x)
    try:
        s = str(x).strip()
        if s.isdigit():
            return int(s)
    except Exception:
        return None
    return None


def normalize_quote(q: Optional[str]) -> Optional[str]:
    if not q:
        return None
    q = str(q).replace("\n", " ").strip()
    return re.sub(r"\s+", " ", q)


def get_extractions(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    mr = rec.get("model_result") or {}
    exts = mr.get("extractions") or []
    return exts if isinstance(exts, list) else []


def get_best_quote(rec: Dict[str, Any]) -> Optional[str]:
    for ext in get_extractions(rec):
        q = normalize_quote(ext.get("verbatim_quote"))
        if q:
            return q
    return normalize_quote(rec.get("sentence"))


def store_min_age(store_rating: Optional[str]) -> Optional[int]:
    if store_rating is None:
        return None
    return STORE_MAP.get(str(store_rating).strip())


def infer_age_from_age_code(code: Any) -> Optional[int]:
    if code is None:
        return None
    s = str(code).strip()
    if not s or s.upper() == "NONE":
        return None
    m = re.search(r"(\d{1,2})", s)
    if not m:
        return None
    return int(m.group(1))


def parse_plausible_ages(text: str) -> List[int]:
    nums = [int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", text or "")]
    return [n for n in nums if 6 <= n <= 25]


def infer_age_from_record(rec: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    v = safe_int(rec.get("min_age"))
    if v is not None:
        return v, "min_age"

    a = infer_age_from_age_code(rec.get("age_code"))
    if a is not None:
        return a, "age_code"

    nums: List[int] = []
    for ext in get_extractions(rec):
        for key in ("age_min_explicit", "age_max_explicit"):
            vv = safe_int(ext.get(key))
            if vv is not None:
                nums.append(vv)
    if nums:
        return max(nums), "explicit_fields"

    for ext in get_extractions(rec):
        if ext.get("mentions_minor_without_number") is True:
            return 13, "minor_mention_default_13"

    q = get_best_quote(rec) or ""
    found = parse_plausible_ages(q)
    if found:
        return max(found), "parsed_quote"

    return None, None


def has_parent_signal(rec: Dict[str, Any]) -> bool:
    if rec.get("parent_permission_required") is True:
        return True
    for ext in get_extractions(rec):
        if ext.get("mentions_parent_or_guardian") is True:
            return True
    return False


def has_enforcement_signal(rec: Dict[str, Any]) -> bool:
    for ext in get_extractions(rec):
        if ext.get("mentions_age_gate_or_restricted_version_or_limited_features") is True:
            return True
    return False


def pick_store_rating(recs: List[Dict[str, Any]]) -> Optional[str]:
    vals = [r.get("store_age_rating") for r in recs if r.get("store_age_rating") is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


def summarize_evidence(rec: Dict[str, Any], used_signal: str) -> Dict[str, Any]:
    return {
        "line": rec.get("_line"),
        "source": rec.get("source"),
        "label": rec.get("model_result", {}).get("statement_label"),
        "used_signal": used_signal,
        "age_code": rec.get("age_code"),
        "min_age": rec.get("min_age"),
        "parent_permission_required": rec.get("parent_permission_required"),
        "verbatim_quote": get_best_quote(rec),
    }


def stage5_single_output(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    app_records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        if "app_id" not in record:
            continue
        app_records[str(record["app_id"])].append(record)

    out_apps: List[Dict[str, Any]] = []

    for app_id, recs in app_records.items():
        names = [r.get("app_name") for r in recs if r.get("app_name")]
        app_name = Counter(names).most_common(1)[0][0] if names else None

        store_rating = pick_store_rating(recs)
        smin = store_min_age(store_rating)
        parent = any(has_parent_signal(r) for r in recs)
        enforce = any(has_enforcement_signal(r) for r in recs)

        infos: List[Tuple[int, str, Dict[str, Any]]] = []
        for rec in recs:
            if rec.get("model_result", {}).get("statement_label") != "Audience_target":
                continue
            age, signal = infer_age_from_record(rec)
            if age is not None and signal is not None:
                infos.append((age, signal, rec))

        exact_matches: List[Tuple[int, str, Dict[str, Any]]] = []
        inferred = None
        if smin is not None:
            exact_matches = [(age, sig, rec) for age, sig, rec in infos if int(age) == int(smin)]
        if exact_matches:
            inferred = smin
        else:
            inferred = max([age for age, _, _ in infos], default=None)

        inferred_sources = set()
        inferred_evidence: List[Dict[str, Any]] = []
        if inferred is not None:
            chosen_infos = exact_matches if exact_matches else [(age, sig, rec) for age, sig, rec in infos if age == inferred]
            for _, sig, rec in chosen_infos:
                inferred_sources.add(sig)
                inferred_evidence.append(summarize_evidence(rec, used_signal=sig))
            inferred_evidence = inferred_evidence[:10]

        override_used = None
        if inferred is None or smin is None:
            final = "insufficient_target_audience" if inferred is None else "insufficient_store_rating"
            match = None
            mismatch = None
            delta = None
        else:
            match = int(inferred) == int(smin)
            mismatch = not match
            delta = int(inferred) - int(smin)
            if match:
                final = "consistent"
            elif parent or enforce:
                final = "consistent_override"
                override_used = []
                if parent:
                    override_used.append("parent_or_guardian")
                if enforce:
                    override_used.append("age_gate_or_limited_features")
            else:
                final = "contradiction"

        override_evidence: List[Dict[str, Any]] = []
        if parent:
            for rec in recs:
                if has_parent_signal(rec):
                    override_evidence.append(
                        {
                            "line": rec.get("_line"),
                            "source": rec.get("source"),
                            "used_signal": "parent_or_guardian",
                            "verbatim_quote": get_best_quote(rec),
                        }
                    )
                    if len(override_evidence) >= 5:
                        break
        if enforce:
            for rec in recs:
                if has_enforcement_signal(rec):
                    override_evidence.append(
                        {
                            "line": rec.get("_line"),
                            "source": rec.get("source"),
                            "used_signal": "age_gate_or_limited_features",
                            "verbatim_quote": get_best_quote(rec),
                        }
                    )
                    if len(override_evidence) >= 10:
                        break

        out_apps.append(
            {
                "app_id": app_id,
                "app_name": app_name,
                "store_age_rating": store_rating,
                "store_min_age": smin,
                "inferred_min_age": int(inferred) if inferred is not None else None,
                "inferred_sources": sorted(inferred_sources) if inferred is not None else [],
                "has_parent_signal": bool(parent),
                "has_enforcement_signal": bool(enforce),
                "match": match,
                "mismatch": mismatch,
                "delta_inferred_minus_store": delta,
                "final_label": final,
                "override_used": override_used,
                "inferred_evidence": inferred_evidence,
                "override_evidence": override_evidence,
                "n_records": len(recs),
                "records": recs,
            }
        )

    out_apps = [row for row in out_apps if row.get("final_label") != "insufficient_target_audience"]
    out_apps.sort(key=lambda x: (x.get("app_name") or "", x.get("app_id") or ""))
    return out_apps


def main() -> None:
    records = read_jsonl(INPUT_PATH)
    app_rows = stage5_single_output(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(app_rows, f, ensure_ascii=False, indent=2)

    labels = Counter(x.get("final_label") for x in app_rows)
    print(f"[OK] Apps: {len(app_rows)}")
    print(f"[OK] Outcome counts: {dict(labels)}")
    print(f"[OK] Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
