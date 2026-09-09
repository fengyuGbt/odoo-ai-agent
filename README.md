# odoo-ai-agent —— Odoo「AI 操作层」服务骨架

## English

**odoo-ai-agent** is an **AI operation layer** for Odoo: an agent service that
runs outside Odoo and operates Odoo's full workflow through its API.

Core principle — **AI runs the flow, humans make the judgment, AI never walks
the whole process alone**:

1. **AI runs the flow**: order → approval → purchase → receiving → production → shipping → collection,
   driven by agents;
2. **Human makes the judgment**: every write action stops at an action gate
   (`agent/gate.py`) and waits for explicit human approval;
3. **Rule pre-check**: before submitting a write action, the agent evaluates the
   tier-approval rules configured in Odoo (OCA `base_tier_validation`) and
   presents the findings (which rules hit, who must approve) together with the
   pending request;
4. **Full audit trail**: every agent action — approved, rejected, blocked — is
   appended to a JSONL audit log.

**Status**: connection + read-only probing, action gate, audit trail, tier-rule
pre-check all working. Open source, MIT. Free to use; paid services only for
"human-in-the-loop" implementation/operations.

For contributors, see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 中文

一个独立于 Odoo 运行的服务，通过 Odoo 的 XML-RPC/JSON-RPC API 连接 Odoo，
作为整个「AI 操作层」项目的第一块地基。当前阶段已完成：连接 + 只读查询、
动作权限门（写操作必须人工确认）、审计留痕、规则引擎预检（对接 OCA
`base_tier_validation`）。

> **项目愿景：AI 操作层** —— 由 AI Agent 代替人操作 Odoo 的全功能流程，
> 但审批流保持完备：**AI 按事前规则做预检，人在关键节点做最终判断，
> AI 不能自行走完全程**。整个项目源代码开源、免费使用；只有"需要人出面"
> 的服务才收费。

## 核心规则（本项目的灵魂）

1. **AI 跑流程**：订单、采购、到货、生产、物流等流程性操作由 AI Agent 执行；
2. **人做判断**：每个写动作都停在权限门前，由人工拍板；
3. **规则预检**：AI 提交动作前先用 Odoo 的 tier 审批规则做核实，
   预检结果（命中哪些规则、需要谁审批）随请求一起呈现；
4. **全程留痕**：AI 的每个动作（含被拒绝的）都写入审计日志。

## 目录结构

```
ai-agent/
├── agent/
│   ├── __init__.py
│   ├── config.py        # 配置加载（环境变量 / .env）
│   ├── odoo_client.py   # Odoo API 客户端封装（odoorpc）
│   ├── audit.py         # 审计日志（JSONL，全程留痕）
│   ├── gate.py          # 动作权限门（写操作必须人工确认）
│   ├── tier_precheck.py # 规则引擎预检（对接 OCA base_tier_validation）
│   ├── domain_agent.py  # 领域 Agent 通用基类（发现→预检→提交确认→等审批）
│   ├── sales_agent.py   # 销售 Agent：草稿报价单 → 确认
│   └── purchase_agent.py # 采购 Agent：草稿采购单 → 确认
├── main.py              # 最小闭环演示：连接 + 只读查询
├── demo_approval.py     # 权限门演示：AI 提交 → 预检 → 人审批 → 执行/拒绝
├── sales_demo.py        # 销售 Agent 演示：录入 → 预检 → 审批 → 确认 → 清理
├── purchase_demo.py     # 采购 Agent 演示：录入 → 预检 → 审批 → 确认 → 清理
├── setup_tier_rule.py   # 创建演示 tier 审批规则（sale + purchase）
├── requirements.txt     # Python 依赖
├── .env.example         # 配置模板（复制为 .env 后填写）
└── README.md
```

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 2. 配置连接（复制模板并修改）
cp .env.example .env

# 3. 运行最小闭环验证
venv/bin/python main.py
```

预期输出：

```
[OK] connected to localhost:8069  db=erp19  user=admin (Administrator)
[OK] probing live record counts:
      res.users       2
      res.partner     1
      sale.order      0
      ...
[OK] closed session. minimal closed loop works.
```

## 动作权限门（demo_approval.py）

项目的核心规则：**AI 跑流程，人做判断，AI 不能自行走完全程**。权限门机制化地保证这一点：

- **只读**操作（search/read/count）自动放行；
- **写**操作（create/write/unlink/call）一律先提交为待审批请求，**必须由人确认后才会执行**；
- 每个动作（含被拒绝的）都写入审计日志 `audit/agent_audit.jsonl`。

```bash
venv/bin/python demo_approval.py              # 只提交，停在"等待人工审批"
venv/bin/python demo_approval.py --approve    # 提交 → 人批准 → 执行 → 审计
venv/bin/python demo_approval.py --reject     # 提交 → 人拒绝 → 不执行 → 审计
venv/bin/python demo_approval.py --read-only  # 只读模式，写操作直接拦截
```

演示用"创建草稿报价单"作为示例动作（--approve 执行后会自动清理，业务库保持干净）。

## 配置项

| 变量 | 说明 | 默认值 |
|---|---|---|
| `ODOO_HOST` | Odoo 服务器地址 | `localhost` |
| `ODOO_PORT` | Odoo HTTP 端口 | `8069` |
| `ODOO_DB` | 数据库名 | `erp19` |
| `ODOO_USER` | Odoo 登录账号 | `admin` |
| `ODOO_PASSWORD` | 登录密码 | `admin123` |
| `ODOO_PROTOCOL` | 协议（xmlrpc / jsonrpc） | `jsonrpc` |

## 规则引擎预检（tier_precheck.py）

权限门的 `precheck_hook` 已对接 **OCA `base_tier_validation`**：AI 提交写动作
时，先读取 Odoo 里配置的 `tier.definition` 审批规则，评估目标记录命中哪些
规则、需要谁审批，预检结果随待审批请求一起呈现给人工。

```bash
# 先创建一条演示规则（partner 3 的报价单需要审批）
venv/bin/python setup_tier_rule.py

# 命中规则：预检显示 [命中] 与审批人
venv/bin/python demo_approval.py

# 未命中规则：预检显示 [未命中]（仍停在人工审批——写操作永远需要人拍板）
venv/bin/python demo_approval.py --partner 1
```

演示输出（命中）：

```
[gate]   check  : pre-check: 1 tier rule(s) apply to sale.order:
[gate]   check  :   [命中] AI demo: partner 3 quotations need approval → 需 用户:Administrator 审批（任一审批人即可）
```

## 销售 Agent（领域 Agent 第一弹）

`agent/sales_agent.py` 是第一个领域 Agent：**盯着草稿报价单队列 → 规则预检 → 
把"确认订单"作为写动作提交权限门 → 人拍板后才执行**。它永远不自己确认
任何订单——这正是项目规则的体现。

```bash
venv/bin/python sales_demo.py --scan       # 只读扫描草稿报价单
venv/bin/python sales_demo.py              # 创建 demo 草稿，提交确认请求后停在 pending
venv/bin/python sales_demo.py --approve    # 完整闭环：创建→审批→确认→验证→取消→清理
```

`--approve` 完整闭环输出（节选）：

```
[sales-agent] S00020 (partner=Administrator, total=0.0) -> request #2 decision=approved
[verify] quotation state after confirmation = sale
[demo] cancelled 6 confirmed demo quotation(s)
[demo] cleaned 6 demo quotation(s)
```

**设计要点**：
- `run_once(only_ids=...)` 限定只处理指定记录——**demo 永远不会碰真实业务订单**；
- 演示动作自清理：确认后的订单先 `action_cancel` 再删除（Odoo 不允许直接删已确认订单），业务库保持干净。

## 采购 Agent（领域 Agent 第二弹）

`agent/purchase_agent.py`：盯着草稿采购单队列 → 规则预检（purchase tier 规则）
→ 把"确认采购单"提交权限门 → 人拍板后才执行。端到端链条中，采购 Agent 承接
销售确认后的采购需求，向供应商下单。

```bash
venv/bin/python purchase_demo.py --scan       # 只读扫描草稿采购单
venv/bin/python purchase_demo.py              # 创建 demo 草稿，提交确认请求后停在 pending
venv/bin/python purchase_demo.py --approve    # 完整闭环：创建→审批→确认→验证→取消→清理
```

`--approve` 完整闭环输出（节选）：

```
[purchase-agent] P00002 -> request #2 decision=approved
[verify] purchase order state after confirmation = purchase
[demo] cancelled 1 confirmed demo purchase order(s)
[demo] cleaned 1 demo purchase order(s)
```

**架构**：销售/采购共用 `agent/domain_agent.py` 通用基类——每个领域 Agent
只定义模型、草稿状态、确认方法和提交理由，权限门流程只写一次。

## Odoo 19 适配要点（OCA tier validation）

本项目在 Odoo 19 上安装 OCA `base_tier_validation` / `sale_tier_validation` /
`purchase_tier_validation`（18.0 分支）时踩过的坑，均已修复：

| Odoo 18 | Odoo 19 | 位置 |
|---|---|---|
| `models.NewId`（已移除） | 判断改为 `not isinstance(rec.id, int)` | `tier_validation.py` `_compute_need_validation` |
| `res.users.groups_id` | `res.users.group_ids` | `tier_validation.py` 异常搜索 domain |
| `res.groups.users` | `res.groups.all_user_ids` | `tier_review.py` reviewer 字段 |
| manifest `version` 前缀 | 必须 `19.0.*` 否则 uninstallable | 三个模块 manifest |
| search 视图 `<group expand=... string=...>` | `<group>` 不允许任何属性 | `tier_definition_view.xml` |

## 下一步（路线图）

- [x] 动作权限门：默认只读，关键写操作需人工确认（`agent/gate.py`）
- [x] 全程留痕：审计日志 `agent/audit.py`
- [x] 规则引擎预检：对接 OCA `base_tier_validation` 的审批层级（`agent/tier_precheck.py`）
- [x] 领域 Agent 第一弹：销售 Agent（草稿报价单 → 确认，全程经权限门，`agent/sales_agent.py`）
- [x] 领域 Agent 第二弹：采购 Agent（草稿采购单 → 确认，`agent/purchase_agent.py`，共用 `domain_agent.py` 基类）
- [ ] 领域 Agent 舰队：生产 / 财务 / 物流
- [ ] 端到端履约链路：订单 → 采购 → 到货检验 → 付款 → 生产 → 出货 → 物流 → 签收
- [ ] docker-compose 交付：odoo + postgres + ai-agent 三服务

## 开源协议

[MIT License](LICENSE)。项目源代码全部开源、免费使用；商业服务（需要人工
出面的实施/运维）另行收费。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)（贡献指南、代码规范、提交规范、PR 流程）。
Bug / 功能请求请用仓库内置的 Issue 模板。
