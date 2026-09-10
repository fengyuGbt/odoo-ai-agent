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
│   ├── purchase_agent.py # 采购 Agent：草稿采购单 → 确认
│   ├── demand_agent.py  # 需求 Agent：库存分析 → 采购提案（经权限门）
│   ├── receiving_agent.py # 收货 Agent：到货检验（单据核对）→ 入库请求（经权限门）
│   ├── payment_agent.py # 付款 Agent：供应商账单（创建/过账）→ 付款单（创建/过账）（均经权限门）
│   ├── production_agent.py # 生产 Agent：生产计划核对（BOM 组件）→ 确认 MO → 完成（经权限门）
│   ├── delivery_agent.py # 出货 Agent：出库核对（与销售单）→ 发货（经权限门）
│   └── logistics_agent.py # 物流 Agent：承运分配 → 运单登记 → 签收登记（经权限门）
├── main.py              # 最小闭环演示：连接 + 只读查询
├── demo_approval.py     # 权限门演示：AI 提交 → 预检 → 人审批 → 执行/拒绝
├── sales_demo.py        # 销售 Agent 演示：录入 → 预检 → 审批 → 确认 → 清理
├── purchase_demo.py     # 采购 Agent 演示：录入 → 预检 → 审批 → 确认 → 清理
├── receiving_demo.py    # 收货 Agent 演示：采购 → 确认 → 到货检验 → 入库 → 清理
├── payment_demo.py      # 付款 Agent 演示：采购 → 收货 → 账单 → 付款（全链审批）→ 清理
├── production_demo.py   # 生产 Agent 演示：BOM → 生产订单 → 确认 → 完成 → 清理
├── delivery_demo.py     # 出货 Agent 演示：销售 → 确认 → 出库核对 → 发货 → 清理
├── logistics_demo.py    # 物流 Agent 演示：承运/运单/签收 → 清理
├── e2e_demo.py          # 端到端链路演示：销售→审批→确认→需求分析→采购→确认→清理
├── setup_tier_rule.py   # 创建演示 tier 审批规则（sale + purchase）
├── docker-compose.yml   # 一键启动：postgres + odoo + ai-agent 三服务
├── Dockerfile           # ai-agent 容器镜像
├── docker/
│   └── wait_then_run.py  # 容器入口：等 Odoo 就绪再执行命令
├── addons/              # 挂载到 Odoo 的额外模块（可选放 OCA tier 模块）
└── scripts/
    ├── cleanup_demo_pickings.sh  # 演示库维护：清理由 demo 产生的已完成收货
    ├── cleanup_demo_finance.sh   # 演示库维护：清理由 demo 产生的账单与付款
    └── cleanup_demo_production.sh # 演示库维护：清理由 demo 产生的生产订单与 BOM
├── requirements.txt     # Python 依赖
├── .env.example         # 配置模板（复制为 .env 后填写）
└── README.md
```

## Docker Compose 一键启动（推荐体验）

不想本地装 Odoo/PG？一条命令拉起 **postgres + odoo + ai-agent** 三服务：

```bash
docker compose up --build         # 首次会自动初始化库（约 3-5 分钟）
```

启动后：
- **Odoo Web**：http://localhost:8070 （admin / admin，8070 避开本机 8069）
- **ai-agent**：启动时自动等待 Odoo 就绪，随后跑一次连通性自检
  （日志可见 `[OK] minimal closed loop works`）

在容器里跑任意 Agent / demo：

```bash
# 权限门演示（AI 提交 → 人审批 → 执行）
docker compose run ai-agent python demo_approval.py --approve

# 进入容器交互式排查
docker compose run ai-agent bash
```

清理：

```bash
docker compose down               # 停止（保留数据卷）
docker compose down -v            # 停止并清空数据卷（完全重置）
```

**说明**：
- Odoo 容器首次启动自动初始化 `odoo19` 库并装好各 Agent 依赖的模块
  （销售/采购/库存/生产/配送/会计）；
- **权限门不依赖 OCA 模块**：没有 tier 模块时预检自动跳过（输出
  "no tier rules configured"），AI 仍被权限门强制拦在每个写动作前；
- 需要规则预检时，按 `addons/README.md` 放入 OCA tier 模块。

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

## 端到端链路（e2e_demo.py）

第一个端到端切片，串起销售 → 需求分析 → 采购，完全对应创始人的设想：
**订单下来 → 审批 → 分解出采购订单 → 给供应商下单**。每个动作都过权限门。

```
step 1 销售录入：创建草稿报价单（带产品行）          → 人审批
step 2 销售 Agent：确认报价单（action_confirm）      → 人审批 → state=sale
step 3 需求 Agent：读库存（缺口 10）→ 创建采购草稿   → 人审批
step 4 采购 Agent：确认采购单（button_confirm）      → 人审批 → state=purchase
step 5 清理：picking → 采购单 → 销售单（取消+删除）  → 全部经人审批
```

```bash
venv/bin/python e2e_demo.py --scan       # 只读：产品库存、草稿单据
venv/bin/python e2e_demo.py --approve    # 完整链路（每步都有人拍板）
```

`--approve` 节选：

```
== step 2: sales agent confirms the quotation (gated) ==
  S00026 -> request #2 decision=approved
  [verify] sale state = sale
== step 3: demand agent checks stock, proposes purchase (gated) ==
  analysis: needed=10 available=0 shortfall=10
  purchase_requested -> gate=approved request=#3
== step 4: purchase agent confirms the purchase order (gated) ==
  P00006 -> request #4 decision=approved
  [verify] purchase state = purchase
== step 5: cleanup (gated) ==
  cleaned 4 demo picking(s) / 2 demo purchase order(s) / 2 demo sale order(s)
```

**需求 Agent**（`agent/demand_agent.py`）读 `qty_available` 算缺口；库存足够时
不提出采购（`no_purchase_needed`）——AI 做判断，判断结果和理由一起呈现给人工。

## 到货检验（收货 Agent，receiving_demo.py）

采购确认后 Odoo 自动生成收货单（inbound picking）。收货 Agent 是链路第三步：

```
step 1 创建演示采购单（可乐 × 10）              → 人审批
step 2 采购 Agent 确认                          → 人审批 → state=purchase
step 3 收货 Agent 检验：picking 与采购单核对
       （产品、数量、状态、origin、move 明细）   → 输出检验结论
step 4 收货 Agent 提交入库请求（button_validate）→ 人审批 → picking state=done
step 5 清理：未完成 picking + 采购单             → 全部经人审批
```

```bash
venv/bin/python receiving_demo.py --scan      # 只读：列出待收货的 inbound picking
venv/bin/python receiving_demo.py --approve   # 完整链路（每步都有人拍板）
```

**检验结论示例**：

```
picking WH/IN/00006 (state=assigned, origin=P00009)
purchase order P00009 (state=purchase, partner=Administrator, total=11.5)
  expected line: 可乐 × 10.0 @ 1.0
  move: 可乐 qty=10.0 done=10.0 state=assigned
inspection result: document cross-check passed — awaiting human confirmation to receive
```

**为什么已完成的收货不能删**：Odoo 的库存语义是——`done` 的调拨只能创建退货
（return）撤销，不能直接删除。`receiving_demo.py --approve` 真实执行收货后会
保留完成的收货单（真实业务留痕），演示库如需清理由维护脚本处理：

```bash
bash scripts/cleanup_demo_pickings.sh   # 仅演示库：清掉 demo 产生的 done picking
```

## 付款（Payment Agent，payment_demo.py）

到货完成后通知财务付款。付款 Agent 是链路第四步：

```
step 1 创建演示采购单（可乐 × 10）              → 人审批
step 2 采购 Agent 确认                          → 人审批
step 3 收货 Agent 入库（到货完成 = 付款触发点）   → 人审批
step 4 创建供应商账单（action_create_invoice）   → 人审批
step 5 设置账单日期 + 账单过账（action_post）    → 人审批 → bill posted
step 6 创建付款单（account.payment，outbound）   → 人审批
step 7 付款单过账（action_post）                 → 人审批 → payment in_process
step 8 清理：付款单 → 账单 → 采购单              → 全部经人审批
```

```bash
venv/bin/python payment_demo.py --scan      # 只读：列出已确认且已收货的采购单
venv/bin/python payment_demo.py --approve   # 完整链路（每步都有人拍板）
```

**Odoo 19 财务字段差异（适配记录）**：
- `account.payment` 引用字段是 `payment_reference`（不是 `ref`）；
- 账单过账前必须有 `invoice_date`（"需要帐单/退款日期才能验证此文档"）；
- 付款单过账后的状态是 `in_process`（不是 `posted`）；
- 已过账账单可以 `button_draft` 回草稿再删除（演示清理路径）。

## 生产（Production Agent，production_demo.py）

到货付款后组织生产。生产 Agent 是链路第五步：

```
step 1 创建演示 BOM（汉堡 = 可乐 × 2）+ 生产订单（汉堡 × 5）→ 人审批
step 2 生产 Agent 核对生产计划（产品/数量/BOM 组件）       → 人审批
        → 确认 MO（action_confirm）→ state=done
step 3 清理：已完成 MO 保留（真实生产留痕），BOM 经审批删除
```

```bash
venv/bin/python production_demo.py --scan      # 只读：列出待生产的 MO
venv/bin/python production_demo.py --approve   # 完整链路（每步都有人拍板）
```

**Odoo 社区版行为（适配记录）**：未安装工单模块（`mrp_workorder`）时，简单
生产订单（无工单/无在制步骤）确认后**立即完成**（`action_confirm` 直接置为
`done`）。demo 会如实报告这一行为并跳过重复的 mark-done 步骤——AI 不做无
意义动作。已完成 MO 关联真实库存移动，演示库用维护脚本清理：

```bash
bash scripts/cleanup_demo_production.sh   # 仅演示库：清 demo 产生的 MO 与 BOM
```

## 出货（Delivery Agent，delivery_demo.py）

生产完成后成品出库、交付客户。出货 Agent 是链路第六步：

```
step 1 创建演示销售单（可乐 × 5）              → 人审批
step 2 销售 Agent 确认                          → 人审批 → state=sale（生成出库单）
step 3 出货 Agent 核对出库单与销售单
       （产品/数量/状态/move 明细）              → 输出核对结论
step 4 出货 Agent 提交发货（button_validate）    → 人审批 → picking state=done
step 5 清理：销售单 + 未完成出库单              → 全部经人审批
```

```bash
venv/bin/python delivery_demo.py --scan      # 只读：列出待发货的出库单
venv/bin/python delivery_demo.py --approve   # 完整链路（每步都有人拍板）
```

**核对结论示例**：

```
picking WH/OUT/00011 (state=assigned, origin=S00027)
sale order S00027 (state=sale, partner=Administrator, total=5.75)
  expected line: 可乐 × 5.0
  move: 可乐 qty=5.0 done=5.0 state=assigned
shipping check passed — awaiting human confirmation to ship
```

已完成的出库与入库一样受 Odoo 库存语义保护（只能退货撤销），演示库用
`scripts/cleanup_demo_pickings.sh` 清理。

## 物流（Logistics Agent，logistics_demo.py）

出货后进入物流环节：分配承运商、登记运单号、客户签收。物流 Agent 是
链路第七步，需要 **delivery 模块**（官方社区版，`odoo-bin -i delivery`）：

```
step 1 创建配送产品 + 承运商（AI Demo Carrier）   → 人审批
step 2 创建销售单（带配送方式）                    → 人审批
step 3 销售 Agent 确认                             → 人审批 → 生成出库单
step 4 出货 Agent 发货（button_validate）          → 人审批 → done
step 5 物流 Agent 核对（carrier/运单号/客户）：
       → 登记运单号（carrier_tracking_ref）        → 人审批
       → 签收登记（chatter 消息，AI 只记录事件）    → 人审批
step 6 清理：承运商/配送产品/销售单                → 全部经人审批
```

```bash
venv/bin/python logistics_demo.py --scan      # 只读：待物流处理的已发运单
venv/bin/python logistics_demo.py --approve   # 完整链路
```

**Odoo 19 字段**：出库单用 `carrier_id`（配送方式）与
`carrier_tracking_ref`（运单号）；销售单确认后配送方式随出库单生成。
"客户签收"在社区版没有原生动作，AI 用标准 chatter 消息（`message_post`）
登记事件——AI 记录事实，人拍板。

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
- [x] 端到端链路雏形：销售 → 审批 → 确认 → 需求分析 → 采购 → 确认 → 清理（`e2e_demo.py`）
- [x] 到货检验：收货 Agent（单据核对 → 入库请求，`agent/receiving_agent.py` + `receiving_demo.py`）
- [x] 付款：付款 Agent（账单创建/过账 → 付款单创建/过账，`agent/payment_agent.py` + `payment_demo.py`）
- [x] 生产：生产 Agent（BOM 计划核对 → 确认 MO → 完成，`agent/production_agent.py` + `production_demo.py`）
- [x] 出货：出货 Agent（出库核对 → 发货，`agent/delivery_agent.py` + `delivery_demo.py`）
- [x] 物流：物流 Agent（承运分配 → 运单登记 → 签收登记，`agent/logistics_agent.py` + `logistics_demo.py`，依赖 delivery 模块）
- [ ] 领域 Agent 舰队：财务（应收）
- [ ] 端到端履约链路扩展：签收后 → 售后/退货
- [ ] 精益经营监控：交货 / 库存 / 浪费
- [x] docker-compose 交付：postgres + odoo + ai-agent 三服务（`docker compose up --build`，Web 在 8070）

## 开源协议

[MIT License](LICENSE)。项目源代码全部开源、免费使用；商业服务（需要人工
出面的实施/运维）另行收费。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)（贡献指南、代码规范、提交规范、PR 流程）。
Bug / 功能请求请用仓库内置的 Issue 模板。
