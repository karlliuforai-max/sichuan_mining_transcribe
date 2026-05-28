# Sichuan Mining Transcribe

四川矿业会议转写纠错与会议纪要生成 skill。

## 功能

- 支持 PDF/TXT/MD 转写源输入。
- 针对四川话、乐山话、普通话混杂和矿业术语误识别做纠错。
- 支持本地规则模式和 DeepSeek 增强模式。
- 输出纠错全文、会议纪要、关键数据、行动项、疑点清单和学习候选。
- 支持实时进度输出，便于 Codex、Claude Code、OpenClaw、飞书等调用。

## 快速运行

```bash
python3 sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input /path/to/transcripts \
  --output sichuan-mining-transcribe/outputs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir sichuan-mining-transcribe/knowledge \
  --learn-policy candidate
```

DeepSeek 模式需要通过环境变量提供 API key：

```bash
export DEEPSEEK_API_KEY="your-key"
```

也可以复制 `.env.example` 为 `.env`，脚本会自动读取：

```bash
cp .env.example .env
```

离线本地模式：

```bash
python3 sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input /path/to/transcripts \
  --output sichuan-mining-transcribe/outputs/run-local \
  --mode local \
  --progress text
```

## 隐私约定

真实会议 PDF、转写全文、运行输出和私有学习数据默认不提交到 git：

- `转写源/`
- `sichuan-mining-transcribe/outputs/`
- `sichuan-mining-transcribe/knowledge/*.jsonl`

仓库只保存代码、通用规则、模板和开发文档。
