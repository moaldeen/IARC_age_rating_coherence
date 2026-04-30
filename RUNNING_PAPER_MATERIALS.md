# Running Paper Materials

This repository includes the exact input files, questionnaire files, and batch scripts used for the paper runs below.

## Included Scripts

- `screenshot_guidlines_v2_batching_other_qwenBATCHED.py`
- `screenshot_guidlines_v2_batching_v2_qwenBATCHEd.py`

## Included Inputs

- `inputs/apps_other_local_input.json`
- `inputs/family_apps_strict_enriched.json`
- `inputs/gplay_enUS_top1000_games.json`

## Included Questionnaires

- `questionnaires/iarc_others_organized_flow_final_verified.json`
- `questionnaires/iarc_questionnaire_organized_flow_final_verified.json`

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
  --input-json inputs/family_apps_strict_enriched.json \
  --questionnaire-json questionnaires/iarc_questionnaire_organized_flow_final_verified.json \
  --output-json family_results_qwen35_screenshot_4.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 9 \
  --include-vlm-input \
  --history-max-steps 3 > nohup9.out 2>&1 &
```

### Games

```bash
CUDA_VISIBLE_DEVICES=1 nohup python -u screenshot_guidlines_v2_batching_v2_qwenBATCHEd.py \
  --input-json inputs/gplay_enUS_top1000_games.json \
  --questionnaire-json questionnaires/iarc_questionnaire_organized_flow_final_verified.json \
  --output-json games_results_2.json \
  --screenshot-policy all \
  --json-repair \
  --max-screenshots-per-app 9 \
  --include-vlm-input \
  --history-max-steps 3 > nohup2.out 2>&1 &
```
