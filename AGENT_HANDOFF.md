# Agent Handoff & Offline Bootstrap Guide (`auto_research`)

> **面向新节点上的 Antigravity 会话（Agent）**：
> 本离线包包含完整的 **Antigravity (Gemini 4 Carbon) × OpenAI Codex (GPT-6.2-Astra `ultra`) Auto Research 自治科研系统**，**完全不依赖 `gh` CLI 或 GitHub 网络访问**，在任意 Linux 单机或多节点集群上解压即可原位部署启用。

---

## 1. 给新节点 Agent 的标准部署步骤（30 秒到位）

如果用户将压缩包（`.tar.gz`）或解压后的 `auto_research` 目录交给你，请按以下步骤完成初始化：

### Step 1：确认安装位置并解压（如尚未解压）
推荐将 `auto_research` 放置在用户主目录或工作区根目录下（例如 `~/auto_research` 或 `/mnt/workspace/$USER/auto_research`）：
```bash
# 若当前收到的是 .tar.gz 压缩包（示例解压至 ~/auto_research）：
tar -xzf auto_research_*.tar.gz -C ~/
cd ~/auto_research
```

### Step 2：运行一键离线安装脚本（无需 GitHub / 外网）
```bash
bash ./install.sh
```
`./install.sh` 会自动完成以下本地配置并跑通 32 项端到端回归自检：
1. 将当前节点的真实绝对路径渲染进 `plugins/auto-research/mcp_config.json` 与 `plugins/auto-research/hooks.json`；
2. 创建软链接 `~/.local/bin/agy-research`、`~/.codex/astra-ro.config.toml`、`~/.codex/astra-code.config.toml`（若包内含离线 `vendor/mihomo` 二进制且本机尚未安装，还会自动安装至 `~/.local/bin/mihomo`）；
3. 注册 `~/.agents/plugins.json` 并执行 `agy plugin validate --strict`、`agy plugin install`、`agy plugin enable auto-research`；
4. 运行 `python3 scripts/self_check.py`，验证 Stdio MCP 协议（`astra_consult` / `astra_code` / `astra_resume` / `astra_review`，默认 `ultra` 推理强度）、`PreToolUse` 安全护栏、`Stop` 目标门禁及课题建库脚本全部就绪。

> **注意**：请检查 `~/.local/bin` 是否已在用户的 `~/.bashrc`（或 `~/.zshrc`）的 `PATH` 中；若不在，请追加 `export PATH="$HOME/.local/bin:$PATH"`。新安装的插件与 MCP Server 在**下一次新启动的 `agy` / `agy-research` 会话**中自动挂载生效。

---

## 2. 按需可选配置

### A. 本机 24/7 独立代理（脱离笔记本 SSH `-R` 反向隧道）
若该节点访问 Google / OpenAI 需要代理，且已有 Clash 订阅链接或导出的 YAML 文件（使用 `full_with_mihomo` 包时 `mihomo` 二进制已自动就绪）：
```bash
# 方式 1：用户在终端交互隐藏输入订阅链接（不回显、配置自动 0600 权限，默认锁定日本 1x 低倍率节点）
python3 ~/auto_research/proxy/setup_local_mihomo.py setup --prompt-url

# 方式 2：直接导入已下载的 Clash YAML 配置文件
python3 ~/auto_research/proxy/setup_local_mihomo.py setup --import-yaml /path/to/config.yaml

# 检查连通性（Google Antigravity / CloudCode PA / OpenAI API / GitHub / HuggingFace）
python3 ~/auto_research/proxy/setup_local_mihomo.py status
```

### B. 单机 / 多节点 GPU 与数据盘配置
- **单机模式（零配置开箱即用）**：不创建 `config/cluster.local.json` 时，`python3 scripts/cluster_ctl.py status` 会自动探测本机所有物理 GPU 及挂载磁盘（`/mnt/data*`），并支持 `launch-bg` / `check-job` 后台训练。
- **多节点集群模式**：
  ```bash
  cp config/cluster.example.json config/cluster.local.json
  # 按当前集群真实节点 IP、SSH 用户名、本地数据盘与只读保护目录编辑 config/cluster.local.json
  python3 scripts/cluster_ctl.py status
  ```

---

## 3. 启动长程自治科研任务标准流程

1. 在终端使用 `tmux` 持久守护启动新会话（自动加载已安装的 `auto-research` 插件与 `auto-research-codex` MCP）：
   ```bash
   ~/.local/bin/agy-research -t ar_<topic>
   ```
2. 若用户提供了调研或实验计划 Markdown 文档：
   - 调用 `bash scripts/init_experiment.sh projects/<topic> "<研究目标>" [大数据盘目录]` 初始化独立 Git 实验仓库与共享记忆总线（`RESEARCH_PLAN.md`、`RESEARCH_GOAL.json`、`EXPERIMENTS.tsv`、`BLOCKERS.md`）；
   - 使用 `astra_consult`（GPT-6.2-Astra `ultra`，`read-only`）联合审查实验设计与潜在数据泄漏风险；
   - 核心复杂算法模块使用 `astra_code`（`use_worktree: true`）在隔离 Git worktree 中实现并合入主分支，长时训练前使用 `astra_review` 独立审计；
   - 每轮实验真实完成后使用 `python3 scripts/log_experiment.py` 记入 `EXPERIMENTS.tsv`。
