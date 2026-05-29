# Sichuan Mining Transcribe

四川矿业会议转写纠错与会议纪要生成 skill。适用于四川话、乐山话、普通话混杂的矿业会议转写材料，可从 PDF/TXT/MD 输入生成纠错全文、会议纪要、关键数据表、行动项、疑点清单和学习候选。

## 功能

- 支持 PDF、TXT、MD 转写源输入。
- 针对四川话、乐山话、普通话混杂和矿业术语误识别做纠错。
- 支持本地规则模式、DeepSeek 增强模式和自动模式。
- 输出 `03_corrected.md`、`04_minutes.md`、关键数据 CSV、行动项 CSV、疑点清单和学习候选。
- 支持文本或 JSONL 实时进度，方便接入 Codex、Claude Code、OpenClaw、飞书机器人和服务器任务。
- 内置基础术语、纠错规则、会议模板和历史学习候选，陌生环境 clone 后即可有一个可用起点。

## 项目结构

```text
.
├── README.md
├── .env.example
├── docs/                         # 项目背景、开发记录、交接文档和沟通过程
│   ├── background/
│   ├── development/
│   ├── handover/
│   └── timeline/
├── sichuan-mining-transcribe/     # 可复用 Codex skill 和处理脚本
│   ├── agents/                    # Agent 配置样例
│   ├── knowledge/                 # 基础学习候选和私有知识
│   ├── outputs/                   # 运行结果，默认不提交
│   │   ├── runs/                  # 正式处理结果
│   │   └── archive/               # 历史样例和测试输出
│   ├── references/                # 术语、规则、模板和质检清单
│   └── scripts/                   # CLI 脚本
└── 转写源/                         # 本地真实会议源文件，默认不提交
```

## 一键准备

### 1. 克隆仓库

```bash
git clone git@github.com:karlliuforai-max/sichuan_mining_transcribe.git
cd sichuan_mining_transcribe
```

没有 SSH key 时可用 HTTPS：

```bash
git clone https://github.com/karlliuforai-max/sichuan_mining_transcribe.git
cd sichuan_mining_transcribe
```

### 2. 创建 Python 环境

macOS、Linux、阿里云 ECS 都推荐使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r sichuan-mining-transcribe/requirements.txt
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r sichuan-mining-transcribe\requirements.txt
```

### 3. 配置 DeepSeek API Key

DeepSeek 模式需要 `DEEPSEEK_API_KEY`。可以使用环境变量：

```bash
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

也可以使用 `.env` 文件：

```bash
cp .env.example .env
```

然后编辑 `.env`：

```text
DEEPSEEK_API_KEY=your-deepseek-api-key
```

`.env` 默认被 `.gitignore` 忽略，不要把真实 key 提交到公开仓库。

## 快速运行

把转写源放到本地任意目录，例如 `转写源/`，然后运行：

```bash
python3 sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input "转写源/05-29 埃塞矿业投资合作洽谈.txt" \
  --output sichuan-mining-transcribe/outputs/runs/2026-05-29-ethiopia-mining-investment \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir sichuan-mining-transcribe/knowledge \
  --learn-policy candidate
```

处理整个目录：

```bash
python3 sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input 转写源 \
  --output sichuan-mining-transcribe/outputs/runs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir sichuan-mining-transcribe/knowledge \
  --learn-policy candidate
```

离线本地模式不调用外部模型，适合隐私敏感或 API 不可用时兜底：

```bash
python3 sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input 转写源 \
  --output sichuan-mining-transcribe/outputs/runs/run-local \
  --mode local \
  --progress text \
  --knowledge-dir sichuan-mining-transcribe/knowledge \
  --learn-policy candidate
```

输出目录通常包含：

```text
01_extracted.txt
02_normalized.txt
03_corrected.md
04_minutes.md
05_key_data.csv
06_action_items.csv
07_uncertain_items.md
08_learn_candidates.jsonl
09_semantic_minutes.json
manifest.json
rule_changes.jsonl
run_log.jsonl
```

## 模式选择

- `--mode deepseek`：生产推荐。需要 `DEEPSEEK_API_KEY`，质量最好。
- `--deepseek-tier flash`：默认推荐，速度快，成本较低。
- `--deepseek-tier pro`：复杂、长篇或高价值会议可用，质量更稳。
- `--mode local`：只用本地规则，不上传文本，适合离线或隐私场景。
- `--mode auto`：检测到 `DEEPSEEK_API_KEY` 时走 DeepSeek，否则走本地。
- `--progress text`：人类可读进度，适合终端和人工查看。
- `--progress jsonl`：机器可读进度，适合 OpenClaw、飞书机器人和自动化系统。

## 作为 Codex Skill 使用

本项目的 skill 入口是：

```text
sichuan-mining-transcribe/SKILL.md
```

在 Codex 或兼容 skill 的环境中，使用时让 agent 先读取 `SKILL.md`，再按 `references/workflow.md` 执行。推荐命令仍然是：

```bash
python scripts/transcribe_pipeline.py \
  --input /path/to/source-or-dir \
  --output outputs/runs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir knowledge \
  --learn-policy candidate
```

如果从 skill 目录内运行，先进入目录：

```bash
cd sichuan-mining-transcribe
python scripts/transcribe_pipeline.py \
  --input ../转写源 \
  --output outputs/runs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress text \
  --knowledge-dir knowledge \
  --learn-policy candidate
```

## 阿里云服务器部署

以 Ubuntu ECS 为例：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
git clone https://github.com/karlliuforai-max/sichuan_mining_transcribe.git
cd sichuan_mining_transcribe
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r sichuan-mining-transcribe/requirements.txt
cp .env.example .env
```

编辑 `.env` 写入 `DEEPSEEK_API_KEY`。服务器上建议用绝对路径运行，方便接入定时任务或外部系统：

```bash
/path/to/sichuan_mining_transcribe/.venv/bin/python \
  /path/to/sichuan_mining_transcribe/sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input /data/transcripts \
  --output /data/transcribe_outputs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress jsonl \
  --knowledge-dir /path/to/sichuan_mining_transcribe/sichuan-mining-transcribe/knowledge \
  --learn-policy candidate
```

如需后台执行：

```bash
nohup /path/to/sichuan_mining_transcribe/.venv/bin/python \
  /path/to/sichuan_mining_transcribe/sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input /data/transcripts \
  --output /data/transcribe_outputs/run-001 \
  --mode deepseek \
  --deepseek-tier flash \
  --progress jsonl \
  --knowledge-dir /path/to/sichuan_mining_transcribe/sichuan-mining-transcribe/knowledge \
  --learn-policy candidate \
  > /data/transcribe_outputs/run-001.log 2>&1 &
```

## 飞书连接 OpenClaw 方案

推荐架构：

```text
飞书机器人 / 飞书应用
        ↓
OpenClaw 工作流或 Agent
        ↓
服务器上的本项目 CLI
        ↓
outputs/runs/<run-id>/
        ↓
把 04_minutes.md、05_key_data.csv、06_action_items.csv 回传飞书
```

OpenClaw 调用时建议使用 `--progress jsonl`，这样可以逐行读取进度并回写到飞书消息：

```bash
/path/to/sichuan_mining_transcribe/.venv/bin/python \
  /path/to/sichuan_mining_transcribe/sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input "$INPUT_PATH" \
  --output "$OUTPUT_DIR" \
  --mode deepseek \
  --deepseek-tier flash \
  --progress jsonl \
  --knowledge-dir /path/to/sichuan_mining_transcribe/sichuan-mining-transcribe/knowledge \
  --learn-policy candidate
```

建议在 OpenClaw 中约定这些变量：

- `INPUT_PATH`：飞书上传文件下载后的本地路径，可以是 PDF/TXT/MD 或目录。
- `OUTPUT_DIR`：本次运行输出目录，建议包含日期和会议名。
- `RUN_ID`：唯一任务 ID，方便飞书消息、日志和输出目录对应。

飞书回传优先级建议：

1. `04_minutes.md`：会议纪要，适合直接发回群或写入文档。
2. `07_uncertain_items.md`：疑点清单，提醒人工复核。
3. `05_key_data.csv`：关键数据，可写入表格。
4. `06_action_items.csv`：行动项，可转任务或待办。
5. `03_corrected.md`：纠错全文，通常作为附件或归档。

安全建议：

- 飞书下载的原始会议文件不要提交 Git。
- DeepSeek API Key 放在服务器环境变量或 `.env`，不要写进 OpenClaw prompt。
- 对外部群聊只回传纪要和疑点，纠错全文按业务敏感级别决定是否回传。
- 生产环境建议按 `RUN_ID` 单独建输出目录，避免并发任务互相覆盖。

## 知识与规则

项目内置三类知识：

- `references/glossary.yml`：矿业术语、四川/乐山语境和高频词。
- `references/correction_rules.yml`：本地高置信纠错规则。
- `knowledge/learned_candidates.jsonl`：历史转写积累的候选知识，公开发布时一并提供，作为基础语料经验。

默认策略是保守的：

- 不自动确认学习候选。
- 不静默改动关键金额、价格、付款条件、公司名、矿山名和人名。
- 有疑问的内容写入 `07_uncertain_items.md`。

如果运行后产生新的 `08_learn_candidates.jsonl`，可人工审阅后再决定是否整理进 `references/correction_rules.yml` 或追加到私有知识库。

## 隐私与 Git 约定

默认不提交：

- `转写源/`
- `sichuan-mining-transcribe/outputs/`
- `.env`
- 任意真实 API key

公开提交：

- 代码和脚本。
- 通用术语、规则和模板。
- `sichuan-mining-transcribe/knowledge/learned_candidates.jsonl` 基础候选知识。
- `docs/` 中的项目开发和交接资料。

发布前可运行：

```bash
git status --short
git check-ignore -v .env 转写源/ sichuan-mining-transcribe/outputs/runs/run-001 || true
```

确认 `.env`、真实转写源和运行输出仍被忽略。

## 常见问题

### 缺少 PyYAML 或 pypdf

执行：

```bash
python -m pip install -r sichuan-mining-transcribe/requirements.txt
```

### DeepSeek 模式提示缺少 API Key

确认已设置：

```bash
echo "$DEEPSEEK_API_KEY"
```

或确认 `.env` 位于项目根目录，并包含：

```text
DEEPSEEK_API_KEY=your-deepseek-api-key
```

### PDF 无法提取或内容为空

先用本地模式验证：

```bash
python3 sichuan-mining-transcribe/scripts/transcribe_pipeline.py \
  --input /path/to/file.pdf \
  --output sichuan-mining-transcribe/outputs/runs/pdf-test \
  --mode local \
  --progress text
```

如果 `01_extracted.txt` 为空，说明 PDF 可能是扫描件，需要先做 OCR。

### 输出目录已有旧文件

每次运行建议使用新的输出目录，例如：

```text
sichuan-mining-transcribe/outputs/runs/2026-05-29-meeting-name
```

这样便于归档和回溯。
