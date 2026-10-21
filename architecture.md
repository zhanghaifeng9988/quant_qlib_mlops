# quant-qlib-mlops 架构设计定稿（v1.0）
本文档为项目架构基线，所有已锁定决策以此为唯一真相源。后续对话、里程碑执行、README 撰写均引用本文档。变更需显式记录版本号与原因。

## 0. 项目定位
围绕 Qlib 构建面向美股量化选股的企业级 MLOps 基础设施。核心原则：Qlib 负责量化正确性，MLOps 层负责工程可重复性与可观测性。不重复造轮子，不侵入 Qlib 内部逻辑。

## 1. 锁定决策（基线）
编号	决策	内容
- D1	Universe 双轨	target_universe = 2000 只（三大指数去重，终态设计目标）；
D1	Universe 双轨	target_universe = 518 只（三大指数去重，终态设计目标）；
- 修正："2000 只"是架构阶段基于"美股全市场选股"的假设，但决策锁定的是"三大指数成分股"，两者去重后的真实规模是 518 只。这不是缩减，是假设修正。
dev_universe = 500 只（按 20 日平均成交额取 Top 500，M1–M5 实际开发与验收基准）
- D2	行业分类	开源静态 GICS sector 映射表（Wikipedia / 开源数据集），11 sector 经 industry_map.yaml 聚合为 10 个方向；不接实时 API；映射版本记入 manifest
- D3	模型失效口径	主判据 = 滚动 IC/RankIC 相对训练基线的衰减 + 特征 PSI；回测指标（滚动 Sharpe、最大回撤）仅作 Grafana 辅助展示，不触发告警
- D4	K8s 定位	概念规划级。M4 仅做单节点 kind + 最小 manifest（FastAPI / MLflow / PostgreSQL / Grafana 各一），不做资源配额、HPA、Ingress 精细化
- D5	推理契约	/predict 按日期返回整个 universe 的排序分数（批量打分模式）；按股票列表查询为二期能力
- D6	CI/CD 边界	lint + 单元测试 + 镜像构建 + kind 部署 + 冒烟测试；模型训练手动/定时触发，不进入 push 流水线
- D7	监控栈	不上 Prometheus（二期再议）。PostgreSQL 存指标 + Grafana 直连展示。服务健康检查用 K8s liveness/readiness 探针
- D8	数据更新	手动从 yfinance 下载，不做自动调度采集
- D9	硬资源约束	WSL2 可用内存 7.6 GB（Swap 2 GB 仅兜底）。全流程按此约束设计内存安全规范；M1–M3 不启动 Docker；M4 部署验证前停止训练相关进程

## 2. 系统总体架构

┌─────────────────────────────────────────────────────────────────────┐
│                    监控与部署层 (Observability & Deployment)          │
│  Grafana Dashboards  │  PostgreSQL (metrics)  │  K8s (kind 单节点)   │
│  GitHub Actions CI/CD                                                 │
└─────────────────────────────────────────────────────────────────────┘
                              ▲ 指标写入 / 部署产物
┌─────────────────────────────────────────────────────────────────────┐
│                     MLOps 平台层 (Orchestration & Registry)           │
│  MLflow Tracking Server  │  MLflow Model Registry  │  FastAPI Serving │
│  Drift / Eval Batch Jobs  │  Model Promotion Gates                    │
└─────────────────────────────────────────────────────────────────────┘
                              ▲ Qlib 训练产物 / 评估指标
┌─────────────────────────────────────────────────────────────────────┐
│                     Qlib 核心层 (Quant Research Engine)               │
│  qrun (YAML workflow)  │  Model.fit/predict  │  SignalRecord / PortAna │
│  Alpha158  │  DatasetH  │  Recorder (MLflow backend)  │  TopkDropout   │
└─────────────────────────────────────────────────────────────────────┘
                              ▲ Qlib .bin 格式数据
┌─────────────────────────────────────────────────────────────────────┐
│                          数据层 (Data Layer)                          │
│  指数成分采集 → 去重截断 → yfinance 归一化 → dump_bin → Qlib .bin     │
│  PostgreSQL (行业映射/元数据)  │  本地卷 (Qlib 数据目录)               │
│  manifest.json (血缘契约：universe 快照 + 行业版本 + bin 哈希)         │
└─────────────────────────────────────────────────────────────────────┘

三个跨层契约：

.bin 数据目录 — Qlib 与数据层的唯一数据接口，Qlib 以 provider_uri 指向。

manifest.json — 数据版本与 MLflow run 的血缘锚点，Qlib 不感知，训练 run 的 params 记录其 ID。

MLflow Tracking URI + Model Registry — Qlib Recorder 与 MLOps 层的接口，Registry 晋升由 MLOps 层控制。

## 3. 职责边界
###3.1 Qlib 原生解决（不重写）
环节	Qlib 能力
训练执行编排	qrun + YAML workflow / workflow_by_code.py
实验追踪后端	MLflowExpManager + MLflowRecorder，自动写 MLflow run
信号分析与回测	SignalRecord / SigAnaRecord / PortAnaRecord
模型训练循环	Model.fit/predict 基类，内置 LightGBM / MLP / LSTM 等
策略层选股	TopkDropoutStrategy，从打分自动选 TopK 调仓
特征工程	Alpha158 / Alpha360 标准因子集
###3.2 MLOps 层封装（Qlib 不提供或不够企业级）
环节	封装方式	与 Qlib 衔接点
数据采集与 .bin 转换	自定义采集/dump 编排脚本 + 容器 Job	输出 data/qlib_bin/us_data
Universe 与行业映射管理	PostgreSQL / YAML 存储快照与映射，生成 instruments/*.txt	纯文本 instruments 文件
数据血缘	manifest.json（universe 快照 ID、行业版本、字段、哈希）	Qlib 不读，训练 run params 记录
模型注册与晋升	MLflow Model Registry：Staging → Production 门禁	从 Qlib MLflow run 提取 artifact 注册
批量评估与漂移	独立 Python Job：PSI/KS 计算，写 PostgreSQL	Qlib 产出预测信号，漂移在外部计算
服务化推理	FastAPI 加载 Registry Production 模型	封装 Qlib DatasetH 构造 + model.predict
CI/CD	GitHub Actions：lint → test → 镜像 → kind 部署 → 冒烟	不重写 Qlib workflow，调用封装入口
监控可视化	Grafana 直连 PostgreSQL	指标由独立 Job 写入

## 4. 业务与数据流程
###4.1 两层“选股”心智模型
text
【第一层：Universe 选股】—— 数据准备阶段，项目负责
  三大指数成分股 → 去重 → 按 20 日平均成交额取 Top 500（dev）
  → 写成 instruments/dev_500.txt → 产出“股票池”
  → 决定后续所有特征/训练/回测的数据范围

【第二层：策略选股】—— 模型打分后，Qlib 自动完成
  模型对 universe 内每只股票每日打分 → TopkDropoutStrategy 选 Top 30-50
  → 调仓回测 → PortAnaRecord 生成报告
###4.2 端到端数据流
text
选股（universe 快照）
  → 数据清洗（yfinance 采集，归一化 OHLCV + factor）
  → 特征工程（Alpha158 自动计算 158 因子）
  → 模型训练（LightGBM / Qlib Model）
  → 模型注册（MLflow Registry，门禁晋升）
  → 服务化推理（FastAPI 批量打分）
  → 数据漂移监控（PSI 写 PostgreSQL → Grafana）
  → 模型失效监控（IC/RankIC 衰减写 PostgreSQL → Grafana）

## 5. 里程碑拆解
M1：数据接入与 Qlib 数据底座
目标：产出 Qlib 可正常 init 并读取的 dev_universe（500 只）数据目录 + manifest + 行业映射。

步骤	内容	验收
0	venv（Python 3.10）+ 版本锁定（pyqlib / pandas / numpy / yfinance）	import qlib 成功；pip freeze > requirements.lock
1	Universe 快照：target_universe 2000 只 + dev_universe 500 只（按成交额 Top 500）	universes.yaml 双轨记录；快照日期、来源、缩减规则明确
2	行业映射：11 GICS sector → 10 方向，industry_map.yaml	覆盖率 ≥95%；11 sector 全部有去向；10 方向均出现
3	yfinance 采集：分批 50 只，落盘归一化七列（date, symbol, O/H/L/C/V, factor）	失败 symbol < 5%；采集日志按批追加
4	dump_bin.py 单进程生成 .bin 目录	features/ / calendars/ / instruments/ 完整；日历覆盖 ≥15 年
5	写 manifest.json（快照 ID、行业版本、时间戳、字段、数量、bin 哈希）	JSON 可解析；数量与 instruments 一致；哈希非空
6	Qlib 验收：qlib.init + D.features 单股票单年	DataFrame 非空；$close/$volume/$factor 合理；全程 available ≥3 GB
M1 内存安全规范：不用 conda base；Jupyter 非必需；采集与 Qlib 验收分进程；每批落盘即释放；dump 单进程无并行；qlib.init 启用磁盘缓存（DiskExpressionCache / DiskDatasetCache），mem_cache_size_limit 显式限制，drop_raw=True；每次操作前后 free -h 检查。

M2：Qlib 训练闭环 + MLflow 单机追踪
目标：用 qrun 跑通 LightGBM + Alpha158 + 回测全链路，所有实验进 MLflow。

配置 MLflowExpManager，统一 tracking URI 与 provider_uri。

至少一个 YAML workflow：train/valid/test 切分 → 训练 → SignalRecord → PortAnaRecord。

DataHandler 强制磁盘缓存；单次 qrun 单模型，不并行。

验收：连续 3 次相同配置产生 3 个可复现 MLflow run；params / IC / 回测指标 / 模型 artifact 均可见；R.get_recorder() 可定位 artifact。

M3：模型注册与 FastAPI 服务化
目标：Qlib 训练产物接入 MLflow Model Registry，FastAPI 提供批量打分服务。

从 Qlib MLflow run 提取模型 artifact，以 pyfunc 或项目内约定 flavor 注册。

FastAPI /predict：入参 date，返回 universe 全量排序分数；轻量适配层构造 DatasetH 调 model.predict。

门禁规则：首版手动 Staging → Production，或基于回测 IC 阈值自动晋升。

验收：Registry 至少 1 个 Production 版本；FastAPI 输出与 Qlib 本地 model.predict 一致；服务启动不构造全量 DatasetH，按请求构造即用即释放。

M4：容器化与 K8s 部署（概念规划级）
目标：FastAPI / MLflow / PostgreSQL / Grafana 部署到单节点 kind。

镜像：Qlib 运行时（.bin 数据通过 PVC 挂载，不打进镜像）、FastAPI 服务。

Manifests：Deployment / Service / ConfigMap / PVC；MLflow + PG 用 StatefulSet 或本地持久卷。

GitHub Actions：lint + test → 镜像构建 → kind load → kubectl apply → /health + /predict 冒烟测试。

验收：kind 内服务可达；MLflow UI 可访问；重启 Pod 数据不丢。执行前停止所有 Qlib 训练进程，kind 节点只跑最小集合。

M5：监控闭环——数据漂移与模型失效
目标：训练后监控可运行闭环，Grafana 回答“数据是否漂移、模型是否失效”。

漂移 Job：按特征分批计算当前窗口 vs 训练基线 PSI / KS，写 PostgreSQL drift_metrics。

失效 Job：滚动计算近 N 日 IC / RankIC，与训练基线对比，阈值（如 IC 降 30% 或 PSI > 0.3）触发告警事件写 PostgreSQL。

Grafana：漂移面板（PSI 热力图/时序 + 阈值线）、模型健康面板（IC 滚动曲线 + 回撤/Sharpe 辅助展示）。

可选演示：漂移告警 → workflow_dispatch 触发重训练，不做生产级自动回滚。

验收：人为注入偏移数据后下个 Job 周期 PSI 上升可见；IC 面板展示 ≥30 个交易日滚动值；告警仅由 IC 衰减 + PSI 触发，回测指标不参与告警逻辑。

6. 核心难点与设计对策
#	难点	对策
1	.bin 格式不携带版本/来源/行业元数据	manifest.json 作为血缘契约；训练 run params 记录 manifest ID
2	Qlib Recorder 不自动调 mlflow.register_model，模型对象序列化不直接兼容 Registry	薄导出器：从 recorder 取模型，包装为 pyfunc 兼容类或按约定 module_path 重建；首版接受“Registry 存 artifact + 元数据，服务侧约定重建”，不在 Qlib 内打补丁
3	量化场景 PSI/IC 口径歧义（截面 vs 时序，基线来源）	M5 前先写“监控指标契约”文档：每指标的计算维度、窗口、基线来源（训练 run artifact 冻结统计量）、阈值
4	Qlib 与 pandas/numpy/yfinance 版本兼容	M1 步骤 0 先锁兼容矩阵：Python 3.10；只装 pyqlib+pandas+numpy 验证 import qlib，再装 yfinance 验证未被升级，最后 freeze
5	7.6 GB 内存约束	500 只 dev_universe；磁盘缓存 + 内存上限 + drop_raw；分进程执行；Swap 仅兜底；M1–M3 不启 Docker
7. 资源与边界说明
目标态 vs 开发态：README 描述“面向 2000 只美股三大指数”的终态设计；实际验收以 500 只 dev_universe 为准。取舍理由写入 README——资源约束下用子集验证工程链路，目标态通过磁盘缓存/分批处理支撑全量。这是真实工程决策。

明确不做：GPU 资源管理、Agent 开发、Prometheus（二期）、自动化数据调度采集（手动 yfinance）、K8s 生产级编排（HPA/Ingress/多节点）。

二期候选：Prometheus 服务指标、按股票列表查询的推理接口、自动重训练闭环、target_universe 全量性能摸底。

8. 技术栈清单
层	组件
语言/环境	Python 3.10，uv / venv，WSL2 Ubuntu
量化引擎	Qlib（pyqlib，锁定版本），Alpha158，LightGBM
实验追踪/注册	MLflow Tracking Server + Model Registry
数据存储	Qlib .bin（本地卷），PostgreSQL（元数据/指标/行业映射）
服务化	FastAPI
容器/编排	Docker，kind（单节点）
CI/CD	GitHub Actions
监控可视化	Grafana（直连 PostgreSQL）
数据源	yfinance（手动采集），开源 GICS 映射表
版本：v1.0（定稿）
锁定日期：2026-10-19 09:30 am
---

## 附录 A：M1 实际产出（2026-10-21 封存）

### A.1 版本矩阵（锁定）

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.11.16 | uv 管理的项目 venv |
| pyqlib | 0.9.7 | `uv add "pyqlib>=0.9.7,<0.10"` |
| pandas | 2.2.3 | 锁 `>=2.1,<2.3`，避开 3.0 破坏性变更 |
| numpy | 1.26.4 | 锁 `>=1.26,<2.0`，避开 numpy 2.x 与回测模块不兼容 |
| yfinance | 1.7.0 | 手动采集，不自动调度 |
| 构建工具 | uv | `uv venv` + `uv add` + `uv run` |

### A.2 Universe 快照

| 项 | 值 |
|---|---|
| snapshot_id | `us_2026-10-19_dev500` |
| target_universe | 518 只（S&P 500 ∪ NASDAQ 100 ∪ Dow Jones，去重） |
| dev_universe | 500 只（按 market_cap 取 Top 500） |
| 行业映射 | 100% 覆盖，10 方向；Materials=5、Communication Services=9 样本偏薄，M5 分组监控需注意 |

> **定稿修正**：架构 v1.0 D1 原写 target=2000 只，实测三大指数去重后为 518 只。修正为 target=518、dev=500。2000 只对应“美股全市场按流动性筛选”，属二期范围。

### A.3 数据产出

| 项 | 值 |
|---|---|
| 原始数据 | `data/raw/us_daily/` 500 CSV，1,772,300 行，成功率 100% |
| Qlib `.bin` | `data/qlib_bin/us_data/`，47 MB，压缩比约 40:1 |
| 日历 | 2011-10-24 → 2026-10-16，3767 交易日 |
| 字段 | open, high, low, close, volume, factor |
| 质量检查 | close 无 NaN、factor>0、volume≥0，issues=0 |
| 峰值内存 | 0.22 GB（远低于 3 GB 安全线） |

### A.4 数据契约（入库）

| 文件 | 作用 |
|---|---|
| `data/universe/universes.yaml` | target/dev 双轨快照 + 缩减规则 + 源 URL |
| `data/universe/industry_mapping.csv` | symbol → source_sector → 10 方向 |
| `data/qlib_bin/us_data/manifest.json` | 血缘锚点：snapshot_id、日历、字段、三个目录 sha256 |

### A.5 脚本清单

| 脚本 | 职责 |
|---|---|
| `scripts/m1_fetch_sources.sh` | 下载三个指数源 CSV 到 `data/universe/raw/` |
| `scripts/m1_build_universe.py` | 生成 `universes.yaml` + `industry_mapping.csv` |
| `scripts/m1_collect_raw.py` | 分批 50 只 yfinance 采集，落盘即释放 |
| `scripts/qlib_tools/dump_bin.py` | 从 Qlib GitHub 下载的 dump 工具（pip 包不含） |
| `scripts/m1_write_manifest.py` | 生成 `manifest.json` |
| `scripts/m1_acceptance.py` | 日历/instruments/样本/内存四项验收 |

### A.6 M1 复现顺序

```bash
bash scripts/m1_fetch_sources.sh
uv run python scripts/m1_build_universe.py
uv run python scripts/m1_collect_raw.py
uv run python scripts/qlib_tools/dump_bin.py dump_all \
  --data_path data/raw/us_daily --qlib_dir data/qlib_bin/us_data \
  --include_fields open,high,low,close,volume,factor --max_workers 2 --freq day
uv run python scripts/m1_write_manifest.py
uv run python scripts/m1_acceptance.py
