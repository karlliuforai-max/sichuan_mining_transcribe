---
name: sichuan-mining-transcribe
description: Use this skill to correct AI-generated transcripts from Sichuan/Leshan Mandarin dialect mining meetings and generate structured meeting minutes, key data tables, action items, review notes, and learning candidates from PDF or TXT sources. It supports cross-platform CLI execution, local rules, optional DeepSeek enhancement, and reusable mining-domain knowledge.
---

# Sichuan Mining Transcribe

Use this skill when the user provides PDF/TXT transcripts from mining meetings involving Sichuan dialect, Leshan dialect, Mandarin, and mining-domain terminology, and wants corrected text plus meeting minutes.

## Workflow

1. Read `references/workflow.md` for the full process.
2. Run the CLI from this skill directory or call the scripts directly:

```bash
python scripts/transcribe_pipeline.py \
  --input /path/to/source-or-dir \
  --output outputs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir knowledge \
  --learn-policy candidate
```

3. Default production-quality output uses `--mode deepseek --deepseek-tier flash`. Use `--deepseek-tier pro` for higher quality or difficult transcripts. Use `--mode local` only for privacy-sensitive or offline fallback runs.
   - `flash` uses `deepseek-v4-flash` with thinking disabled for speed.
   - `pro` uses `deepseek-v4-pro` with thinking disabled by default for stable JSON output.
   - `--progress text` prints human-readable live progress.
   - `--progress jsonl` prints machine-readable events for agents and automations.
4. Preserve both primary outputs:
   - `03_corrected.md`
   - `04_minutes.md`
5. Review:
   - `07_uncertain_items.md`
   - `08_learn_candidates.jsonl`

## References

- Use `references/glossary.yml` for mining terms and dialect context.
- Use `references/correction_rules.yml` for high-confidence and context-sensitive corrections.
- Use `references/meeting_templates.md` for minutes structure.
- Use `references/prompt_templates.md` when invoking an external model.
- Use `references/quality_checks.md` before presenting final results.

## Safety

- Do not silently alter key numbers, prices, payment terms, company names, mine names, or personal names.
- Put uncertain corrections in review notes.
- Do not auto-promote learned candidates into confirmed corrections unless the user explicitly approves.
- Do not write to Feishu documents in MVP mode. Produce stable Markdown/CSV/JSONL files for later Feishu CLI integration.
