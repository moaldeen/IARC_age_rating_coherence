#!/usr/bin/env python3
import argparse
import hashlib
import inspect
import json
import logging
import os
import random
import re
import socket
import time
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urlparse

LOGGER = logging.getLogger(__name__)

# Optional (Linux/macOS) file locking for safe shared output updates.
try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore

# -----------------------------
# Chain order (main 8 + misc)
# -----------------------------

MISC_SECTION = "Miscellaneous"

VIOLENCE_SECTION = "Violence, Blood, or Gory Images"
LANGUAGE_SECTION = "Language"
GAMBLING_SECTION = "Gambling Themes, Simulated Gambling, or Real Gambling"
DIGITAL_PURCHASES_SECTION = "Digital Purchases, Cash Convertible Rewards, or NFTs"
CRUDE_HUMOR_SECTION = "Crude Humor"

# Canonical name we use internally:
SEXUALITY_SECTION_CANONICAL = "Sexuality, Suggestiveness, or Dating Games"
# Some questionnaire JSONs use this alternate spelling:
SEXUALITY_SECTION_ALT = "Sexuality, Suggestiveness, or Dating game/images"

SECTION_ALIASES: Dict[str, str] = {
    SEXUALITY_SECTION_ALT: SEXUALITY_SECTION_CANONICAL,
}

MAIN_CHAIN_SECTIONS = [
    VIOLENCE_SECTION,
    SEXUALITY_SECTION_CANONICAL,
    LANGUAGE_SECTION,
    "Fear",
    GAMBLING_SECTION,
    "Controlled Substance",
    CRUDE_HUMOR_SECTION,
    DIGITAL_PURCHASES_SECTION,
]


def canonical_section_name(section: str) -> str:
    return SECTION_ALIASES.get(section, section)


# -----------------------------
# MASTER MERGED GUIDELINES (The "Brain")
# -----------------------------
# These will be dynamically injected into the Root Questions
# so NVILA understands the strict definitions in the single generic pass.

MASTER_GUIDELINES = {
    # --- VIOLENCE ROOT (Q1110) ---
    "1110": {
        "title": "Violence, Blood, and Gory Images - Consolidated Criteria",
        "content": [
            {
                "type": "general_text",
                "text": "Answer 'Yes' if the screenshot contains ANY of the following 4 categories:"
            },
            {
                "type": "tab",
                "heading": "1. Violence Against Humans",
                "text": "Characters that look human (even if pixelated/armored) or implied violence against human-crewed vehicles."
            },
            {
                "type": "tab",
                "heading": "2. Violence Against Non-Humans",
                "text": "Violence against robots, aliens, monsters, animals, or uncrewed drones/machines."
            },
            {
                "type": "tab",
                "heading": "3. Gory Images",
                "text": "Dismemberment, mutilation, exposed internal organs, or disturbing images of corpses (even without a visible violent act)."
            },
            {
                "type": "tab",
                "heading": "4. Blood",
                "text": "Sprays of blood, blood pools, or static bloodstains on surfaces/characters (even if unrelated to a specific act)."
            }
        ]
    },

    # --- GAMBLING ROOT (Q1056) ---
    "1056": {
        "title": "Gambling and Betting - Consolidated Criteria",
        "content": [
            {
                "type": "general_text",
                "text": "Answer 'Yes' if the screenshot contains ANY of the following 4 categories:"
            },
            {
                "type": "tab",
                "heading": "1. Gambling Themes",
                "text": "Imagery evoking casinos, betting parlors, or racetracks (cards, dice, chips, slot reels) even if non-playable."
            },
            {
                "type": "tab",
                "heading": "2. Playable Bingo",
                "text": "Interactive Bingo games."
            },
            {
                "type": "tab",
                "heading": "3. Casino/Lottery/Betting",
                "text": "Card games, board games, or digital machines typically played for money, or betting on races/sports."
            },
            {
                "type": "tab",
                "heading": "4. Gambling with Currency",
                "text": "Input screens for betting money/virtual currency ('Ante', 'Buy-in') or output screens showing monetary wins ('Jackpot', 'Cash Out')."
            }
        ]
    },

    # --- SEXUALITY ROOT (Q1353) ---
    "1353": {
        "title": "Sexuality, Nudity, and Dating - Consolidated Criteria",
        "content": [
            {
                "type": "general_text",
                "text": "Answer 'Yes' if the screenshot contains ANY of the following 5 categories:"
            },
            {
                "type": "tab",
                "heading": "1. Sexual Violence",
                "text": "Depictions or references to non-consensual sexual acts, rape, or sexual assault."
            },
            {
                "type": "tab",
                "heading": "2. Sexual Activity",
                "text": "Visible sex acts or implied/obscured sex acts (obvious action but blocked view)."
            },
            {
                "type": "tab",
                "heading": "3. Nudity/Revealing Attire",
                "text": "Genitalia, female breasts (with or without nipples), buttocks, or revealing outfits (bikinis, lingerie)."
            },
            {
                "type": "tab",
                "heading": "4. Suggestive Themes",
                "text": "Sexual innuendo, overtly sexual situations (e.g., pole dancing), or references to sex."
            },
            {
                "type": "tab",
                "heading": "5. Dating Games",
                "text": "Gameplay focused on dating, marriage, or romantic relationships."
            }
        ]
    },

    # --- LANGUAGE ROOT (Q892) ---
    "892": {
        "title": "Language and Profanity - Consolidated Criteria",
        "content": [
            {
                "type": "general_text",
                "text": "Answer 'Yes' if visible text contains ANY of the following:"
            },
            {
                "type": "tab",
                "heading": "1. Minor Profanities",
                "text": "Mild insults or swearing (e.g., 'damn', 'hell', 'jerk')."
            },
            {
                "type": "tab",
                "heading": "2. Moderate Swearing",
                "text": "Stronger profanity (e.g., 'shit', 'bitch', 'asshole') or offensive gestures."
            },
            {
                "type": "tab",
                "heading": "3. Sexual Expletives",
                "text": "Explicit language referring to sex acts or organs (e.g., 'fuck', 'cunt')."
            },
            {
                "type": "tab",
                "heading": "4. Discriminatory Language",
                "text": "Slurs or hate speech targeting race, ethnicity, gender, religion, or sexual orientation."
            }
        ]
    },

    # --- DIGITAL PURCHASES ROOT (Q1359) ---
    "1359": {
        "title": "Digital Purchases and NFTs - Consolidated Criteria",
        "content": [
            {
                "type": "general_text",
                "text": "Answer 'Yes' if the screenshot contains ANY of the following:"
            },
            {
                "type": "tab",
                "heading": "1. Digital Goods",
                "text": "Buying items, skins, DLC, season passes, or 'Loot Boxes' (randomized items) with real money."
            },
            {
                "type": "tab",
                "heading": "2. Cash Convertible Rewards",
                "text": "Systems allowing withdrawal of real-world cash or crypto."
            },
            {
                "type": "tab",
                "heading": "3. NFTs",
                "text": "Issuance, trading, or minting of Non-Fungible Tokens or blockchain assets."
            }
        ]
    },

    # --- CRUDE HUMOR ROOT (Q916) ---
    "916": {
        "title": "Crude Humor (Bodily Functions) - Consolidated Criteria",
        "content": [
            {
                "type": "general_text",
                "text": "Answer 'Yes' if the screenshot depicts ANY bodily functions for humor:"
            },
            {
                "type": "tab",
                "heading": "1. Sounds/Text",
                "text": "References to flatulence, burping, or vomiting without visuals."
            },
            {
                "type": "tab",
                "heading": "2. Whimsical/Cartoonish",
                "text": "Stylized 'poo coils', green gas clouds, or cartoon vomit."
            },
            {
                "type": "tab",
                "heading": "3. Realistic/Graphic",
                "text": "Realistic feces, urine, vomit, or the graphic act of defecation."
            }
        ]
    }
}



# -----------------------------
# Prompting (index-based)
# -----------------------------

ANSWER_SCHEMA = (
    '{\n'
    '  "step_1_visual_facts": "List ONLY the exact text and distinct objects visible in the image. No interpretation.",\n'
    '  "step_2_policy_check": "Compare the visual facts against the provided GUIDANCE. Note direct matches or missing elements.",\n'
    '  "step_3_conclusion": "State your final decision based on step 2. Default to safety if ambiguous.",\n'
    '  "confidence": "high",\n'
    '  "unknown": false,\n'
    '  "selected_option_index": -1,\n'
    '  "selected_option_indexes": []\n'
    '}\n'
)

GENERAL_RULES = (
    "You are a professional, unbiased content rater for digital app safety. You do NOT assume anything; you strictly follow the visual evidence.\n"
    "You MUST follow the 3-Step Thinking Process before making a selection:\n"
    "STEP 1 (Visual Facts): Transcribe large text and list distinct objects/UI elements ONLY. Do not speculate or assume unseen gameplay. You must also explicitly identify the visible surface type using only the image: native app screen, in-app advertisement, external store/payment/browser page, OS/system dialog, account chooser/sign-in provider dialog, or unclear.\n"
    "STEP 2 (Policy Check): Compare the visual facts against the provided GUIDANCE strictly. Do not use outside knowledge of the app's title or genre. You must include both evidence FOR the target section and evidence AGAINST it, including any reason the screenshot may belong to a different section or may not qualify at all. Step 2 must explicitly address the active section/question and must say whether this screenshot does or does not show evidence for that specific section.\n"
    "STEP 3 (Conclusion): Reach a decision based ONLY on the evidence in Step 2. If you noted in Step 2 that the content does not meet the specific criteria directly, your Conclusion MUST be 'No' or 'None'. State what visible evidence is sufficient for the final choice and what required evidence is missing when the answer is negative. Step 3 must explicitly answer the active section/question and must say either that the screenshot directly satisfies this section or that it does not satisfy this section and better fits another section.\n"
    "\n"
    "IMPORTANT ANSWERING RULES:\n"
    "- For radio questions: choose exactly ONE valid option index and put it in selected_option_index; keep selected_option_indexes empty.\n"
    "- For checkbox questions: you MUST choose AT LEAST ONE valid option index and put them in selected_option_indexes; set selected_option_index to -1.\n"
    "- Never leave selected_option_indexes empty for checkbox questions.\n"
    "- If the specific content is NOT visible and there is an explicit 'No'/'None' option, select that option.\n"
    "- If there is NO explicit 'No'/'None' option, choose the closest matching option (do not leave it blank).\n"
    "- Only set unknown=true if the image is corrupted or unreadable.\n"
    "- Use screenshot evidence only. Do not infer app capabilities, moderation policy, sharing systems, monetization, or catalog content that are not visibly shown.\n"
    "- Do not rely on app title, store category, or brand reputation.\n"
    "- Treat full Google Play/App Store/browser pages, OS permission prompts, account chooser dialogs, and external install sheets as separate surface types. They are not the same as native app content.\n"
    "- In-app advertisements or promoted content shown inside the screenshot can count as visible app evidence only when the screenshot clearly shows the app itself promoting that content. A full external store/install page should not be treated as native app evidence.\n"
    "- Generic sign-in screens, onboarding pages, feature tours, permission dialogs, and account chooser popups are usually not enough by themselves to trigger Online Content, User Content Sharing, Miscellaneous, or Promotion/Age-Restricted sections unless the screenshot directly shows the relevant content or feature.\n"
    "- For Online Content, answer Yes only when the screenshot visibly shows or directly promotes non-downloaded catalog content such as media libraries, article/news feeds, product listings, streaming catalogs, novel/comic feeds, or other browsable content inside the app. Do not trigger Online Content from a blank sign-in page, permission dialog, or generic subscription/paywall alone.\n"
    "- For User Content Sharing, answer Yes only when the screenshot visibly shows native user interaction or exchange, such as chat, comments, shared photos/videos/posts, creator uploads, community feeds, multiplayer communication, or an explicit statement that users can publicly share content. Do not infer user-content sharing from a login chooser, a social-media ad, or generic references to another app.\n"
    "- Each question must be answered as its own section. Do not reuse Online Content reasoning to answer Violence, Sexuality, Miscellaneous, or Promotion/Age-Restricted questions. Do not reuse User Content Sharing reasoning to answer another section unless the screenshot independently shows evidence for that other section.\n"
    "- If the visible evidence better fits a different section than the active one, the active section must be answered No and you must name the better-fitting section in Step 2 or Step 3.\n"
    "- For every node, the evidence and conclusion must be section-local: they must justify the active section only, not a different section.\n"
    "- For Miscellaneous location sharing, distinguish device permission from user-to-user sharing. A request to access location or show nearby offers is not the same as sharing the user's precise location with other users.\n"
    "- For Miscellaneous digital goods, generic subscriptions, premium tiers, or restore-purchase paywalls are not enough by themselves to prove cash rewards, cash-convertible rewards, NFT issuance, or wager-linked purchases.\n"
    "- For cash rewards or significant monetary value, require explicit evidence such as cash-out, PayPal redemption, payout receipts, withdrawal confirmations, gift-card rewards, or clear real-money reward claims. Do not infer this from coins, gems, coupons, discounts, loyalty points, or virtual balances alone.\n"
    "- For Promotion or Sale of Age-Restricted Products or Activities, answer Yes only when the screenshot directly promotes or sells age-restricted goods or activities such as alcohol, tobacco, firearms, gambling, or another clearly age-restricted product/activity. Mature entertainment, an 18+ age gate, or a streaming bundle by itself is not enough unless the restricted product/activity is explicitly what is being promoted.\n"
    "- Mildly glamorous portraits, beauty filters, ordinary romance artwork, or non-explicit fashion imagery are not automatically sexuality. Trigger sexuality only when the image directly shows sexual material, nudity, clearly revealing attire, strong suggestiveness, or equivalent explicit sexual content.\n"
    "- Step 2 must check consistency between the visible evidence and the answer choice. If you say a screenshot is an external store page, OS dialog, or account chooser, your final answer should not still treat it as strong native app evidence unless the target section is directly visible on that surface.\n"
    "- Generic onboarding slogans, welcome text, progress/setup screens, permission requests, and account setup prompts remain weak evidence by themselves. However, if onboarding/setup text explicitly states the app's core capability, that text does count as visible evidence for the relevant section.\n"
    "- Explicit capability text can count when it clearly describes creating UGC, making skins/assets for others, publishing creator content, exchanging user-made content, or building/shareable items for another platform or community.\n"
    "- For every positive decision, the evidence must directly satisfy the target section. For every negative decision, say what tempting cue is present and why it is still insufficient.\n"
    "\n"
    "Return ONLY valid JSON matching EXACTLY the schema. No extra keys. No commentary.\n"
)


def format_guideline_content(guideline_obj: Dict[str, Any]) -> str:
    """
    Converts the JSON guideline structure (from IARC JSON or our MASTER_GUIDELINES)
    into a readable string for the LLM.
    """
    if not guideline_obj or not isinstance(guideline_obj, dict):
        return ""

    content_list = guideline_obj.get("content", [])
    if not content_list:
        return ""

    lines = []
    title = guideline_obj.get("title", "Definitions")
    lines.append(f"--- GUIDANCE ({title}) ---")

    for item in content_list:
        itype = item.get("type")
        text = (item.get("text") or "").strip()
        if not text:
            continue

        if itype == "general_text":
            lines.append(text)
        elif itype == "tab":
            heading = item.get("heading", "Example")
            lines.append(f"* {heading}: {text}")

    lines.append("---------------------------")
    return "\n".join(lines)


def build_question_prompt(
    question_text: str,
    group_type: str,
    options: List[Tuple[int, str]],
    question_guideline: Optional[Dict[str, Any]],
    option_guidelines: Dict[int, Dict[str, Any]],
) -> str:
    """
    Constructs the final user prompt string.
    This is where the Injection happens: 'question_guideline' will receive
    the MASTER_GUIDELINE object for root questions.
    """
    q_guide_str = format_guideline_content(question_guideline)

    formatted_options = []
    for idx, txt in options:
        opt_str = f"{idx}: {txt}"
        # Attach option-specific guidelines if they exist (rarely used for roots, mostly for leaves)
        if idx in option_guidelines and option_guidelines[idx]:
            opt_guide_str = format_guideline_content(option_guidelines[idx])
            indented_guide = "\n   ".join(opt_guide_str.split("\n"))
            opt_str += f"\n   {indented_guide}"
        formatted_options.append(opt_str)

    opts_block = "\n".join(formatted_options)

    prompt_parts = [GENERAL_RULES]
    
    # If we have injected a Master Guideline, it appears right after the rules
    if q_guide_str:
        prompt_parts.append(q_guide_str)

    prompt_parts.append(f"Question type: {group_type}")
    prompt_parts.append(f"Question: {question_text}")
    prompt_parts.append(f"Options:\n{opts_block}\n")
    prompt_parts.append(f"JSON schema:\n{ANSWER_SCHEMA}")

    return "\n".join(prompt_parts)


def build_json_fix_prompt(bad_text: str) -> str:
    return (
        "You produced invalid JSON.\n"
        "Convert it into valid JSON matching EXACTLY this schema (no extra keys, no commentary):\n"
        f"{ANSWER_SCHEMA}\n"
        "Original response:\n<<<\n"
        f"{bad_text}\n"
        ">>>\n"
    )


def build_root_batch_prompt(roots: List[Dict[str, Any]]) -> str:
    parts = [
        GENERAL_RULES,
        (
            "You must answer ALL questionnaire items below in ONE JSON array.\n"
            "Return one object per question in the same order.\n"
            "Each object must contain:\n"
            '- "questionId": string\n'
            '- "step_1_visual_facts": string\n'
            '- "step_2_policy_check": string\n'
            '- "step_3_conclusion": string\n'
            '- "confidence": "high" | "medium" | "low"\n'
            '- "unknown": boolean\n'
            '- "selected_option_index": integer\n'
            '- "selected_option_indexes": array\n'
            "Do not omit any question. Do not add extra keys.\n"
        ),
    ]

    for idx, node in enumerate(roots, start=1):
        qid = str(node.get("questionId", "")).strip() or f"root_{idx}"
        q_text = (node.get("text") or "").strip()
        group_type = get_group_type(node) or "radio"
        options = get_options_for_prompt(node)
        q_guideline = node.get("guideline")
        if qid in MASTER_GUIDELINES:
            q_guideline = MASTER_GUIDELINES[qid]

        opt_guidelines: Dict[int, Dict[str, Any]] = {}
        for o in get_option_objects(node):
            if "guideline" in o and o["guideline"]:
                opt_guidelines[int(o["idx"])] = o["guideline"]

        parts.append(f"### Item {idx}")
        parts.append(f"questionId: {qid}")
        if q_guideline:
            parts.append(format_guideline_content(q_guideline))
        parts.append(f"Question type: {group_type}")
        parts.append(f"Question: {q_text}")
        parts.append("Options:")
        for opt_idx, opt_text in options:
            opt_line = f"{opt_idx}: {opt_text}"
            if opt_idx in opt_guidelines and opt_guidelines[opt_idx]:
                opt_line += "\n   " + "\n   ".join(format_guideline_content(opt_guidelines[opt_idx]).splitlines())
            parts.append(opt_line)
        parts.append("")

    parts.append(
        "Return ONLY valid JSON as an array like:\n"
        "[\n"
        "  {\n"
        '    "questionId": "1110",\n'
        '    "step_1_visual_facts": "",\n'
        '    "step_2_policy_check": "",\n'
        '    "step_3_conclusion": "",\n'
        '    "confidence": "high",\n'
        '    "unknown": false,\n'
        '    "selected_option_index": -1,\n'
        '    "selected_option_indexes": []\n'
        "  }\n"
        "]"
    )
    return "\n".join(parts)


def _coerce_batched_root_entry(
    entry: Any,
    node: Dict[str, Any],
) -> Dict[str, Any]:
    options = get_options_for_prompt(node)
    group_type = get_group_type(node) or "radio"
    allowed = {idx for idx, _ in options}

    if not isinstance(entry, dict):
        return {"unknown": True, "confidence": "low", "selected_option_index": -1, "selected_option_indexes": []}

    parsed = dict(entry)
    parsed.pop("questionId", None)

    if group_type == "radio":
        sel = _coerce_int(parsed.get("selected_option_index"))
        if sel not in allowed:
            parsed["selected_option_index"] = _fallback_radio_choice(options)
            parsed["selected_option_indexes"] = []
    else:
        sels = [i for i in _coerce_int_list(parsed.get("selected_option_indexes")) if i in allowed]
        if not sels:
            sels = _fallback_checkbox_choice(options)
        parsed["selected_option_index"] = -1
        parsed["selected_option_indexes"] = sels

    parsed["unknown"] = _coerce_bool(parsed.get("unknown"))
    conf = parsed.get("confidence")
    parsed["confidence"] = conf if conf in ("high", "medium", "low") else "low"
    for key in ("step_1_visual_facts", "step_2_policy_check", "step_3_conclusion"):
        val = parsed.get(key)
        parsed[key] = val.strip() if isinstance(val, str) else ""
    return parsed


def _build_chain_base_messages(image_ref: str) -> List[Dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image_ref},
                {
                    "type": "text",
                    "text": (
                        "This is the single screenshot to use for ALL subsequent questionnaire items in this chain.\n"
                        "Maintain consistency across answers only when the same visible evidence truly supports the active section.\n"
                        "Each new questionnaire item must be evaluated independently for its own section.\n"
                        "A previous Yes for one category does NOT imply Yes for a different category.\n"
                        "Previous answers are context, not proof for the current section.\n"
                    ),
                },
            ],
        }
    ]


def ask_main_roots_batched(
    model,
    processor,
    image_ref: str,
    roots: List[Dict[str, Any]],
    max_new_tokens: int,
    do_sample: bool,
    temperature: float,
    top_p: float,
    json_repair: bool,
    include_vlm_input: bool,
) -> Dict[str, Dict[str, Any]]:
    if not roots:
        return {}

    prompt = build_root_batch_prompt(roots)
    messages = [{"role": "user", "content": [{"type": "image", "image": image_ref}, {"type": "text", "text": prompt}]}]

    vlm_input: Optional[Dict[str, Any]] = None
    if include_vlm_input:
        vlm_input = {
            "image_ref": image_ref,
            "question_prompt_text": "[BATCHED_ROOT_REQUEST]",
            "chat_template_text": apply_chat_template_no_thinking(processor, messages),
            "messages": messages,
        }

    batch_max_new_tokens = max(max_new_tokens, min(4096, max(1024, 220 * len(roots))))

    raw = generate_with_messages(
        model=model,
        processor=processor,
        messages=messages,
        max_new_tokens=batch_max_new_tokens,
        do_sample=do_sample,
        temperature=temperature,
        top_p=top_p,
    )

    parsed = extract_first_json_value(raw)
    parsed_list = parsed if isinstance(parsed, list) else []
    parsed_by_qid: Dict[str, Any] = {}
    for item in parsed_list:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("questionId", "")).strip()
        if qid:
            parsed_by_qid[qid] = item

    out: Dict[str, Dict[str, Any]] = {}
    base_messages = _build_chain_base_messages(image_ref)
    for node in roots:
        qid = str(node.get("questionId", "")).strip()
        entry = parsed_by_qid.get(qid)
        if entry is None:
            parsed_single, raw_single, vlm_input_single = ask_node(
                model=model,
                processor=processor,
                image_ref=image_ref,
                node=node,
                max_new_tokens=max_new_tokens,
                do_sample=do_sample,
                temperature=temperature,
                top_p=top_p,
                json_repair=json_repair,
                include_raw=True,
                include_vlm_input=include_vlm_input,
                messages_prefix=base_messages,
                include_image_in_this_turn=False,
            )
            out[qid] = {
                "parsed": parsed_single,
                "raw": raw_single,
                "vlm_input": vlm_input_single,
            }
            continue

        coerced = _coerce_batched_root_entry(entry, node)
        out[qid] = {"parsed": coerced, "raw": raw, "vlm_input": vlm_input}
    return out


# -----------------------------
# Basic utilities
# -----------------------------

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def atomic_write_json(path: Path, data: Any) -> None:
    """Safely writes JSON to a file by writing to a temp file first, then renaming."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    tmp.replace(path)


def read_existing_results(path: Path) -> List[Dict[str, Any]]:
    """Reads the current output JSON file if it exists."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def build_processed_keyset(existing: List[Dict[str, Any]]) -> set:
    """Builds a set of image keys that have already been processed."""
    processed = set()
    for rec in existing:
        key = rec.get("image_key")
        if isinstance(key, str) and key:
            processed.add(key)
    return processed


def extract_first_json_value(text: str) -> Optional[Any]:
    """Robustly extracts the first valid JSON object from a string (even with markdown)."""
    text = (text or "").strip()
    
    # Strip markdown code blocks if the model wraps the JSON
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text)
        text = re.sub(r"```$", "", text).strip()

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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def file_uri_to_path(uri: str) -> Optional[Path]:
    try:
        p = urlparse(uri)
        if p.scheme != "file":
            return None
        return Path(unquote(p.path))
    except Exception:
        return None


def as_image_ref(s: str) -> str:
    """Normalizes a string into a valid image reference (URI or local path)."""
    s = (s or "").strip()
    if not s:
        return s
    lower = s.lower()
    if lower.startswith(("http://", "https://", "file://", "data:image")):
        return s
    p = Path(s)
    if p.exists():
        return p.resolve().as_uri()
    return s


def _chunk_nodes(nodes: List[Dict[str, Any]], chunk_size: int) -> List[List[Dict[str, Any]]]:
    if chunk_size <= 0:
        return [nodes]
    return [nodes[i : i + chunk_size] for i in range(0, len(nodes), chunk_size)]


def list_images_in_dir(root: Path, recursive: bool, exts: Tuple[str, ...]) -> List[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Input directory does not exist: {root}")
    exts_lower = tuple(e.lower() for e in exts)
    paths: List[Path] = []
    if recursive:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts_lower:
                paths.append(p)
    else:
        for p in root.glob("*"):
            if p.is_file() and p.suffix.lower() in exts_lower:
                paths.append(p)
    return sorted(paths)


def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


# -----------------------------
# URL download cache
# -----------------------------

def _guess_ext_from_headers(headers: Any) -> str:
    ctype = ""
    try:
        ctype = (headers.get("Content-Type") or "").lower()
    except Exception:
        ctype = ""
    if "png" in ctype:
        return ".png"
    if "webp" in ctype:
        return ".webp"
    if "jpeg" in ctype or "jpg" in ctype:
        return ".jpg"
    if "bmp" in ctype:
        return ".bmp"
    if "tiff" in ctype or "tif" in ctype:
        return ".tif"
    if "tiff" in ctype or "tif" in ctype:
        return ".tif"
    return ".img"


def _find_existing_cached_file(cache_dir: Path, key: str) -> Optional[Path]:
    if not cache_dir.exists():
        return None

    preferred_exts = (".png", ".webp", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".img")
    for ext in preferred_exts:
        p = cache_dir / f"{key}{ext}"
        try:
            if p.exists() and p.is_file() and p.stat().st_size > 0:
                return p
        except Exception:
            pass

    try:
        for p in cache_dir.glob(f"{key}.*"):
            if not p.is_file():
                continue
            if p.suffix.lower().endswith(".tmp") or p.name.endswith(".tmp"):
                continue
            try:
                if p.stat().st_size > 0:
                    return p
            except Exception:
                continue
    except Exception:
        pass

    return None


def download_url_to_cache(url: str, cache_dir: Path, timeout_s: int, retries: int) -> Optional[Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = sha256_text(url)

    existing = _find_existing_cached_file(cache_dir, key)
    if existing is not None:
        return existing

    headers = {"User-Agent": "Mozilla/5.0 (compatible; IARC-VLM-Rater/3.0)"}
    req = urllib.request.Request(url, headers=headers)

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = resp.read()
                ext = _guess_ext_from_headers(resp.headers)
            if not data:
                raise RuntimeError("empty response body")

            out_path = cache_dir / f"{key}{ext}"
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(out_path)
            return out_path
        except Exception as e:
            last_err = e
            time.sleep(0.5 * attempt)

    LOGGER.warning("Failed to download URL after %d retries: %s (%s)", retries, url, last_err)
    return None


def ensure_local_image_ref(
    image_ref: str,
    cache_dir: Path,
    timeout_s: int,
    retries: int,
    download_urls: bool,
) -> Tuple[str, Optional[str]]:
    image_ref = (image_ref or "").strip()
    if not download_urls:
        return image_ref, None
    if image_ref.lower().startswith(("http://", "https://")):
        local = download_url_to_cache(image_ref, cache_dir=cache_dir, timeout_s=timeout_s, retries=retries)
        if not local:
            return image_ref, "download_failed"
        return local.resolve().as_uri(), None
    return image_ref, None


# -----------------------------
# Qwen-VL inference
# -----------------------------

def load_model_and_processor(
    model_name: str,
    conv_mode: str,
    device_map: str,
    dtype_str: str,
    attn_implementation: Optional[str],
):
    del conv_mode
    import transformers

    wants_qwen35 = "qwen3.5" in model_name.lower()
    try:
        from transformers import Qwen3_5ForConditionalGeneration
        model_cls = Qwen3_5ForConditionalGeneration
    except Exception:
        if wants_qwen35:
            raise SystemExit(
                "Qwen3.5 requires a newer Transformers build than the one currently installed. "
                f"Detected transformers=={transformers.__version__}. "
                'Install the latest main build, e.g. `pip install "transformers[serving] @ git+https://github.com/huggingface/transformers.git@main"` '
                "or use a supported serving stack such as vLLM/SGLang for Qwen3.5."
            )
        try:
            from transformers import Qwen3VLForConditionalGeneration
            model_cls = Qwen3VLForConditionalGeneration
        except Exception:
            try:
                from transformers import Qwen2VLForConditionalGeneration
                model_cls = Qwen2VLForConditionalGeneration
            except Exception:
                try:
                    from transformers import Qwen2_5_VLForConditionalGeneration
                    model_cls = Qwen2_5_VLForConditionalGeneration
                except Exception:
                    from transformers import AutoModelForImageTextToText
                    model_cls = AutoModelForImageTextToText

    dtype: Union[str, torch.dtype]
    if dtype_str == "auto":
        dtype = "auto"
    elif dtype_str == "float16":
        dtype = torch.float16
    elif dtype_str == "bfloat16":
        dtype = torch.bfloat16
    elif dtype_str == "float32":
        dtype = torch.float32
    else:
        raise ValueError(f"Unsupported --dtype: {dtype_str}")

    kwargs: Dict[str, Any] = {"device_map": device_map}
    if attn_implementation:
        kwargs["attn_implementation"] = attn_implementation

    LOGGER.info("Loading Qwen model: %s", model_name)
    try:
        model = model_cls.from_pretrained(model_name, dtype=dtype, **kwargs)
    except TypeError:
        model = model_cls.from_pretrained(model_name, torch_dtype=dtype, **kwargs)

    processor = AutoProcessor.from_pretrained(model_name)
    return model, processor


def apply_chat_template_no_thinking(processor, messages: Any) -> str:
    try:
        return processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        return processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )


def _sanitize_video_kwargs(video_inputs: Any, video_kwargs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = dict(video_kwargs or {})
    if not video_inputs:
        cleaned.pop("fps", None)
        cleaned.pop("video_fps", None)
        cleaned.pop("max_pixels", None)
        cleaned.pop("min_pixels", None)
        return cleaned

    for key in ("fps", "video_fps"):
        value = cleaned.get(key)
        if isinstance(value, list):
            if len(value) == 0:
                cleaned.pop(key, None)
            elif len(value) == 1:
                cleaned[key] = value[0]

    return cleaned


def generate_with_messages(
    model,
    processor,
    messages: Any,
    max_new_tokens: int,
    do_sample: bool,
    temperature: float,
    top_p: float,
) -> str:
    text = apply_chat_template_no_thinking(processor, messages)

    try:
        image_inputs, video_inputs, video_kwargs = process_vision_info(messages, return_video_kwargs=True)
    except TypeError:
        image_inputs, video_inputs = process_vision_info(messages)
        video_kwargs = {}
    video_kwargs = _sanitize_video_kwargs(video_inputs, video_kwargs)

    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
        **(video_kwargs or {}),
    )
    inputs = inputs.to(model.device)

    gen_kwargs: Dict[str, Any] = {"max_new_tokens": max_new_tokens, "do_sample": do_sample}
    if do_sample:
        gen_kwargs.update({"temperature": temperature, "top_p": top_p})
    tokenizer = getattr(processor, "tokenizer", None)
    pad_token_id = getattr(tokenizer, "pad_token_id", None)
    if pad_token_id is None:
        pad_token_id = getattr(tokenizer, "eos_token_id", None)
    if pad_token_id is not None:
        gen_kwargs["pad_token_id"] = pad_token_id

    with torch.inference_mode():
        generated_ids = model.generate(**inputs, **gen_kwargs)

    prompt_len = inputs["input_ids"].shape[-1]
    generated_ids_trimmed = generated_ids[:, prompt_len:]

    out_text = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return (out_text[0] if out_text else "").strip()


def force_json_repair(
    model,
    processor,
    bad_text: str,
    max_new_tokens: int,
) -> Optional[Dict[str, Any]]:
    repair_prompt = build_json_fix_prompt(bad_text)
    messages = [{"role": "user", "content": [{"type": "text", "text": repair_prompt}]}]

    fixed = generate_with_messages(
        model=model,
        processor=processor,
        messages=messages,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        temperature=1.0,
        top_p=1.0,
    )
    parsed = extract_first_json_value(fixed)
    return parsed if isinstance(parsed, dict) else None


# -----------------------------
# Input adapters
# -----------------------------

def load_image_records_from_json(
    path: Path,
    screenshot_policy: str,
    screenshot_index: int,
    max_screenshots_per_app: int,
) -> List[Dict[str, Any]]:
    """
    Parses the input JSON file to build a flat list of work items.
    Supports:
    1. Google Play dump format: list[dict] with key "screenshots": list[str]
    2. Generic format: list[dict] with key "image" or "images"
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list")

    # Heuristic: Check the first few items to see if it looks like a GPlay dump
    is_gplay = any(isinstance(x, dict) and isinstance(x.get("screenshots"), list) for x in data[:10])

    out: List[Dict[str, Any]] = []
    
    if is_gplay:
        for item in data:
            if not isinstance(item, dict):
                continue
            
            shots = item.get("screenshots")
            if not isinstance(shots, list):
                continue
            
            # Clean and deduplicate URLs while preserving order
            shot_urls = dedupe_preserve_order([s.strip() for s in shots if isinstance(s, str) and s.strip()])
            if not shot_urls:
                continue

            # Apply Screenshot Policy
            chosen: List[str] = []
            if screenshot_policy == "first":
                chosen = shot_urls[:1]
            elif screenshot_policy == "all":
                chosen = shot_urls[: max(1, max_screenshots_per_app)]
            elif screenshot_policy == "index":
                if 0 <= screenshot_index < len(shot_urls):
                    chosen = [shot_urls[screenshot_index]]
            elif screenshot_policy == "random":
                # Use deterministic seed based on appId so re-runs pick the same "random" image
                seed = item.get("appId") or item.get("url") or ""
                rng = random.Random(sha256_text(str(seed)))
                chosen = [rng.choice(shot_urls)]
            else:
                chosen = shot_urls[:1]

            # Metadata to carry forward into the results
            app_meta = {
                "title": item.get("title"),
                "appId": item.get("appId"),
                "store_url": item.get("url"),
                "developer": item.get("developer"),
                "genre": item.get("genre"),
                "contentRating": item.get("contentRating"),
                "contentRatingDescription": item.get("contentRatingDescription"),
            }

            for j, u in enumerate(chosen):
                out.append(
                    {
                        "image_ref": as_image_ref(u),
                        "source": "gplay",
                        "app": app_meta,
                        "screenshot_index": j,
                    }
                )
        return out

    # Generic format fallback
    for item in data:
        if not isinstance(item, dict):
            continue
        
        # Handle 'images' list
        if isinstance(item.get("images"), list):
            for img in item["images"]:
                if isinstance(img, str) and img.strip():
                    rec = dict(item)
                    rec.pop("images", None)
                    rec["image_ref"] = as_image_ref(img)
                    out.append(rec)
        # Handle single 'image'/'path' field
        else:
            img = item.get("image") or item.get("image_path") or item.get("path")
            if isinstance(img, str) and img.strip():
                rec = dict(item)
                rec["image_ref"] = as_image_ref(img)
                out.append(rec)

    return out


# -----------------------------
# Questionnaire loading + chain roots
# -----------------------------

def load_questionnaire(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("sequence"), list):
        raise ValueError("Questionnaire JSON must be an object with a 'sequence' list.")
    return data


def find_chain_roots(questionnaire: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Identifies the starting questions (Roots) for the sections we care about.
    """
    seq = questionnaire["sequence"]

    section_first: Dict[str, Dict[str, Any]] = {}
    misc_nodes: List[Dict[str, Any]] = []
    incoming_misc: Dict[str, int] = {}

    for node in seq:
        if not isinstance(node, dict):
            continue
        raw_section = node.get("section")
        if not isinstance(raw_section, str):
            continue
        
        section = canonical_section_name(raw_section)

        if section in MAIN_CHAIN_SECTIONS and section not in section_first:
            # Store the first node encountered for this section as the Root
            section_first[section] = node

        if raw_section == MISC_SECTION:
            misc_nodes.append(node)
            qid = str(node.get("questionId", ""))
            if qid and qid not in incoming_misc:
                incoming_misc[qid] = 0

    roots: List[Dict[str, Any]] = []
    for sec in MAIN_CHAIN_SECTIONS:
        # Miscellaneous is handled as multiple graph roots.
        if sec == MISC_SECTION:
            continue
        if sec not in section_first:
            LOGGER.warning("No root found for section: %s", sec)
            continue
        roots.append(section_first[sec])

    # For Miscellaneous, create one chain per actual root node
    # (node in Misc section with no incoming reveal from another Misc node).
    if MISC_SECTION in MAIN_CHAIN_SECTIONS:
        misc_qids = set(str(n.get("questionId", "")) for n in misc_nodes if isinstance(n, dict))
        for node in misc_nodes:
            for opt in get_option_objects(node):
                reveals = opt.get("reveals")
                if not isinstance(reveals, list):
                    continue
                for child in reveals:
                    if not isinstance(child, dict):
                        continue
                    cqid = str(child.get("questionId", ""))
                    if cqid in misc_qids:
                        incoming_misc[cqid] = incoming_misc.get(cqid, 0) + 1

        for node in misc_nodes:
            qid = str(node.get("questionId", ""))
            if incoming_misc.get(qid, 0) == 0:
                roots.append(node)

    return roots


def get_group_type(node: Dict[str, Any]) -> Optional[str]:
    group = node.get("group")
    if not isinstance(group, dict):
        return None
    t = group.get("type")
    return t if t in ("radio", "checkbox") else None


def get_option_objects(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    group = node.get("group")
    if not isinstance(group, dict):
        return []
    opts = group.get("options")
    if not isinstance(opts, list):
        return []
    out = []
    for o in opts:
        if not isinstance(o, dict):
            continue
        if not isinstance(o.get("idx"), int):
            continue
        if not isinstance(o.get("text"), str):
            continue
        out.append(o)
    return out


def get_options_for_prompt(node: Dict[str, Any]) -> List[Tuple[int, str]]:
    opts = get_option_objects(node)
    return [(int(o["idx"]), str(o["text"]).strip()) for o in opts]


# -----------------------------
# Parsing + normalization (robust)
# -----------------------------

def _coerce_bool(x: Any) -> bool:
    """Safely converts various inputs (str, int, bool) to a boolean."""
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        t = x.strip().lower()
        return t in ("true", "yes", "y", "1")
    if isinstance(x, (int, float)):
        return x == 1
    return False


def _coerce_int(x: Any) -> Optional[int]:
    """Safely converts input to an integer or None."""
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str):
        try:
            return int(x.strip())
        except Exception:
            return None
    return None


def _coerce_int_list(x: Any) -> List[int]:
    """Safely converts input to a list of integers."""
    if isinstance(x, list):
        out: List[int] = []
        for v in x:
            iv = _coerce_int(v)
            if iv is not None:
                out.append(iv)
        return out
    iv = _coerce_int(x)
    return [iv] if iv is not None else []


def normalize_answer(
    parsed: Dict[str, Any],
    group_type: str,
    options: List[Tuple[int, str]],
) -> Dict[str, Any]:
    """
    Standardizes the parsed JSON into a consistent internal format.
    Handles 'unknown' flags, confidence levels, and resolves text-based answers back to indices if needed.
    """
    allowed = {idx for idx, _ in options}
    idx_to_text = {idx: txt for idx, txt in options}

    unknown = _coerce_bool(parsed.get("unknown"))
    confidence = parsed.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "low" if unknown else "medium"

    # Normalize evidence fields (combining step-by-step reasoning if available)
    step1 = parsed.get("step_1_visual_facts", "")
    step2 = parsed.get("step_2_policy_check", "")
    step3 = parsed.get("step_3_conclusion", "")
    
    evidence_parts = []
    if step1: evidence_parts.append(f"Visual Facts: {step1}")
    if step2: evidence_parts.append(f"Policy Check: {step2}")
    if step3: evidence_parts.append(f"Conclusion: {step3}")
    
    evidence = " | ".join(evidence_parts) if evidence_parts else "No reasoning provided."

    if unknown and not evidence_parts:
        evidence = "Not enough relevant content is clearly visible in this screenshot."

    # Backup: check if model returned text 'choice' instead of 'index'
    fallback_choice_text = parsed.get("choice") if isinstance(parsed.get("choice"), str) else None
    fallback_choices_text = parsed.get("choices") if isinstance(parsed.get("choices"), list) else None

    # --- RADIO LOGIC ---
    if group_type == "radio":
        sel = _coerce_int(parsed.get("selected_option_index"))
        
        # Try text fallback if index is missing
        if not unknown and sel is None and fallback_choice_text:
            for idx, txt in options:
                if txt == fallback_choice_text.strip():
                    sel = idx
                    break

        if unknown or sel is None or sel not in allowed:
            return {"answer": "Unknown", "checked": [], "unknown": True, "confidence": confidence, "evidence": evidence}

        return {"answer": idx_to_text[sel], "checked": [], "unknown": False, "confidence": confidence, "evidence": evidence}

    # --- CHECKBOX LOGIC ---
    sels = _coerce_int_list(parsed.get("selected_option_indexes"))
    
    # Try text fallback if indices are missing
    if not unknown and not sels and fallback_choices_text:
        exact = set(txt for _, txt in options)
        sels = []
        for t in fallback_choices_text:
            if isinstance(t, str) and t.strip() in exact:
                for idx, txt in options:
                    if txt == t.strip():
                        sels.append(idx)

    # Filter invalid indices and deduplicate
    sels = [i for i in sels if i in allowed]
    seen = set()
    sels2 = []
    for i in sels:
        if i not in seen:
            sels2.append(i)
            seen.add(i)

    if unknown:
        return {"answer": None, "checked": [], "unknown": True, "confidence": confidence, "evidence": evidence}

    return {"answer": None, "checked": [idx_to_text[i] for i in sels2], "unknown": False, "confidence": confidence, "evidence": evidence}


# -----------------------------
# Selection enforcement + fallback
# -----------------------------

def _has_explicit_none_option(options: List[Tuple[int, str]]) -> Optional[int]:
    for idx, txt in options:
        t = (txt or "").strip().lower()
        if t in {"none", "no", "not present"}:
            return idx
        if t.startswith("no ") or "none of the above" in t or t == "none of these":
            return idx
    return None


def _fallback_radio_choice(options: List[Tuple[int, str]]) -> Optional[int]:
    """If repair fails, default to 'None' or the first option."""
    none_idx = _has_explicit_none_option(options)
    if none_idx is not None:
        return none_idx
    return options[0][0] if options else None


def _fallback_checkbox_choice(options: List[Tuple[int, str]]) -> List[int]:
    """If repair fails, default to 'None' or 'Rarely' (for frequency) or first option."""
    none_idx = _has_explicit_none_option(options)
    if none_idx is not None:
        return [none_idx]
    for idx, txt in options:
        if "rarely" in (txt or "").lower():
            return [idx]
    return [options[0][0]] if options else []


RADIO_SELECTION_REPAIR_PREFIX = (
    "SELECTION REPAIR:\n"
    "Your previous JSON did not select a valid option index.\n"
    "You MUST select exactly ONE valid option index from the Options list.\n"
    "Return ONLY valid JSON matching EXACTLY the schema.\n\n"
)

CHECKBOX_SELECTION_REPAIR_PREFIX = (
    "SELECTION REPAIR:\n"
    "Your previous JSON did not select any option index (or selected invalid indexes).\n"
    "You MUST select at least one valid option index from the Options list.\n"
    "Do NOT leave selected_option_indexes empty.\n"
    "If frequency ('Rarely' vs 'Often') cannot be inferred from a single screenshot, choose the 'Rarely' option.\n"
    "Return ONLY valid JSON matching EXACTLY the schema.\n\n"
)


# -----------------------------
# Chain memory helpers (MULTI-TURN)
# -----------------------------

def _schema_json_for_history(parsed: Dict[str, Any], group_type: str) -> Dict[str, Any]:
    """Reconstructs a clean JSON object to store in conversation history as the assistant's previous turn."""
    unknown = _coerce_bool(parsed.get("unknown"))
    confidence = parsed.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "low" if unknown else "medium"
    
    # Store reasoning steps in history
    step1 = parsed.get("step_1_visual_facts", "")
    step2 = parsed.get("step_2_policy_check", "")
    step3 = parsed.get("step_3_conclusion", "")

    if group_type == "radio":
        sel = _coerce_int(parsed.get("selected_option_index"))
        return {
            "step_1_visual_facts": step1,
            "step_2_policy_check": step2,
            "step_3_conclusion": step3,
            "selected_option_index": int(sel) if sel is not None else -1,
            "selected_option_indexes": [],
            "unknown": unknown,
            "confidence": confidence,
        }

    sels = _coerce_int_list(parsed.get("selected_option_indexes"))
    return {
        "step_1_visual_facts": step1,
        "step_2_policy_check": step2,
        "step_3_conclusion": step3,
        "selected_option_index": -1,
        "selected_option_indexes": sels,
        "unknown": unknown,
        "confidence": confidence,
    }


def _build_history_turn_text(node: Dict[str, Any]) -> str:
    """Reconstructs the text of the user's previous question for history."""
    q_text = (node.get("text") or "").strip()
    group_type = get_group_type(node) or "radio"
    opts = get_options_for_prompt(node)
    opts_block = "\n".join([f"{i}: {t}" for i, t in opts])
    qid = node.get("questionId")
    qid_str = f"{qid}" if isinstance(qid, str) and qid else "N/A"
    return (
        f"Previous questionnaire item (questionId={qid_str})\n"
        f"Type: {group_type}\n"
        f"Question: {q_text}\n"
        f"Options:\n{opts_block}\n"
        "Responded above with JSON.\n"
        "Use this only as context for consistency when the same evidence applies.\n"
        "Do not reuse a previous rationale to answer a different section unless the current section is independently supported by visible evidence.\n"
    )


def _truncate_history_messages(history: List[Dict[str, Any]], max_steps: int) -> List[Dict[str, Any]]:
    """Keeps the context window from growing indefinitely."""
    if max_steps <= 0:
        return []
    keep = 2 * max_steps # 2 messages per step (User + Assistant)
    return history[-keep:]


FORCED_STATIC_ROOT_QIDS = {"1109"}

# Downloaded App (Q1152) custom master-guideline questions:
# keep only section-starter prompts requested by policy owner.
DOWNLOADED_APP_MASTER_SUBQUESTIONS = [
    "Does the app contain inferences of, references to, or depictions of violence, blood, or gory images?",
    "Does the app contain inferences of, references to, or depictions of sexuality, sexual violence, suggestiveness, revealing attire, or nudity?",
    "Does the app contain gambling, simulations of casino gambling/bingo, or gambling themes?",
    "Does the app contain any potentially offensive language?",
    "Does the app contain any bodily functions such as belching, flatulence, or vomiting when used for humorous purposes?",
]


def _pick_forced_option_index(qid: str, options: List[Tuple[int, str]]) -> Optional[int]:
    # 1109 (App Type) must always be "All Other App Types"
    if qid == "1109":
        for idx, txt in options:
            if (txt or "").strip().lower() == "all other app types":
                return idx
        return None

    return None


def _clone_guideline_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    itype = item.get("type")
    text = item.get("text")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    # Drop clearly broken placeholder artifacts.
    if text.lower() in ("undefined", "null", "none", "nan"):
        return None

    # Accept normalized types from JSON and map `block_raw` into prompt-friendly format.
    if itype in ("general_text", "tab"):
        normalized = itype
    elif itype == "block_raw":
        normalized = "tab" if isinstance(item.get("heading"), str) and item.get("heading").strip() else "general_text"
    else:
        return None

    out = {"type": normalized, "text": text}
    if normalized == "tab":
        heading = item.get("heading")
        if isinstance(heading, str) and heading.strip():
            out["heading"] = heading.strip()
    return out


def _iter_revealed_subquestions(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = []
    seen_qids = set()

    for opt in get_option_objects(node):
        rev = opt.get("reveals")
        if isinstance(rev, list):
            for child in rev:
                if isinstance(child, dict):
                    queue.append(child)

    while queue:
        cur = queue.pop(0)
        qid = str(cur.get("questionId", ""))
        if qid and qid in seen_qids:
            continue
        if qid:
            seen_qids.add(qid)
        out.append(cur)

        g = cur.get("group")
        if isinstance(g, dict):
            opts = g.get("options")
            if isinstance(opts, list):
                for o in opts:
                    if not isinstance(o, dict):
                        continue
                    rev = o.get("reveals")
                    if isinstance(rev, list):
                        for child in rev:
                            if isinstance(child, dict):
                                queue.append(child)

    return out


def _build_dynamic_master_guideline(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Builds per-question guidance:
    - include node guideline (if present)
    - include revealed subquestion prompts/guidelines (if any)
    """
    content: List[Dict[str, Any]] = []
    seen = set()

    def add_item(item: Dict[str, Any]) -> None:
        cloned = _clone_guideline_item(item)
        if not cloned:
            return
        key = (cloned.get("type"), cloned.get("heading"), cloned.get("text"))
        if key in seen:
            return
        seen.add(key)
        content.append(cloned)

    def add_text(text: str, heading: Optional[str] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        if heading:
            add_item({"type": "tab", "heading": heading, "text": text})
        else:
            add_item({"type": "general_text", "text": text})

    base_guideline = node.get("guideline")
    if isinstance(base_guideline, dict):
        for it in base_guideline.get("content", []) if isinstance(base_guideline.get("content"), list) else []:
            if isinstance(it, dict):
                add_item(it)

    raw_section = str(node.get("section") or "").strip()
    q_text_lc = str(node.get("text") or "").strip().lower()
    if raw_section == ONLINE_CONTENT_SECTION:
        add_text(
            "Online Content means non-downloaded content that can be accessed from inside the app, such as media libraries, article/news feeds, product listings, streaming catalogs, novel/comic feeds, or other browsable seller-provided content.",
            heading="Online Content Definition",
        )
        add_text(
            "Answer Yes only when the screenshot visibly shows that remote content itself or directly promotes a specific remotely accessed item, feed, catalog, library, listing, or collection inside the app.",
            heading="Online Content Positive Evidence",
        )
        add_text(
            "Answer No for onboarding/setup questions, purpose-selection screens, generic sign-in pages, permission dialogs, subscription/paywall screens, or generic capability text that does not show or directly promote actual remotely accessed content.",
            heading="Online Content Negative Boundary",
        )
        add_text(
            "Reward splashes, payout claims, sign-in screens, verification banners, legal confirmations, account creation prompts, and cash-out messaging are not Online Content by themselves unless the screenshot also shows a remote catalog, feed, listing, library, or other browsable seller-provided content.",
            heading="Online Content Reward/Login Boundary",
        )
        add_text(
            "Native lobby screens, dashboards, wallet/balance screens, bonus popups, mini-game launchers, reward centers, and app menus are not Online Content just because they are interactive or update over the internet. Online Content is about remotely accessed seller-provided content collections, not every live app feature.",
            heading="Online Content Native-Lobby Boundary",
        )

    if raw_section == USER_CONTENT_SHARING_SECTION:
        add_text(
            "User Content Sharing means the app natively lets users interact with other users or exchange user-made content through chat, comments, posts, uploads, shared photos/videos/audio, public creator publishing, community feeds, or other user-to-user sharing features.",
            heading="User Content Sharing Definition",
        )
        add_text(
            "Answer Yes when the screenshot shows native interaction or exchange features, or when the screenshot explicitly states that users can share, publish, upload, exchange, or distribute user-made content to other users or to a platform/community.",
            heading="User Content Sharing Positive Evidence",
        )
        add_text(
            "For onboarding/setup screens, explicit capability text can count if it clearly describes shareable or publishable user-made content, including creating UGC, skins, or assets for a named platform/community or for clients. Do not require the sharing UI itself if the text explicitly states this user-content purpose.",
            heading="User Content Sharing Onboarding Exception",
        )
        add_text(
            "Answer No when the screenshot only shows generic creation/editing, business growth, or goal-selection text without any indication that the created content is shared, published, uploaded, exchanged, or intended for other users or a platform/community.",
            heading="User Content Sharing Negative Boundary",
        )

    if raw_section == PROMOTION_SALE_SECTION:
        add_text(
            "This section is only about whether the product focuses on promoting or selling age-restricted goods or activities. The relevant follow-ups are whether it focuses on promoting alcohol or tobacco, or on any other age-restricted activities or items.",
            heading="Age-Restricted Promotion Definition",
        )
        add_text(
            "Answer Yes only when the screenshot directly promotes, advertises, sells, or centers an age-restricted product or activity itself, such as alcohol, tobacco, firearms, gambling, betting, casinos, or another clearly age-restricted item or activity.",
            heading="Age-Restricted Positive Evidence",
        )
        add_text(
            "A screenshot can count even if it is a native app screen rather than a checkout page, as long as the visible focus is clearly on the restricted product or activity being promoted or sold.",
            heading="Age-Restricted Focus Rule",
        )
        add_text(
            "Answer No when the screenshot only shows general app navigation, mature entertainment, age gates, reward systems, virtual balances, remote content catalogs, or unrelated features without directly promoting or selling the age-restricted product or activity itself.",
            heading="Age-Restricted Negative Boundary",
        )

    if raw_section == DOWNLOADED_APP_SECTION:
        add_text(
            "Downloaded App covers ratings-relevant content that is visibly present in the app's bundled native UI, built-in assets, or prepackaged screens. The examples in the question text such as sex, violence, and language are examples, not a complete list of ratings-relevant content.",
            heading="Downloaded App Definition",
        )
        add_text(
            "Answer Yes when the screenshot visibly shows ratings-relevant mechanics, themes, or systems that are clearly part of the native downloaded interface itself, including gambling-like features, slot-machine or lucky-spin screens, casino-style menus, reward-wheel interfaces, betting/trading style game mechanics, or other built-in ratings-relevant elements shown directly in the app UI.",
            heading="Downloaded App Native-Mechanics Rule",
        )
        add_text(
            "Do not answer No merely because the screenshot lacks sex, violence, or language if another ratings-relevant category is visibly present in the native downloaded UI. However, answer No when the screenshot only shows remote content catalogs, external/store/system surfaces, login-only shells, or content that is merely implied rather than visibly shown.",
            heading="Downloaded App Boundary",
        )
        add_text(
            "If the native app screen itself visibly presents a reward-economy lobby with elements such as virtual balance, free funds, sign-up bonus, daily reward, lucky spin, slot machine, or similar built-in gambling-like mechanics, treat those visible mechanics as ratings-relevant downloaded content rather than dismissing them just because they are not sex, violence, or language.",
            heading="Downloaded App Reward-Economy Rule",
        )

    if raw_section == LANGUAGE_SECTION:
        add_text(
            "Potentially offensive language requires visible profanities, slurs, sexual expletives, insulting phrases, or other text/gestures that are themselves offensive. Do not answer Yes from general maturity cues, payout language, verification text, legal confirmations, age gates, login prompts, brand names, or reward slogans.",
            heading="Language Positive/Negative Boundary",
        )
        add_text(
            "Words such as reward, payout, verify, continue, login, privacy, terms, cash, PayPal, gift cards, and similar transactional or account text are not offensive language by themselves.",
            heading="Language Transactional Text Rule",
        )

    if raw_section == MISC_SECTION and "allow users to purchase digital goods" in q_text_lc:
        add_text(
            "Digital goods purchases require visible evidence of buying digital items or app features, such as prices, purchase buttons, upgrade/premium offers, subscriptions, item packs, skins, coins, unlocks, DLC, or a store/check-out flow for digital content.",
            heading="Digital Goods Positive Evidence",
        )
        add_text(
            "Do not answer Yes from earn-money claims, PayPal payouts, gift cards, cash-out language, reward redemption, verification banners, login screens, or generic monetary messaging unless the screenshot also shows a purchase flow for digital items/features.",
            heading="Digital Goods Reward Boundary",
        )

    if raw_section == MISC_SECTION and "include cash rewards, gift cards, play-to-earn features" in q_text_lc:
        add_text(
            "Cash rewards / gift cards / play-to-earn should be answered Yes when the screenshot explicitly shows cash-out, PayPal payouts, gift-card rewards, earn-money claims, withdrawal/redemption, or similar real-world reward messaging.",
            heading="Cash Rewards Positive Evidence",
        )
        add_text(
            "This question is about reward mechanics, not digital-item purchases. A reward or payout screen can be Yes here even when the separate digital-goods purchase question should remain No.",
            heading="Cash Rewards vs Digital Goods Boundary",
        )
        add_text(
            "Also answer Yes when the screenshot clearly shows a native reward-economy system centered on bonuses, free funds, daily rewards, lucky spins, slot-machine rewards, or play-to-earn style incentives that are presented as part of earning value or prizes in the app, even if the screenshot does not literally show PayPal or a cash-out button.",
            heading="Cash Rewards Reward-Economy Rule",
        )

    if raw_section == MISC_SECTION and "issuance of these digital assets" in q_text_lc:
        add_text(
            "Issuance of transferable digital assets requires explicit evidence of minting, creating, issuing, or generating blockchain/NFT-style assets. Do not answer Yes from generic rewards, coins, balances, virtual currency, spins, slots, shops, or prize screens alone.",
            heading="Digital Asset Issuance Boundary",
        )

    if raw_section == MISC_SECTION and "integrated marketplace for these assets" in q_text_lc:
        add_text(
            "An integrated marketplace for digital assets requires visible evidence of a marketplace/exchange specifically for transferable digital assets, such as NFT listings, buy/sell/trade controls, wallet-linked asset pages, blockchain asset collections, or explicit marketplace branding. A generic 'Shop' button or store is not enough.",
            heading="Digital Asset Marketplace Boundary",
        )

    if raw_section == MISC_SECTION and "purchases related to the issuance or earning of these digital assets" in q_text_lc:
        add_text(
            "Answer Yes only when the screenshot explicitly ties purchases or fees to minting, issuing, acquiring, or earning transferable digital assets. Do not infer this from a generic shop, bonuses, sign-up rewards, slot features, or virtual balances.",
            heading="Digital Asset Purchase Boundary",
        )

    # Q1152 (Downloaded App): keep guidance concise and policy-controlled.
    # Include only the node's own guideline blocks + the requested starter subquestions.
    if str(node.get("questionId", "")) == "1152":
        add_text(
            "Downloaded App is only a gateway into possible ratings-relevant subtypes. A Yes at Downloaded App does not mean that violence, sexuality, language, crude humor, fear, or substance use are present. Each subtype must still be answered from its own direct visible evidence.",
            heading="Downloaded App Subtype Independence Rule",
        )
        for idx, qtext in enumerate(DOWNLOADED_APP_MASTER_SUBQUESTIONS, start=1):
            add_text(qtext, heading=f"Downloaded App Subquestion {idx}")
        if not content:
            return None
        base_title = ""
        if isinstance(base_guideline, dict):
            t = base_guideline.get("title")
            if isinstance(t, str):
                base_title = t.strip()
        if not base_title:
            base_title = "Downloaded App"
        return {"title": base_title, "content": content}

    for child in _iter_revealed_subquestions(node):
        child_qid = str(child.get("questionId", ""))
        child_text = (child.get("text") or "").strip()
        heading = f"Subquestion {child_qid}" if child_qid else "Subquestion"
        if child_text:
            add_text(child_text, heading=heading)

        child_g = child.get("guideline")
        if isinstance(child_g, dict):
            for it in child_g.get("content", []) if isinstance(child_g.get("content"), list) else []:
                if isinstance(it, dict):
                    add_item(it)

    if not content:
        return None

    base_title = ""
    if isinstance(base_guideline, dict):
        t = base_guideline.get("title")
        if isinstance(t, str):
            base_title = t.strip()
    if not base_title:
        base_title = f"Master Guidance for Q{node.get('questionId', '')}".strip()

    return {"title": base_title, "content": content}



# -----------------------------
# Ask a node (supports multi-turn prefix messages + Injection)
# -----------------------------

def ask_node(
    model,
    processor, # unused for NVILA, passed for signature parity
    image_ref: str,
    node: Dict[str, Any],
    max_new_tokens: int,
    do_sample: bool,
    temperature: float,
    top_p: float,
    json_repair: bool,
    include_raw: bool,
    include_vlm_input: bool,
    messages_prefix: Optional[List[Dict[str, Any]]] = None,
    include_image_in_this_turn: bool = True,
) -> Tuple[Dict[str, Any], str, Optional[Dict[str, Any]]]:
    """
    Constructs the prompt for a single questionnaire node and gets the VLM's response.
    """
    q_text = (node.get("text") or "").strip()
    group_type = get_group_type(node) or "radio"
    options = get_options_for_prompt(node)
    qid = str(node.get("questionId", ""))

    # 1109 is a static routing answer for this "Other" run.
    if qid in FORCED_STATIC_ROOT_QIDS and group_type == "radio":
        forced_idx = _pick_forced_option_index(qid, options)
        if forced_idx is None:
            forced_idx = _fallback_radio_choice(options)
            if forced_idx is None and options:
                forced_idx = options[0][0]
            LOGGER.warning("Forced static answer target not found for qid=%s; using fallback index=%s", qid, forced_idx)

        idx_to_text = {idx: txt for idx, txt in options}
        forced_text = idx_to_text.get(forced_idx, "forced option")
        parsed = {
            "step_1_visual_facts": "Static routing question. No visual inference required.",
            "step_2_policy_check": "This run is restricted to All Other App Types.",
            "step_3_conclusion": f"Forced selection applied: {forced_text}",
            "confidence": "high",
            "unknown": False,
            "selected_option_index": int(forced_idx) if forced_idx is not None else -1,
            "selected_option_indexes": [],
        }
        raw = json.dumps(parsed, ensure_ascii=False)
        vlm_input: Optional[Dict[str, Any]] = None
        if include_vlm_input:
            vlm_input = {
                "image_ref": image_ref,
                "question_prompt_text": "[STATIC_FORCED_ANSWER]",
                "chat_template_text": "",
                "messages": [],
                "static_forced_qid": qid,
                "static_forced_answer": forced_text,
            }
        return parsed, raw, vlm_input

    # Dynamic per-question master guideline:
    # base question guideline + revealed subquestion guidance.
    q_guideline = _build_dynamic_master_guideline(node)

    opt_guidelines: Dict[int, Dict[str, Any]] = {}
    opt_objs = get_option_objects(node)
    for o in opt_objs:
        if "guideline" in o and o["guideline"]:
            opt_guidelines[int(o["idx"])] = o["guideline"]

    prompt = build_question_prompt(
        q_text,
        group_type,
        options,
        question_guideline=q_guideline,
        option_guidelines=opt_guidelines,
    )

    user_content: List[Dict[str, Any]] = []
    if include_image_in_this_turn:
        user_content.append({"type": "image", "image": image_ref})
    user_content.append({"type": "text", "text": prompt})

    messages: List[Dict[str, Any]] = []
    if messages_prefix:
        messages.extend(messages_prefix)
    messages.append({"role": "user", "content": user_content})

    vlm_input: Optional[Dict[str, Any]] = None
    if include_vlm_input:
        # For logging purposes only
        chat_template_text = apply_chat_template_no_thinking(processor, messages)
        vlm_input = {
            "image_ref": image_ref,
            "question_prompt_text": prompt,
            "chat_template_text": chat_template_text,
            "messages": messages,
        }

    # First Pass Generation
    raw = generate_with_messages(
        model=model,
        processor=processor,
        messages=messages,
        max_new_tokens=max_new_tokens,
        do_sample=do_sample,
        temperature=temperature,
        top_p=top_p,
    )

    parsed = extract_first_json_value(raw)
    
    # Optional Second Pass: Repair Invalid JSON
    if not isinstance(parsed, dict) and json_repair:
        repaired = force_json_repair(model, processor, raw, max_new_tokens)
        if isinstance(repaired, dict):
            parsed = repaired

    # Final Fallback if still invalid
    if not isinstance(parsed, dict):
        parsed = {"unknown": True, "confidence": "low", "evidence": "Model did not return valid JSON."}

    # Selection Validation & Repair Logic
    allowed = {idx for idx, _ in options}
    unknown_flag = _coerce_bool(parsed.get("unknown"))

    def _do_repair(repair_prefix: str) -> Tuple[Optional[Dict[str, Any]], str, Optional[Dict[str, Any]]]:
        """Helper to run a repair turn specifically for selection errors."""
        prompt2 = repair_prefix + prompt
        user_content2: List[Dict[str, Any]] = []
        
        if include_image_in_this_turn:
            user_content2.append({"type": "image", "image": image_ref})
        user_content2.append({"type": "text", "text": prompt2})

        messages2: List[Dict[str, Any]] = []
        if messages_prefix:
            messages2.extend(messages_prefix)
        messages2.append({"role": "user", "content": user_content2})

        vlm_input2: Optional[Dict[str, Any]] = None
        if include_vlm_input:
            chat_template_text2 = apply_chat_template_no_thinking(processor, messages2)
            vlm_input2 = {
                "image_ref": image_ref,
                "question_prompt_text": prompt2,
                "chat_template_text": chat_template_text2,
                "messages": messages2,
            }

        raw2 = generate_with_messages(
            model=model,
            processor=processor,
            messages=messages2,
            max_new_tokens=max_new_tokens,
            do_sample=False, # Repairs should be deterministic
            temperature=1.0,
            top_p=1.0,
        )

        parsed2 = extract_first_json_value(raw2)
        if not isinstance(parsed2, dict) and json_repair:
            repaired2 = force_json_repair(model, processor, raw2, max_new_tokens)
            if isinstance(repaired2, dict):
                parsed2 = repaired2

        return (parsed2 if isinstance(parsed2, dict) else None), raw2, vlm_input2

    if not unknown_flag and options:
        if group_type == "radio":
            sel = _coerce_int(parsed.get("selected_option_index"))
            # If selection is invalid, try to repair
            if sel is None or sel not in allowed:
                parsed2, raw2, vlm_input2 = _do_repair(RADIO_SELECTION_REPAIR_PREFIX)
                if parsed2 is not None:
                    parsed = parsed2
                    raw = raw2
                    if include_vlm_input and vlm_input2 is not None:
                        vlm_input = vlm_input2

                # If still invalid, force fallback
                sel3 = _coerce_int(parsed.get("selected_option_index"))
                unk3 = _coerce_bool(parsed.get("unknown"))
                if (not unk3) and (sel3 is None or sel3 not in allowed):
                    fb = _fallback_radio_choice(options)
                    parsed = {
                        "selected_option_index": int(fb) if fb is not None else -1,
                        "selected_option_indexes": [],
                        "unknown": False,
                        "confidence": "low",
                        "evidence": "Forced fallback radio selection because the model did not select a valid option index.",
                    }
        else: # Checkbox
            sels = _coerce_int_list(parsed.get("selected_option_indexes"))
            sels = [i for i in sels if i in allowed]
            # If no selection made (and not unknown), try to repair
            if len(sels) == 0:
                parsed2, raw2, vlm_input2 = _do_repair(CHECKBOX_SELECTION_REPAIR_PREFIX)
                if parsed2 is not None:
                    parsed = parsed2
                    raw = raw2
                    if include_vlm_input and vlm_input2 is not None:
                        vlm_input = vlm_input2

                # If still invalid, force fallback
                sels3 = _coerce_int_list(parsed.get("selected_option_indexes"))
                unk3 = _coerce_bool(parsed.get("unknown"))
                sels3 = [i for i in sels3 if i in allowed]
                if (not unk3) and len(sels3) == 0:
                    parsed["selected_option_index"] = -1
                    parsed["selected_option_indexes"] = _fallback_checkbox_choice(options)
                    parsed["unknown"] = False
                    if parsed.get("confidence") not in ("high", "medium", "low"):
                        parsed["confidence"] = "low"
                    if not isinstance(parsed.get("evidence"), str) or not parsed["evidence"].strip():
                        parsed["evidence"] = "Forced fallback checkbox selection because the model returned no valid checkbox selections."

    return parsed, raw, vlm_input


def _mk_step(
    node_section: str,
    node_text: str,
    group_type: str,
    parsed: Dict[str, Any],
    options: List[Tuple[int, str]],
    include_raw: bool,
    raw_text: Optional[str],
    include_vlm_input: bool,
    vlm_input: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Helper to format the final output step dictionary."""
    ans = normalize_answer(parsed, group_type, options)
    step: Dict[str, Any] = {
        "section": node_section,
        "question": node_text,
        "type": group_type,
        "answer": ans,
    }
    if include_raw and raw_text is not None:
        step["raw_model_text"] = raw_text
    if include_vlm_input and vlm_input is not None:
        step["vlm_input"] = vlm_input
    return step


# -----------------------------
# Generic chain traversal (The Engine)
# -----------------------------

def _traverse_chain_generic(
    model,
    processor, # unused for NVILA, passed for signature parity
    image_ref: str,
    root: Dict[str, Any],
    max_new_tokens: int,
    do_sample: bool,
    temperature: float,
    top_p: float,
    json_repair: bool,
    max_depth: int,
    max_questions: int,
    include_raw: bool,
    include_vlm_input: bool,
    history_max_steps: int,
    root_prefill: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Performs a recursive Depth-First Search (DFS) on the questionnaire tree.
    
    How it works with the Hybrid Plan:
    1. It visits the Root Node (e.g., Violence Root).
    2. 'ask_node' sees the ID (e.g. 1110) and injects the MASTER_GUIDELINE (Merged Definitions).
    3. The VLM answers "Yes" or "No" based on those strict definitions.
    4. If "No": The loop ends (Fast).
    5. If "Yes": The code looks at the 'reveals' list in the JSON and recursively 
       visits the child nodes (Specific Types), maintaining the chat history.
    """
    section = root.get("section") if isinstance(root.get("section"), str) else "Unknown"
    chain_name = section
    if section == MISC_SECTION:
        q = (root.get("text") or "").strip().replace("\n", " ")
        short = (q[:80] + "...") if len(q) > 80 else q
        chain_name = f"Miscellaneous: {short}"

    steps: List[Dict[str, Any]] = []
    visited_qids = set()
    hidden_qids = set()
    stop_reason = "completed"

    base_messages = _build_chain_base_messages(image_ref)

    def dfs(
        node: Dict[str, Any],
        depth: int,
        history_messages: List[Dict[str, Any]],
        node_prefill: Optional[Dict[str, Any]] = None,
    ) -> None:
        nonlocal stop_reason
        if depth > max_depth:
            stop_reason = "max_depth"
            return
        if len(steps) >= max_questions:
            stop_reason = "max_questions"
            return

        qid = node.get("questionId")
        if isinstance(qid, str):
            if qid in visited_qids or qid in hidden_qids:
                return
            visited_qids.add(qid)

        group_type = get_group_type(node)
        if group_type is None:
            return

        options = get_options_for_prompt(node)
        opt_objs = get_option_objects(node)
        opt_by_idx = {int(o["idx"]): o for o in opt_objs}

        # Scope history to an activated subchain only:
        # - depth 0: root question gets no history
        # - depth 1: first child gate under a root also starts fresh
        # - depth >= 2: descendants inherit only branch-local history built inside that gate
        hist_trimmed = [] if depth <= 1 else _truncate_history_messages(history_messages, history_max_steps)

        prefill = node_prefill
        if depth == 0 and prefill is None:
            prefill = root_prefill

        if prefill is not None:
            parsed = dict(prefill.get("parsed") or {})
            raw = prefill.get("raw") or ""
            vlm_input = prefill.get("vlm_input") if include_vlm_input else None
        else:
            # ask_node handles the MASTER_GUIDELINE injection automatically
            parsed, raw, vlm_input = ask_node(
                model=model,
                processor=processor,
                image_ref=image_ref,
                node=node,
                max_new_tokens=max_new_tokens,
                do_sample=do_sample,
                temperature=temperature,
                top_p=top_p,
                json_repair=json_repair,
                include_raw=include_raw,
                include_vlm_input=include_vlm_input,
                messages_prefix=base_messages + hist_trimmed,
                include_image_in_this_turn=False,
            )

        steps.append(
            _mk_step(
                node_section=str(node.get("section") or section),
                node_text=str(node.get("text") or ""),
                group_type=group_type,
                parsed=parsed,
                options=options,
                include_raw=include_raw,
                raw_text=raw,
                include_vlm_input=include_vlm_input,
                vlm_input=vlm_input,
            )
        )

        # Update History
        history_user_text = _build_history_turn_text(node)
        history_assistant_json = _schema_json_for_history(parsed, group_type)
        history_next = hist_trimmed + [
            {"role": "user", "content": history_user_text},
            {"role": "assistant", "content": json.dumps(history_assistant_json, ensure_ascii=False)},
        ]

        ans = normalize_answer(parsed, group_type, options)
        if ans.get("unknown") is True:
            stop_reason = "unknown"
            return

        # --- LOGIC FOR REVEALING CHILDREN ---
        
        # 1. Radio Buttons (Select One)
        if group_type == "radio":
            sel_idx = _coerce_int(parsed.get("selected_option_index"))
            
            # Fallback if model returned text
            if sel_idx is None and isinstance(ans.get("answer"), str):
                for idx, txt in options:
                    if txt == ans["answer"]:
                        sel_idx = idx
                        break

            if sel_idx is None or sel_idx not in opt_by_idx:
                # If selection is invalid, we can't traverse deeper
                stop_reason = "no_or_none" 
                return

            # Hiding logic (some options explicitly hide other questions)
            for hid in (opt_by_idx[sel_idx].get("hidesQuestionIds") or []):
                if isinstance(hid, str) and hid:
                    hidden_qids.add(hid)

            # Revealing logic (Standard DFS)
            reveals = opt_by_idx[sel_idx].get("reveals")
            if isinstance(reveals, list) and reveals:
                batched_child_prefills: Dict[str, Dict[str, Any]] = {}
                if depth == 0 and str(node.get("section") or "") == DOWNLOADED_APP_SECTION:
                    batch_children = [child for child in reveals if isinstance(child, dict)]
                    if batch_children:
                        for chunk in _chunk_nodes(batch_children, 2):
                            chunk_prefills = ask_main_roots_batched(
                                model=model,
                                processor=processor,
                                image_ref=image_ref,
                                roots=chunk,
                                max_new_tokens=max_new_tokens,
                                do_sample=do_sample,
                                temperature=temperature,
                                top_p=top_p,
                                json_repair=json_repair,
                                include_vlm_input=include_vlm_input,
                            )
                            batched_child_prefills.update(chunk_prefills)
                for child in reveals:
                    if isinstance(child, dict):
                        child_history = [] if depth == 0 else history_next
                        child_qid = str(child.get("questionId", "")).strip()
                        child_prefill = batched_child_prefills.get(child_qid) if batched_child_prefills else None
                        dfs(child, depth + 1, child_history, node_prefill=child_prefill)
                        if stop_reason in ("unknown", "max_depth", "max_questions"):
                            return
            return

        # 2. Checkboxes (Select Multiple)
        sel_idxs = _coerce_int_list(parsed.get("selected_option_indexes"))
        
        # Fallback if model returned text
        if not sel_idxs and isinstance(ans.get("checked"), list):
            txt_to_idx = {txt: idx for idx, txt in options}
            for t in ans["checked"]:
                if isinstance(t, str) and t in txt_to_idx:
                    sel_idxs.append(txt_to_idx[t])

        sel_idxs = [i for i in sel_idxs if i in opt_by_idx]
        if not sel_idxs:
            stop_reason = "no_or_none"
            return

        # Hiding logic
        for iidx in sel_idxs:
            for hid in (opt_by_idx[iidx].get("hidesQuestionIds") or []):
                if isinstance(hid, str) and hid:
                    hidden_qids.add(hid)

        # Revealing logic
        for iidx in sel_idxs:
            reveals = opt_by_idx[iidx].get("reveals")
            if isinstance(reveals, list) and reveals:
                for child in reveals:
                    if isinstance(child, dict):
                        child_history = [] if depth == 0 else history_next
                        dfs(child, depth + 1, child_history)
                        if stop_reason in ("unknown", "max_depth", "max_questions"):
                            return

    dfs(root, 0, [])
    return {"chain_name": chain_name, "steps": steps, "stop_reason": stop_reason}


# -----------------------------
# Traverse chain dispatcher
# -----------------------------

def traverse_chain(
    model,
    processor,
    image_ref: str,
    root: Dict[str, Any],
    max_new_tokens: int,
    do_sample: bool,
    temperature: float,
    top_p: float,
    json_repair: bool,
    max_depth: int,
    max_questions: int,
    include_raw: bool,
    include_vlm_input: bool,
    history_max_steps: int,
    root_prefill: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Routes all sections to the generic traversal.
    We no longer need specialized 'traverse_violence', 'traverse_sexuality', etc. functions
    because 'ask_node' now handles the specialization via injection.
    """
    return _traverse_chain_generic(
        model=model,
        processor=processor,
        image_ref=image_ref,
        root=root,
        max_new_tokens=max_new_tokens,
        do_sample=do_sample,
        temperature=temperature,
        top_p=top_p,
        json_repair=json_repair,
        max_depth=max_depth,
        max_questions=max_questions,
        include_raw=include_raw,
        include_vlm_input=include_vlm_input,
        history_max_steps=history_max_steps,
        root_prefill=root_prefill,
    )


# -----------------------------
# Multi-worker app locking + safe output writing
# -----------------------------

def app_key_from_record(rec: Dict[str, Any]) -> str:
    """Generates a unique string identifier for the app being processed."""
    app = rec.get("app")
    if isinstance(app, dict):
        app_id = app.get("appId")
        if isinstance(app_id, str) and app_id.strip():
            return "appId:" + app_id.strip()
        url = app.get("store_url")
        if isinstance(url, str) and url.strip():
            return "store_url:" + url.strip()
    img = rec.get("image_ref")
    if isinstance(img, str) and img.strip():
        return "image_ref:" + img.strip()
    return "unknown:" + sha256_text(json.dumps(rec, sort_keys=True, ensure_ascii=False))


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
    """
    Attempts to create an exclusive lock file for a specific app.
    Returns (True, lock_path, done_path) if successful.
    """
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
                f.write(json.dumps({"owner": owner, "ts": time.time(), "app_key": app_key}, ensure_ascii=False) + "\n")
            return True, lock_path, done_path
        except FileExistsError:
            if lock_is_stale(lock_path):
                try:
                    stale_name = lock_path.with_suffix(f".stale.{int(time.time())}.lock")
                    lock_path.replace(stale_name)
                except Exception:
                    pass
                continue
            return False, lock_path, done_path

    return False, lock_path, done_path


def release_app_lock(lock_path: Path, owner: str) -> None:
    """Releases the lock only if we still own it."""
    try:
        txt = lock_path.read_text(encoding="utf-8").strip()
        obj = json.loads(txt) if txt.startswith("{") else {}
        if obj.get("owner") == owner:
            lock_path.unlink()
    except Exception:
        pass


class OutputFileLock:
    """Exclusive lock for updating the shared output JSON list."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.fp = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fp = self.lock_path.open("a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX)
        else:
            LOGGER.warning("fcntl not available; shared output writes are not safely locked on this platform.")
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.fp and fcntl is not None:
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
        finally:
            if self.fp:
                self.fp.close()
                self.fp = None


def append_records_safely(output_path: Path, new_records: List[Dict[str, Any]]) -> int:
    if not new_records:
        return 0
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")
    with OutputFileLock(lock_path):
        existing = read_existing_results(output_path)
        processed = build_processed_keyset(existing)
        filtered = [r for r in new_records if isinstance(r.get("image_key"), str) and r["image_key"] not in processed]
        if not filtered:
            return 0
        existing.extend(filtered)
        atomic_write_json(output_path, existing)
        return len(filtered)


def write_app_done(done_path: Path, app_key: str, owner: str, num_records: int) -> None:
    done_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = done_path.with_suffix(done_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"app_key": app_key, "owner": owner, "ts": time.time(), "records": num_records}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(done_path)


# -----------------------------
# CLI / Main
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run IARC OTHER questionnaire on images with Qwen (Hybrid Logic).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input-dir", help="Directory of images to annotate.")
    g.add_argument("--input-json", help="JSON list containing image references or Google Play app dumps.")

    p.add_argument("--questionnaire-json", required=True, help="Path to iarc_questionnaire_organized_flow.json")
    p.add_argument("--output-json", default="iarc_chain_results.json", help="Output JSON file (shared across workers).")

    p.add_argument("--model-name", default="Qwen/Qwen3.5-9B", help="HF model id.")
    p.add_argument("--conv-mode", default="auto", help="[Ignored for Qwen; kept for CLI compatibility].")
    
    p.add_argument("--device-map", default="auto", help="Transformers device_map.")
    p.add_argument("--dtype", default="float16", choices=["auto", "float16", "bfloat16", "float32"])
    p.add_argument("--attn-implementation", default="sdpa", help="e.g. sdpa or flash_attention_2")
    
    p.add_argument("--max-new-tokens", type=int, default=1024)
    p.add_argument("--do-sample", action="store_true", help="Enable sampling (otherwise greedy).")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--top-p", type=float, default=0.9)

    p.add_argument("--recursive", action="store_true", help="Recursively scan --input-dir.")
    p.add_argument("--exts", default=".jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff", help="Comma-separated extensions.")
    p.add_argument("--max-images", type=int, default=None, help="Optional limit for testing (do NOT use for production).")

    p.add_argument("--no-resume", action="store_true", help="Disable resume; overwrite output at startup. Not safe for multi-worker.")
    p.add_argument("--json-repair", action="store_true", help="If parsing fails, run a second-pass JSON repair.")

    p.add_argument("--screenshot-policy", default="first", choices=["first", "all", "index", "random"])
    p.add_argument("--screenshot-index", type=int, default=0)
    p.add_argument("--max-screenshots-per-app", type=int, default=1)

    p.add_argument("--max-depth", type=int, default=25)
    p.add_argument("--max-questions-per-chain", type=int, default=80)

    p.add_argument("--url-cache-dir", default="./_image_cache", help="Where to cache downloaded screenshots.")
    p.add_argument("--url-timeout", type=int, default=20, help="HTTP timeout seconds for downloads.")
    p.add_argument("--url-retries", type=int, default=1, help="Retries for URL downloads.")
    p.add_argument("--no-download-urls", action="store_true", help="Do not download URLs; pass URLs directly to the VLM.")

    p.add_argument("--include-raw", action="store_true", help="Include raw model text per question (debug).")
    p.add_argument(
        "--include-vlm-input",
        action="store_true",
        help="Include per-question VLM input (messages + prompt + chat_template_text). WARNING: output becomes very large.",
    )

    p.add_argument(
        "--history-max-steps",
        type=int,
        default=6,
        help="How many previous Q/A steps to include in the multi-turn chat context per chain (each step = user+assistant).",
    )

    p.add_argument("--app-lock-dir", default="./_app_locks", help="Directory for per-app lock files (shared).")
    p.add_argument("--app-done-dir", default="./_app_done", help="Directory for per-app done markers (shared).")
    p.add_argument("--lock-ttl-seconds", type=int, default=6 * 60 * 60, help="Break lock if older than this (seconds). 0 disables.")

    return p.parse_args()


# -----------------------------
# Other mode overrides
# -----------------------------
# Keep the engine unchanged, but switch section/root selection and
# guideline injection to All-Other-App-Types questionnaire semantics.

APP_TYPE_SECTION = "App Type"
DOWNLOADED_APP_SECTION = "Downloaded App"
USER_CONTENT_SHARING_SECTION = "User Content Sharing"
ONLINE_CONTENT_SECTION = "Online Content"
PROMOTION_SALE_SECTION = "Promotion or Sale of Age-Restricted Products or Activities"

MISC_SECTION = "Miscellaneous"
SECTION_ALIASES = {}
MAIN_CHAIN_SECTIONS = [
    DOWNLOADED_APP_SECTION,
    USER_CONTENT_SHARING_SECTION,
    ONLINE_CONTENT_SECTION,
    PROMOTION_SALE_SECTION,
    MISC_SECTION,
]

MASTER_GUIDELINES = {}


def load_chain_roots(questionnaire_path: Path) -> List[Dict[str, Any]]:
    questionnaire = load_questionnaire(questionnaire_path)
    roots = find_chain_roots(questionnaire)
    LOGGER.info("Loaded %d chain root(s).", len(roots))
    return roots


def compute_image_key(raw_image_ref: str) -> str:
    if raw_image_ref.startswith("file://"):
        local = file_uri_to_path(raw_image_ref)
        if local and local.exists():
            return "sha256:" + sha256_file(local)
    return "urlsha256:" + sha256_text(raw_image_ref)


def main() -> None:
    args = parse_args()
    configure_logging()

    output_path = Path(args.output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.no_resume:
        LOGGER.warning("--no-resume will overwrite the output. Do NOT use this with multiple terminals.")
        atomic_write_json(output_path, [])
        processed = set()
    else:
        existing = read_existing_results(output_path)
        processed = build_processed_keyset(existing)
        if processed:
            LOGGER.info("Resume enabled: found %d previously processed images.", len(processed))

    chain_roots = load_chain_roots(Path(args.questionnaire_json))

    # Load Qwen model
    model, processor = load_model_and_processor(
        model_name=args.model_name,
        conv_mode=args.conv_mode,
        device_map=args.device_map,
        dtype_str=args.dtype,
        attn_implementation=args.attn_implementation,
    )

    records: List[Dict[str, Any]] = []
    if args.input_dir:
        exts = tuple(e.strip() for e in args.exts.split(",") if e.strip())
        img_paths = list_images_in_dir(Path(args.input_dir), recursive=args.recursive, exts=exts)
        for pth in img_paths:
            records.append({"image_ref": pth.resolve().as_uri(), "local_path": str(pth.resolve()), "source": "dir"})
    else:
        records = load_image_records_from_json(
            Path(args.input_json),
            screenshot_policy=args.screenshot_policy,
            screenshot_index=args.screenshot_index,
            max_screenshots_per_app=args.max_screenshots_per_app,
        )

    if args.max_images is not None:
        records = records[: max(0, args.max_images)]
        LOGGER.warning("--max-images is set; apps will NOT be marked done (safe for testing).")

    app_order: List[str] = []
    app_to_records: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        ak = app_key_from_record(rec)
        rec["app_key"] = ak
        if ak not in app_to_records:
            app_to_records[ak] = []
            app_order.append(ak)
        app_to_records[ak].append(rec)

    LOGGER.info("Queued %d image(s) across %d app(s).", len(records), len(app_order))

    cache_dir = Path(args.url_cache_dir)
    download_urls = not args.no_download_urls

    lock_dir = Path(args.app_lock_dir)
    done_dir = Path(args.app_done_dir)
    owner = f"{socket.gethostname()}:{os.getpid()}"

    for app_idx, app_key in enumerate(app_order, start=1):
        app_records = app_to_records.get(app_key, [])
        if not app_records:
            continue

        _, done_path = _lock_file_paths(lock_dir, done_dir, app_key)
        if done_path.exists():
            LOGGER.info("Skipping app %d/%d (done): %s", app_idx, len(app_order), app_key)
            continue

        acquired, lock_path, done_path = try_acquire_app_lock(
            lock_dir=lock_dir,
            done_dir=done_dir,
            app_key=app_key,
            owner=owner,
            ttl_seconds=args.lock_ttl_seconds,
        )
        if not acquired:
            LOGGER.info("Skipping app %d/%d (locked by another worker): %s", app_idx, len(app_order), app_key)
            continue

        LOGGER.info("Processing app %d/%d: %s", app_idx, len(app_order), app_key)

        new_out_records: List[Dict[str, Any]] = []

        try:
            for rec in app_records:
                raw_image_ref = (rec.get("image_ref") or "").strip()
                if not raw_image_ref:
                    continue

                image_key = compute_image_key(raw_image_ref)
                if image_key in processed:
                    continue

                image_ref_used, dl_err = ensure_local_image_ref(
                    raw_image_ref,
                    cache_dir=cache_dir,
                    timeout_s=args.url_timeout,
                    retries=args.url_retries,
                    download_urls=download_urls,
                )

                if dl_err:
                    out_rec = dict(rec)
                    out_rec.update(
                        {
                            "image_key": image_key,
                            "model_name": args.model_name,
                            "image_ref_used": image_ref_used,
                            "error": dl_err,
                            "chains": [],
                        }
                    )
                    new_out_records.append(out_rec)
                    processed.add(image_key)
                    continue

                batched_root_answers: Dict[str, Dict[str, Any]] = {}
                for chunk in _chunk_nodes(chain_roots, 2):
                    chunk_answers = ask_main_roots_batched(
                        model=model,
                        processor=processor,
                        image_ref=image_ref_used,
                        roots=chunk,
                        max_new_tokens=args.max_new_tokens,
                        do_sample=args.do_sample,
                        temperature=args.temperature,
                        top_p=args.top_p,
                        json_repair=args.json_repair,
                        include_vlm_input=args.include_vlm_input,
                    )
                    batched_root_answers.update(chunk_answers)

                chains_out: List[Dict[str, Any]] = []
                for root in chain_roots:
                    qid = str(root.get("questionId", "")).strip()
                    root_prefill = batched_root_answers.get(qid)
                    chains_out.append(
                        traverse_chain(
                            model=model,
                            processor=processor,
                            image_ref=image_ref_used,
                            root=root,
                            max_new_tokens=args.max_new_tokens,
                            do_sample=args.do_sample,
                            temperature=args.temperature,
                            top_p=args.top_p,
                            json_repair=args.json_repair,
                            max_depth=args.max_depth,
                            max_questions=args.max_questions_per_chain,
                            include_raw=args.include_raw,
                            include_vlm_input=args.include_vlm_input,
                            history_max_steps=args.history_max_steps,
                            root_prefill=root_prefill,
                        )
                    )

                out_rec = dict(rec)
                out_rec.update(
                    {
                        "image_key": image_key,
                        "model_name": args.model_name,
                        "image_ref_used": image_ref_used,
                        "chains": chains_out,
                    }
                )
                new_out_records.append(out_rec)
                processed.add(image_key)

            added = append_records_safely(output_path, new_out_records)
            LOGGER.info("App write: added %d record(s) -> %s", added, output_path)

            if args.max_images is None:
                write_app_done(done_path, app_key=app_key, owner=owner, num_records=len(app_records))
                LOGGER.info("Marked app done: %s", app_key)
            else:
                LOGGER.info("Not marking app done due to --max-images testing mode.")

        finally:
            release_app_lock(lock_path, owner=owner)

    LOGGER.info("Done.")
    LOGGER.info("Output: %s", output_path)
    LOGGER.info("Done markers: %s", done_dir)
    LOGGER.info("Locks: %s", lock_dir)


if __name__ == "__main__":
    main()

    
