# Agent Skills 规范

> 来源:[https://agentskills.io/specification](https://agentskills.io/specification)
> 抓取日期:2026-08-17
> 本文件是 Agent Skills 开放规范的本地副本,作为本项目 skill 开发的权威依据。如与线上规范冲突,以线上规范为准。

## 目录结构

一个 skill 是一个目录,至少包含一个 `SKILL.md` 文件:

```
skill-name/
├── SKILL.md      # 必须:元数据 + 指令
├── scripts/      # 可选:可执行代码
├── references/   # 可选:文档
├── assets/       # 可选:模板、资源
└── ...           # 任意其他文件或目录
```

## SKILL.md 格式

`SKILL.md` 必须包含 YAML frontmatter,后接 Markdown 正文。

### Frontmatter

| 字段 | 必填 | 约束 |
|---|---|---|
| `name` | 是 | 最多 64 字符。仅小写字母、数字和连字符。不得以连字符开头或结尾。 |
| `description` | 是 | 最多 1024 字符。非空。描述 skill 做什么以及何时使用。 |
| `license` | 否 | 许可证名称或指向打包许可证文件的引用。 |
| `compatibility` | 否 | 最多 500 字符。说明环境要求(目标产品、系统包、网络访问等)。 |
| `metadata` | 否 | 任意键值映射,用于额外元数据(字符串键到字符串值)。 |
| `allowed-tools` | 否 | 空格分隔的预批准工具字符串。(实验性) |

**最小示例:**

```
---
name: skill-name
description: A description of what this skill does and when to use it.
---
```

**带可选字段示例:**

```
---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
---
```

### `name` 字段

必填的 `name` 字段:

- 必须 1-64 字符
- 只能含 unicode 小写字母数字字符(`a-z`、`0-9`)和连字符(`-`)
- 不得以连字符(`-`)开头或结尾
- 不得含连续连字符(`--`)
- 必须与父目录名匹配

**有效示例:** `pdf-processing`、`data-analysis`、`code-review`

**无效示例:**

- `PDF-Processing` —— 不允许大写
- `-pdf` —— 不能以连字符开头
- `pdf--processing` —— 不允许连续连字符

### `description` 字段

必填的 `description` 字段:

- 必须 1-1024 字符
- 应同时描述 skill 做什么以及何时使用
- 应包含帮助 agent 识别相关任务的具体关键词

**好示例:** `description: Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction.`

**差示例:** `description: Helps with PDFs.`

### `license` 字段

可选的 `license` 字段:

- 指定应用于该 skill 的许可证
- 建议保持简短(许可证名称或打包许可证文件名)

**示例:** `license: Proprietary. LICENSE.txt has complete terms`

### `compatibility` 字段

可选的 `compatibility` 字段:

- 若提供须 1-500 字符
- 仅当 skill 有特定环境要求时才包含
- 可说明目标产品、所需系统包、网络访问需求等
- 多数 skill 不需要此字段

**示例:**

- `compatibility: Designed for Claude Code (or similar products)`
- `compatibility: Requires git, docker, jq, and access to the internet`
- `compatibility: Requires Python 3.14+ and uv`

### `metadata` 字段

可选的 `metadata` 字段:

- 字符串键到字符串值的映射
- 客户端可用此存储 Agent Skills 规范未定义的额外属性
- 建议键名有一定独特性以避免冲突

**示例:**

```
metadata:
  author: example-org
  version: "1.0"
```

### `allowed-tools` 字段

可选的 `allowed-tools` 字段:

- 空格分隔的预批准可运行工具字符串
- 实验性,各 agent 实现支持程度可能不同

**示例:** `allowed-tools: Bash(git:*) Bash(jq:*) Read`

### 正文内容

Frontmatter 之后的 Markdown 正文包含 skill 指令,无格式限制。推荐章节:

- 分步指令
- 输入输出示例
- 常见边界情况

agent 一旦决定激活某 skill,会加载整个文件。考虑将较长的 `SKILL.md` 内容拆分到引用文件中。

## 可选目录

skill 目录除必需的 `SKILL.md` 外可包含任意文件和目录。以下约定是组织常见内容类型的建议。

### `scripts/`

包含 agent 可运行的可执行代码。脚本应:

- 自包含或清晰记录依赖
- 包含有用的错误信息
- 优雅处理边界情况

支持的语言取决于 agent 实现,常见选项包括 Python、Bash、JavaScript。

### `references/`

包含 agent 按需阅读的额外文档:

- `REFERENCE.md` —— 详细技术参考
- `FORMS.md` —— 表单模板或结构化数据格式
- 领域特定文件(`finance.md`、`legal.md` 等)

保持各引用文件聚焦。agent 按需加载这些文件,因此较小文件意味着更少的上下文占用。

### `assets/`

包含静态资源:

- 模板(文档模板、配置模板)
- 图片(图表、示例)
- 数据文件(查找表、schema)

## 渐进式披露

agent 加载 skill 是*渐进式*的,仅当任务需要时才拉取更多细节。skill 应利用此结构:

1. **元数据**(~100 tokens):启动时为所有 skill 加载 `name` 和 `description` 字段
2. **指令**(建议 < 5000 tokens):skill 激活时加载完整 `SKILL.md` 正文
3. **资源**(按需):文件(如 `scripts/`、`references/`、`assets/` 中的)仅在需要时加载

主 `SKILL.md` 保持在 500 行以内。详细参考材料移到独立文件。

## 文件引用

在 skill 中引用其他文件时,使用相对于 skill 根的路径:

```
See [the reference guide](references/REFERENCE.md) for details.

Run the extraction script: scripts/extract.py
```

文件引用保持从 `SKILL.md` 向下一层。避免深层嵌套引用链。

## 校验

使用 [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref) 参考库校验 skill:

```
skills-ref validate ./my-skill
```

此命令检查 `SKILL.md` frontmatter 是否有效并遵循所有命名约定。
