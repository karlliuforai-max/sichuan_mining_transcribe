# Sichuan Mining Transcribe

四川矿业会议转写纠错与会议纪要生成 skill。针对四川话、乐山话、普通话混杂的矿业会议转写文本(PDF/TXT/MD),输出纠错全文、可直接发送的会议纪要和疑点清单。

核心特性:

- **快**:6 路并行调用 DeepSeek,5 万字约 3~4 分钟。
- **稳**:逐段断点落盘,中断重跑自动续传;单段失败用本地规则兜底,流程必定跑完。
- **进度可见**:每 5 秒刷新进度和预计剩余时间,不会无声卡死。
- **越用越准**:自动 diff 提取纠错对并累积,高频候选自动注入后续提示词;`review` 命令一键晋升为本地硬规则。

## 安装

```bash
git clone https://github.com/karlliuforai-max/sichuan_mining_transcribe.git
cd sichuan_mining_transcribe
python3 -m venv .venv && source .venv/bin/activate
pip install -r sichuan-mining-transcribe/requirements.txt
```

配置 DeepSeek API Key(项目根目录建 `.env`):

```text
DEEPSEEK_API_KEY=your-deepseek-api-key
```

## 使用

```bash
cd sichuan-mining-transcribe
python transcribe.py "/path/to/会议.txt"               # 日常默认(flash)
python transcribe.py "/path/to/会议.pdf" --tier pro    # 重要会议,质量更高、约慢一倍
python transcribe.py "/path/to/会议.txt" --local       # 离线模式,只用本地规则
python transcribe.py review                           # 审核高频候选,晋升为确认规则
```

常用参数:`-o 输出目录`(默认 `outputs/runs/<日期-会议名>/`)、`--workers 并行数`(默认 6)。

产出三个文件:

| 文件 | 内容 |
|---|---|
| `纠错全文.md` | 纠错后的完整转写 |
| `会议纪要.md` | 核心结论、关键数据表、商务条件、行动项、风险分歧 |
| `疑点清单.md` | 数字变更、失败兜底段、待人工复核项 |

## 知识库

`sichuan-mining-transcribe/knowledge/` 下三个文件构成纠错知识:

- `glossary.yml` — 矿业术语表,注入每次纠错提示词,可手工维护。
- `confirmed_rules.yml` — 已确认规则:`replacements` 本地硬替换;`context_hints` 注入提示词由模型判断。
- `candidates.jsonl` — 自动累积的候选纠错对(带频次),出现 ≥2 场会议后自动注入提示词;经 `review` 采纳后晋升为硬规则。

安全策略:不自动改动数字、金额、人名、公司名、矿山名;涉数字的模型变更一律写入疑点清单供人工复核。

## 隐私与 Git 约定

默认不提交:`.env`、真实会议转写源、`outputs/`。仓库只保存代码、通用术语规则和文档。

## 作为 Skill 使用

skill 入口为 `sichuan-mining-transcribe/SKILL.md`,在 Claude Code / Codex 等环境中由 agent 读取后按其指引运行 `transcribe.py`。
