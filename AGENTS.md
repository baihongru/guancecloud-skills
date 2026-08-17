# AGENTS.md

本项目用于构建和迭代多个观测云(Guance Cloud)相关的 AI 技能(skill)。在此项目中开发、修改或调试 skill 时,必须遵守以下规范。

## 目录结构

```
guancecloud-skills/
├── .agents/skills/                            # 所有 skill 的源码目录(受版本控制)
│   └── <skill-name>/                          # 单个 skill,自包含;内部结构遵循 Agent Skills 规范
│       └── SKILL.md                           # (必须)核心指令;其他文件按规范与 skill 需要组织
├── references/                                # 项目级参考文档(受版本控制)
│   ├── agent-skills-specification.md          # Agent Skills 规范(权威来源)
│   └── conventional-commits-specification.md  # 约定式提交规范(权威来源)
├── temp/                                      # 临时数据目录(被 gitignore)
│   └── <skill-name>/                          # 与 skill 同名,存放该 skill 的临时产出
├── .trae/                                     # IDE 运行时目录(被 gitignore,不放源码)
└── AGENTS.md                                  # 本文件,项目级 agent 指引
```

## 硬性规则

### skill 内部结构
- skill 目录的内部结构遵循 Agent Skills 规范,权威来源:`references/agent-skills-specification.md`。
- 规范要求:每个 skill 必须有 `SKILL.md`(含 YAML frontmatter,`name` 和 `description` 必填);可选目录 `scripts/`、`references/`、`assets/`;允许按需添加其他文件和目录。
- 修改 skill 结构前先读规范,不要凭单个已有 skill 的结构臆测规则。

### skill 存放位置
- 所有 skill 必须放在 `.agents/skills/<skill-name>/` 下,使用 kebab-case 命名(如 `guance-rca`、`guance-raa`)。
- 不要把 skill 源码放在项目根目录或 IDE 运行时目录(如 `.trae/skills/`)下。这些目录被 gitignore,源码放那里会失去版本控制。
- 若 IDE 通过其 UI 创建了项目技能(通常落在 IDE 运行时目录如 `.trae/skills/`),应立即将其移动到 `.agents/skills/` 并删除运行时目录下的副本,避免重名冲突。

### skill 目录是只读定义
- skill 目录(`.agents/skills/<name>/`)是**能力定义**,不是数据落地区。
- skill 运行时产生的报告、中间结果、调试 scratch,一律写到 `temp/<skill-name>/`,不要写进 skill 自己的目录。保持 skill 目录无状态、可分享、可 zip 导出。

### references 与 temp 的边界
- skill 内的 `references/`(即 `.agents/skills/<name>/references/`)受版本控制,存放被该 skill 的 `SKILL.md` 引用的方法论、规则、playbook 等文档。
- 项目根的 `references/`(即 `references/`)受版本控制,存放项目级参考文档(如 Agent Skills 规范),被 `AGENTS.md` 引用。
- `temp/` 被 gitignore,存放运行时产出和调试 scratch。
- **关键约束**:如果 `SKILL.md` 或 `AGENTS.md` 引用了一个文件,该文件必须位于受版本控制的位置(skill 内 `references/` 或项目根 `references/`),绝不能放在 `temp/`——否则 clone 仓库的人拿不到该资源,引用会断。
- 当移动或重命名 `references/` 下文件时,必须同步更新引用它的 `SKILL.md` 或 `AGENTS.md` 中的路径。

### 临时数据目录
- 所有临时文件写入 `temp/<skill-name>/`,子目录名与 skill 同名,一一对应。
- 不要把多个 skill 的临时文件混在 `temp/` 根下,也不要建与 skill 不同名的子目录。
- `temp/` 整体被 `**/temp` 忽略,无需手动排除;清理某 skill 的临时数据用 `rm -rf temp/<skill-name>/*`。

## 开发流程

1. **新建 skill**:在 `.agents/skills/` 下创建 `<skill-name>/` 目录,按 `references/agent-skills-specification.md` 规范写 `SKILL.md` 及按需创建 `scripts/`、`references/`、`assets/` 等。
2. **引用资源**:被 `SKILL.md` 引用的文档放 skill 内 `references/`,被 `AGENTS.md` 引用的项目级文档放项目根 `references/`;两者都受版本控制,不要放 `temp/`。
3. **开发/调试**:运行时产出和 scratch 写到 `temp/<skill-name>/`,可随时清空。
4. **加载测试**:`.agents/skills/` 符合 Agent Skills 开放规范,支持该规范的 IDE/工具可自动发现并加载其中的 skill;具体启用方式参见各 IDE 文档。
5. **提交**:skill 源码正常 commit;`temp/` 自动被忽略,无需手动排除。提交信息遵循 `references/conventional-commits-specification.md` 规范。
