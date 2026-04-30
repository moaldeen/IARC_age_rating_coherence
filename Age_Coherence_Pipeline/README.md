# Age Coherence Pipeline

This folder contains the policy-text pipeline used to derive age-coherence judgments from legal documents.

The stages do **not** use the same input format.

## Environment

Use the **same conda environment described in the repository root `README.md`**. This pipeline is part of the same release materials and does **not** require creating a separate environment just for `Age_Coherence_Pipeline`.

From the repository root:

```bash
conda create -n iarc_qwen python=3.10 -y
conda activate iarc_qwen
pip install -r requirements.txt
export PYTORCH_KERNEL_CACHE_PATH=$HOME/.cache/torch_kernels
mkdir -p "$PYTORCH_KERNEL_CACHE_PATH"
```

Then run the commands below from the repository root.

## Stage Overview

1. `step1_classification_agent.py`
   - input: app metadata JSON
   - output: extracted age-related statements JSON

2. `step2_thematic_agent.py`
   - input: Stage 1 extracted statements JSON
   - output: sentence-level thematic JSONL

3. `age_mapper.py`
   - input: Stage 2 thematic JSONL
   - output: sentence-level age-mapping JSONL

4. `stage5_eval_single_json_final.py`
   - input: Stage 3 age-mapping JSONL
   - output: coherence / contradiction analysis JSONL

## Stage 1

Script:

- `step1_classification_agent.py`

### Input format

Stage 1 expects a JSON list of app metadata records with fields such as:

- `description`
- `privacyPolicy`
- `external_documents`
- `contentRating`
- `appId`

### Copied Step 1 inputs

- `inputs/step1/family_input_exact.json`
  - exact family-style input reflected by the active final script defaults
- `inputs/step1/general_apps_input.json`
- `inputs/step1/games_input.json`

### Example commands

Family:

```bash
python Age_Coherence_Pipeline/step1_classification_agent.py \
  --input-json Age_Coherence_Pipeline/inputs/step1/family_input_exact.json \
  --output-json Age_Coherence_Pipeline/qwen_child_safety_statements_family.json
```

General apps:

```bash
python Age_Coherence_Pipeline/step1_classification_agent.py \
  --input-json Age_Coherence_Pipeline/inputs/step1/general_apps_input.json \
  --output-json Age_Coherence_Pipeline/qwen_child_safety_statements_general_apps.json
```

Games:

```bash
python Age_Coherence_Pipeline/step1_classification_agent.py \
  --input-json Age_Coherence_Pipeline/inputs/step1/games_input.json \
  --output-json Age_Coherence_Pipeline/qwen_child_safety_statements_games.json
```

## Stage 2

Script:

- `step2_thematic_agent.py`

### Input format

Stage 2 expects the **output of Stage 1**, not raw app metadata. The input is a JSON file whose records contain fields such as:

- `app_id`
- `app_name`
- `age_rating`
- `description_statements`
- `privacy_policy_statements`
- `external_links_statements`

### Example Stage 2 inputs copied here

- `examples/step2_input/qwen_child_safety_statements_family.json`
- `examples/step2_input/qwen_child_safety_statements_games.json`

### Example command

```bash
python Age_Coherence_Pipeline/step2_thematic_agent.py \
  --model Qwen/Qwen2.5-32B-Instruct \
  --input-json Age_Coherence_Pipeline/examples/step2_input/qwen_child_safety_statements_family.json \
  --output Age_Coherence_Pipeline/thematic_family.jsonl
```

## Stage 3

Script:

- `age_mapper.py`

### Input format

Stage 3 expects the **output of Stage 2**, not Stage 1 JSON. The input is JSONL with one record per sentence, containing fields such as:

- `app_name`
- `app_id`
- `store_age_rating`
- `source`
- `sentence_index`
- `sentence`
- `model_result`

### Example Stage 3 inputs copied here

- `examples/step3_input/thematic_family_final.jsonl`
- `examples/step3_input/thematic_games_final.jsonl`

### Example command

```bash
python Age_Coherence_Pipeline/age_mapper.py \
  --input-json Age_Coherence_Pipeline/examples/step3_input/thematic_family_final.jsonl \
  --output-json Age_Coherence_Pipeline/age_maping_family.jsonl \
  --model-name Qwen/Qwen2.5-32B-Instruct
```

## Stage 5

Script:

- `stage5_eval_single_json_final.py`

### Input format

Stage 5 expects the **output of Stage 3**, not raw app metadata and not Stage 1 extraction JSON. The input is JSONL with fields such as:

- `app_name`
- `app_id`
- `store_age_rating`
- `sentence`
- `age_code`
- `min_age`
- `parent_permission_required`
- `model_result`

### Example Stage 5 inputs copied here

- `examples/step5_input/age_maping_apps_merged.jsonl`
- `examples/step5_input/age_maping_family.jsonl`
- `examples/step5_input/age_maping_games.jsonl`

### Example Stage 5 outputs copied here

- `examples/step5_output/coherent_analysis_apps.jsonl`
- `examples/step5_output/coherent_analysis_family_table_only.jsonl`
- `examples/step5_output/coherent_analysis_games.jsonl`

## Notes

- The active final Step 1 script default model is `Qwen/Qwen3-14B`.
- The example Stage 2 command above is based on the recorded family run:
  - `Thematic_agent_final.py --input-json new_age/qwen_child_safety_statements_family.json`
- The example Stage 3 command is based on the recorded family run:
  - `age_mapper.py --input-json new_age/thematic_family_final.jsonl`
- The Stage 5 script currently has hardcoded family paths in its source, so users may need to edit those paths or adapt the script before running it on another copied input file.
