# 贡献指南 / Contributing

感谢你有兴趣参与 **odoo-ai-agent**。这个项目的愿景是：**AI 跑流程，人做判断，
AI 不能自行走完全程**。任何让这个愿景更稳、更通用、更安全的贡献都欢迎。

## 项目定位（先读，避免方向跑偏）

- **AI 操作层**：Agent 服务独立于 Odoo 运行，通过 Odoo API 操作 Odoo 全流程，
  不做"模块内嵌 AI"。
- **审批完备**：写操作一律过权限门（`agent/gate.py`），必须人工确认；
  AI 提交动作前用 Odoo 的 tier 审批规则预检（`agent/tier_precheck.py`）。
- **全程留痕**：AI 的每个动作（含被拒绝的）写入审计日志（`agent/audit.py`）。
- **全开源免费**：MIT 协议；服务收费只针对"需要人出面"的实施/运维。

## 开发环境

- **Python 3.12+**，依赖见 `requirements.txt`（仅 `odoorpc`，保持轻量）。
- 连接配置走 `.env`（复制 `.env.example` 填写），不要提交真实凭据。
- Odoo 端建议安装 OCA `base_tier_validation`（本项目的预检依赖它），
  并预先配置好 `tier.definition` 审批规则。

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env      # 填写你的 Odoo 连接
venv/bin/python main.py   # 最小闭环验证
```

## 代码规范

- Python 3.12+ 类型标注；公开函数写 docstring（中文或英文皆可，保持一致）。
- 新增文件放入 `agent/`；**Agent 与 Odoo 的交互只通过 `agent/odoo_client.py`**，
  不允许散落裸的 odoorpc 调用。
- 写操作必须经 `agent/gate.py` 的 `ActionGate`，禁止绕过权限门直写。
- 规则逻辑放 `agent/tier_precheck.py`，保持 gate 只负责"放行/拦截"。
- 外部新依赖需在 PR 说明理由。

## 提交规范（Conventional Commits）

```
feat: 新功能
fix: 修 bug
docs: 文档/注释
refactor: 重构（行为不变）
test: 测试
chore: 构建/杂项
```

示例：`feat: sales agent drafts quotations through the gate`。
一次提交只做一件事，消息写清"为什么"。

## 分支与 PR 流程

1. 从 `main` 切分支：`git checkout -b feat/your-change`；
2. 小步提交（见上）；
3. 推送到你的 fork，开 PR 到 `main`；
4. PR 描述：改了什么、为什么、如何验证（demo 输出贴出来）。

## 验证要求（PR 合并前必须）

- 跑通 `venv/bin/python main.py`（连接 + 只读）；
- 若改动涉及权限门：跑 `demo_approval.py --approve` 和 `--reject`，
  确认"提交→人审批→执行/拒绝→审计留痕"完整；
- 若改动涉及预检：跑 `setup_tier_rule.py` 后验证 `[命中]` / `[未命中]` 两条路径；
- 业务库不留脏数据（演示动作应自清理）。

## 安全红线

- **绝不提交** `.env`、密码、token、私钥、审计日志（`.gitignore` 已覆盖，
  新增敏感文件类型时同步更新 `.gitignore`）。
- 审计日志不得记录密码等敏感字段值。
- 发现凭据泄露，立即在 GitHub 上 revoke 并告知维护者。

## 行为准则

- 友善、就事论事；欢迎新人和跨领域（业务/运维/ERP）视角；
- 讨论围绕代码与设计，不针对个人；
- 中文或英文交流均可。
