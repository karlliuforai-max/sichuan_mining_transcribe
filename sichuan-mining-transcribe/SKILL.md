---
name: sichuan-mining-transcribe
description: Use this skill to correct AI-generated transcripts from Sichuan/Leshan dialect mining meetings and generate meeting minutes. Input is a PDF/TXT/MD transcript; output is corrected full text, ready-to-send minutes, and a review checklist. Supports parallel DeepSeek correction, checkpoint resume, live progress, and a self-improving correction knowledge base.
---

# Sichuan Mining Transcribe

用户提供四川话/乐山话/普通话混杂的矿业会议转写文本(PDF/TXT/MD),需要纠错全文和会议纪要时使用本 skill。

## 部署与首次运行自检

本 skill 自包含:整个 `sichuan-mining-transcribe/` 目录可整体拷贝到任意平台
(Claude Code `~/.claude/skills/`、Codex skills 目录、OpenClaw/Hermes 工作区等)即可使用。
知识库(`knowledge/`)随目录一起走,纠错经验在各处部署间共享。

首次在新环境运行前,agent 应依次自检(任一不满足则先修复再继续):

1. Python ≥ 3.9 可用。
2. 依赖已装:`pip install -r requirements.txt`(需要 `PyYAML`,处理 PDF 还需 `pypdf`)。
3. 凭据就绪:skill 目录下存在 `.env` 且含有效 `DEEPSEEK_API_KEY`
   (没有就 `cp .env.example .env` 后填入;纯离线场景可改用 `--local` 跳过)。
4. 网络可达 `api.deepseek.com`(`--local` 模式无此要求)。

## 运行方式

唯一入口是 `transcribe.py`,从 skill 目录运行:

```bash
python transcribe.py "/path/to/会议.txt"               # 日常默认(DeepSeek flash,6 路并行)
python transcribe.py "/path/to/会议.pdf" --tier pro    # 重要会议,质量更高、约慢一倍
python transcribe.py "/path/to/会议.txt" --local       # 离线,只用本地规则,不调 API
```

要点:

- DeepSeek 模式需要 `DEEPSEEK_API_KEY`(自动从工作区 `.env` 读取)。
- 5 万字约 3~4 分钟;进度每 5 秒打印一次,**应以前台方式运行让用户看到进度**。
- 运行中断后,用相同输出目录(`-o`)重跑即可断点续传,已完成的段不会重算。
- 即使部分段落 API 失败,流程也会用本地规则版兜底跑完,失败段记入疑点清单。

## 产出

输出目录(默认 `outputs/runs/<日期-会议名>/`)下三个文件:

- `纠错全文.md` — 纠错后的完整转写
- `会议纪要.md` — 含核心结论、关键数据表、商务条件、行动项、风险分歧,可直接发参会方
- `疑点清单.md` — 数字变更、API 失败段、模型标记的待复核项

运行结束后向用户呈现会议纪要正文,并提醒查看疑点清单。

## 自进化机制

- 每次运行自动 diff 提取纠错对,累积到 `knowledge/candidates.jsonl`;出现 ≥2 场会议的候选会自动注入后续运行的提示词(越用越准,无需人工干预)。
- 运行结束如提示有可晋升候选,建议用户运行 `python transcribe.py review` 逐条 y/n 审核,采纳的进入 `knowledge/confirmed_rules.yml` 成为本地硬替换规则。
- `knowledge/glossary.yml`(术语表)和 `confirmed_rules.yml` 可手工编辑。

## 安全红线

- 不改动数字、金额、日期、人名、公司名、矿山名;涉及数字的模型变更一律进疑点清单。
- 会议内容商业敏感:原始转写源和 `outputs/` 不提交 Git,不外发。
