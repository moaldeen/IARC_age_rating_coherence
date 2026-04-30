# Running Paper Materials

This repository contains the main scripts, input JSON files, questionnaire files, and screenshot subset bundle used for the paper materials release.

## Included Scripts

- `Qwen_IARC_guidlines_general_apps.py`
- `Qwen_IARC_guidlines_games_family.py`

## Included Inputs

- `inputs/general_apps_input.json`
- `inputs/family_input.json`
- `inputs/games_input.json`

## Included Questionnaires

- `questionnaires/iarc_others_organized_flow_final_verified.json`
- `questionnaires/iarc_questionnaire_organized_flow_final_verified.json`

## Included Screenshot Archive

- `final_inconsistinces_bundle.zip`

This archive is a smaller screenshot subset exported from `2contr/final_inconsistinces`. It is separate from the original local project data and does not modify any of the source screenshots.

## Environment Setup

The exact environment used in the study was:

```bash
conda activate /scratch/mshujaa/llava_hugingface/QwenVL_annotation/env2
cd /scratch/mshujaa/llava_hugingface/XRZOO/new_age/2contr
export PYTORCH_KERNEL_CACHE_PATH=/scratch/mshujaa/.cache/torch_kernels
mkdir -p "$PYTORCH_KERNEL_CACHE_PATH"
```

For a fresh environment, you can use:

```bash
conda create -n iarc_qwen python=3.10 -y
conda activate iarc_qwen
pip install -r requirements.txt
export PYTORCH_KERNEL_CACHE_PATH=$HOME/.cache/torch_kernels
mkdir -p "$PYTORCH_KERNEL_CACHE_PATH"
```

## Requirements

Install the Python dependencies with:

```bash
pip install -r requirements.txt
```

## How To Run

Run these commands from the repository root.

### General Apps

```bash
python Qwen_IARC_guidlines_general_apps.py \
  --input-json inputs/general_apps_input.json \
  --questionnaire-json questionnaires/iarc_others_organized_flow_final_verified.json \
  --output-json apps_other_results_1.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 20 \
  --include-vlm-input \
  --history-max-steps 3
```

### Family Apps

```bash
python Qwen_IARC_guidlines_games_family.py \
  --input-json inputs/family_input.json \
  --questionnaire-json questionnaires/iarc_questionnaire_organized_flow_final_verified.json \
  --output-json family_results_qwen35_3.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 9 \
  --include-vlm-input \
  --history-max-steps 3
```

### Games

```bash
python Qwen_IARC_guidlines_games_family.py \
  --input-json inputs/games_input.json \
  --questionnaire-json questionnaires/iarc_questionnaire_organized_flow_final_verified.json \
  --output-json games_results_qwen35.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 9 \
  --include-vlm-input \
  --history-max-steps 3
```
