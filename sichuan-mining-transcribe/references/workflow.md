# Workflow

## Default CLI

```bash
python scripts/transcribe_pipeline.py \
  --input 转写源 \
  --output outputs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir knowledge \
  --learn-policy candidate
```

## Stages

1. Extract text from PDF/TXT/MD.
2. Normalize Unicode, spacing, page markers, speaker labels, and transcript noise.
3. Apply local correction rules.
4. Call DeepSeek for contextual correction when `--mode deepseek`, or when `--mode auto` and `DEEPSEEK_API_KEY` is present.
5. Generate corrected transcript, meeting minutes, key data CSV, action item CSV, uncertain items, learning candidates, and run log.

## Mode Defaults

- `deepseek` with `--deepseek-tier flash` is the default production-quality mode.
- `deepseek --deepseek-tier pro` uses `deepseek-v4-pro` for difficult transcripts or higher quality.
- `local` is the safest offline fallback and never sends transcript content externally.
- `agent` currently behaves like local in the CLI. The calling agent may perform additional review after files are generated.
- `auto` calls DeepSeek only if `DEEPSEEK_API_KEY` exists.
- `deepseek` requires `DEEPSEEK_API_KEY`; otherwise the run fails with a clear error.
- The default DeepSeek tier follows the current official docs: `flash` uses `deepseek-v4-flash` with thinking disabled for speed; `pro` uses `deepseek-v4-pro` with thinking enabled for higher-quality difficult transcripts.
- Override thinking behavior only when needed with `--deepseek-thinking enabled` or `--deepseek-thinking disabled`.

## Progress

The CLI writes `run_log.jsonl` for every run. By default, it also prints human-readable progress lines so users can see elapsed time and character-level progress during long DeepSeek calls. Use `--progress jsonl` when an agent or automation needs machine-readable stdout.

Each progress event includes:

- `stage`
- `message`
- `done_chars`
- `total_chars`
- `elapsed_seconds`
- `stage_elapsed_seconds`

During DeepSeek correction, progress is emitted before and after each chunk, for example:

```text
[总耗时 73.6s | 本阶段 73.5s] DeepSeek：DeepSeek 纠错进行中：第 2/5 段，已完成 4500/13967 字（32.2%）
```

With `--progress jsonl`, stdout uses JSON Lines:

```json
{"stage":"llm","message":"DeepSeek 纠错进行中：第 2/5 段，已完成 4500/13967 字"}
```

## Knowledge Layout

- `references/`: built-in knowledge shipped with the skill.
- `knowledge/`: private project knowledge that grows over time.
- Use `--knowledge-dir` to point to another private knowledge directory later.
