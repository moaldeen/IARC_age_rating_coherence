# Running Paper Materials

This repository includes the exact input files, questionnaire files, and batch scripts used for the paper runs below.

## Included Scripts

- `screenshot_guidlines_v2_batching_other_qwenBATCHED.py`
- `screenshot_guidlines_v2_batching_v2_qwenBATCHEd.py`

## Included Inputs

- `inputs/apps_other_local_input.json`
- `inputs/family_apps_local_input.json`
- `inputs/gplay_xrzoo_local_input.json`

## Included Questionnaires

- `questionnaires/iarc_others_organized_flow_final_verified.json`
- `questionnaires/iarc_questionnaire_organized_flow_final_verified.json`

## Screenshot Bundle

The local input JSON files above point to the authors' original absolute screenshot paths. For a portable rerun, extract:

- `paper_screenshot_bundle.zip`

at the repository root, then use the bundled portable input JSON files inside:

- `paper_screenshot_bundle/inputs/apps_other_local_input.json`
- `paper_screenshot_bundle/inputs/family_apps_local_input.json`
- `paper_screenshot_bundle/inputs/gplay_xrzoo_local_input.json`

These bundled JSON files have their screenshot paths rewritten to the extracted `paper_screenshot_bundle/screenshots/...` layout.

## Run Commands

Run these commands from the repository root.

### Other Apps

```bash
CUDA_VISIBLE_DEVICES=0 nohup python -u screenshot_guidlines_v2_batching_other_qwenBATCHED.py \
  --input-json inputs/apps_other_local_input.json \
  --questionnaire-json questionnaires/iarc_others_organized_flow_final_verified.json \
  --output-json apps_other_results_1.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 20 \
  --include-vlm-input \
  --history-max-steps 3 > nohup1.out 2>&1 &
```

### Family Apps

```bash
CUDA_VISIBLE_DEVICES=0 nohup python -u screenshot_guidlines_v2_batching_v2_qwenBATCHEd.py \
  --input-json paper_screenshot_bundle/inputs/family_apps_local_input.json \
  --questionnaire-json questionnaires/iarc_questionnaire_organized_flow_final_verified.json \
  --output-json family_results_qwen35_3.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 9 \
  --include-vlm-input \
  --history-max-steps 3 > nohup3.out 2>&1 &
```

### Games

The exact local games run below used `screenshot_guidlines_v2_batching_v2_mimo.py`. That script is not yet copied into this repository.

```bash
CUDA_VISIBLE_DEVICES=0 nohup python -u screenshot_guidlines_v2_batching_v2_mimo.py \
  --input-json paper_screenshot_bundle/inputs/gplay_xrzoo_local_input.json \
  --questionnaire-json questionnaires/iarc_questionnaire_organized_flow_final_verified.json \
  --output-json games_results_20.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 9 \
  --include-vlm-input \
  --history-max-steps 3 > nohup20.out 2>&1 &
```
