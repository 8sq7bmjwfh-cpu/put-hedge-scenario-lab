# Put Hedge Scenario Lab

用于分析“指数多头 + 买入 Put”的对冲效果。当前版本为**纯情景矩阵分析**，不做最优化选点。

## 版本说明

- 已移除最优化目标与加权打分逻辑（后端和前端都已移除）。
- 执行价按交易所挂牌挡位生成（ATM±N 档）。
- 情景结果以矩阵展示：
  - 横轴：到期标的价格 `S_T`
  - 纵轴：执行价 `K`
  - 单元格：对冲后总收益（元）
  - 颜色：当前情境下的对冲后总收益水平

同时仍输出改善程度数据，定义为：

`改善程度 = (对冲后总收益 - 未对冲总收益) / (头寸金额 * Beta)`

---

## 项目结构

- `put_hedge_optimizer.py`：后端矩阵计算脚本（Python）。
- `web/index.html`：前端页面。
- `web/app.js`：前端计算与矩阵渲染逻辑。
- `web/styles.css`：页面样式。

---

## 本地运行

### 1) 运行前端页面

直接打开：

- `web/index.html`

页面会按输入参数实时计算并展示矩阵与颜色。

### 2) 运行后端脚本

```bash
python put_hedge_optimizer.py
```

默认输出到 `outputs/`：

- `strike_summary.csv`：执行价与成本摘要
- `hedged_pnl_matrix.csv`：对冲后总收益矩阵（元）
- `improvement_ratio_matrix.csv`：改善程度矩阵（比例）
- `hedged_pnl_color_matrix.csv`：对冲后总收益颜色矩阵（hex）
- `matrix_cells_long.csv`：长表（每个格点的完整明细）

---

## 常用参数（后端）

```bash
python put_hedge_optimizer.py \
  --spot-index 8560.84 \
  --portfolio-value 10000000 \
  --portfolio-beta 1.0 \
  --hedge-ratio 1.0 \
  --hedge-method notional \
  --listing-depth 12 \
  --terminal-start 6800 \
  --terminal-stop 9600 \
  --terminal-step 100 \
  --pricing-mode auto \
  --bs-premium-rate 0.10
```

### 到期与挂牌参数

- `--valuation-date YYYY-MM-DD`：估值日（默认今天）
- `--expiry-month YYYY-MM`：到期月（不填则取常见挂牌月中的第一个）
- `--listing-depth N`：ATM±N 档

### 定价参数

- `--pricing-mode auto|manual_flat|manual_curve`
- `--bs-premium-rate`：仅在 `auto` 模式生效，最终价格按 `BS理论价 * (1+溢价率)` 调整
- `--manual-premium`：统一手动价格（点/张）
- `--manual-premium-csv`：手动曲线文件（`strike,premium`）

### 成本参数

- `--fee-per-contract`
- `--slippage-per-contract`

---

## GitHub Pages 部署（已配置工作流）

仓库已提供工作流：

- `.github/workflows/deploy-pages.yml`

工作流行为：

- 当 `main` 或 `master` 分支推送 `web/**`、工作流文件或 `README.md` 变更时，自动部署到 GitHub Pages。
- 部署目录为 `web/`（静态页面）。

线上访问地址：

- <https://8sq7bmjwfh-cpu.github.io/put-hedge-scenario-lab/>

---

## 单位约定

- Put 价格：`点/张`
- 合约乘数：默认 `100`
- 成本与收益：`元`
