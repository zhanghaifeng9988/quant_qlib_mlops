Quant Qlib MLOps 架构设计（v0.1.1）
本文档记录项目的总体架构、关键技术决策、工程实现及阶段性演进过程。

项目当前状态以本文档中的“当前实现状态”为准；后续附录保留各阶段的历史设计和实施记录。若历史记录与当前实现不一致，以当前实现状态为准。
0. 项目定位
本项目是基于 Microsoft Qlib 构建的端到端 MLOps 工程实践项目，以美股日频数据和 LightGBM 模型为例，验证机器学习模型从数据处理、特征工程、模型训练、实验追踪、模型注册、推理服务到模型监控的完整工程链路。

项目重点是 MLOps 工程能力，而不是开发交易策略或构建真实交易系统。

当前主要运行环境为 Ubuntu on WSL2，采用本地批处理、Docker Compose 服务和 GitHub Actions 镜像发布相结合的方式。
1. 当前实现状态与架构决策
1.1 当前项目状态
| 模块 | 当前实现 |

|---|---|

| 数据来源 | yfinance 美股日频数据 |

| 股票池 | 三大指数去重目标股票池 518 只，开发股票池 500 只 |

| 数据处理 | 增量更新、数据质量检查、Qlib .bin 更新 |

| 数据血缘 | manifest.json、Lineage run records、数据质量报告 |

| 特征工程 | Qlib Alpha158 离线计算，Parquet 存储 |

| 模型训练 | Qlib + LightGBM，Walk-forward 多窗口训练 |

| 实验追踪 | MLflow Tracking |

| 模型注册 | MLflow Model Registry，Alias 管理 |

| 推理服务 | FastAPI，读取离线 Parquet 特征进行批量预测 |

| 监控 | IC、Rank IC、PSI 等指标 |

| 指标存储 | PostgreSQL |

| 可视化与告警 | Grafana + Webhook |

| 容器化 | Docker 多阶段构建、Docker Compose |

| CI | GitHub Actions，依赖锁定检查、Ruff、Python 编译检查 |

| 镜像发布 | GitHub Actions 构建并发布至 GHCR |
1.2 核心架构决策
- 数据与特征解耦：数据更新、Qlib 数据处理和 Alpha158 特征计算在离线批处理中完成，不在 FastAPI 请求过程中实时计算全量特征。
- 训练与推理解耦：模型训练由独立脚本执行，训练产物通过 MLflow 管理，FastAPI 加载注册模型提供推理服务。
- 特征与推理解耦：离线生成 Parquet 特征文件，API 按预测日期提取截面数据，避免在线加载完整历史数据。
- 数据可追溯：通过 manifest.json、Lineage 记录和质量报告保存数据版本、运行记录及质量检查结果。
- 监控独立运行：预测结果、实际收益反馈、IC/Rank IC 和 PSI 由独立批处理脚本计算并写入 PostgreSQL。
- 容器化边界：Docker 镜像主要用于 FastAPI 推理服务；数据、模型及其他持久化文件通过外部目录或卷管理。
- CI/CD 边界：GitHub Actions 负责代码质量检查和 Docker 镜像发布，不负责模型训练或云端自动部署。
1.3 当前不实施的范围
- Kubernetes / kind 集群部署。
- AWS EC2 自动部署及云端日更流水线。
- 真实交易账户接入和自动交易执行。
- 自动化模型晋升、自动回滚及无人值守重训练。
- Point-in-Time Universe 和完整的全市场历史成分股重建。
这些内容不属于当前 v0.1.1 已完成的工程能力。
2. 系统总体架构
┌──────────────────────────────────────────────────────────────────────┐
│                         监控与展示层                                  │
│       Grafana Dashboards  │  PostgreSQL  │  Webhook                   │
│       Grafana Labs Public Snapshot（项目成果展示）                    │
└──────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ 指标查询 / 告警
┌──────────────────────────────────────────────────────────────────────┐
│                       模型服务与监控层                                │
│       FastAPI Batch Serving  │  MLflow Model Registry                │
│       IC / Rank IC / PSI 独立批处理监控                               │
└──────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ 注册模型 / 预测结果 / 实际收益反馈
┌──────────────────────────────────────────────────────────────────────┐
│                       模型训练与实验层                                │
│       Qlib qrun  │  LightGBM  │  Walk-forward Validation              │
│       MLflow Tracking  │  MLflow Model Registry                       │
└──────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ Qlib 数据 / Parquet 特征
┌──────────────────────────────────────────────────────────────────────┐
│                         数据与特征层                                  │
│       yfinance  │  增量更新  │  数据质量检查  │  Qlib .bin             │
│       Alpha158 离线计算  │  Parquet 特征存储                           │
│       manifest.json  │  Lineage 记录  │  质量报告                     │
└──────────────────────────────────────────────────────────────────────┘

运行与交付：
- 本地 Ubuntu on WSL2：执行数据更新、特征生成、训练、推理及监控批处理。
- Docker Compose：管理本地 FastAPI、PostgreSQL、Grafana、Adminer 等服务。
- GitHub Actions：执行 CI 检查，并在符合触发条件时构建、发布 Docker 镜像。
- GHCR：存储已发布的 Docker 镜像。
- Grafana Labs Public Snapshot：用于展示监控看板，不依赖 AWS EC2。

三个跨层数据契约：

1. Qlib `.bin` 数据目录：连接原始行情处理、Qlib 数据读取和模型训练。
2. Parquet 特征文件：连接离线 Alpha158 特征生成与 FastAPI 批量推理。
3. MLflow Tracking URI + Model Registry：连接训练实验、模型产物、模型注册与推理服务。


## 3. 职责边界

### 3.1 Qlib 在当前项目中的职责

| 环节 | 使用方式 |
|---|---|
| 数据读取 | 读取本地 Qlib `.bin` 数据目录 |
| 特征工程 | 使用 Alpha158 进行离线特征计算 |
| 训练执行 | 使用 `qrun` 和 YAML workflow 执行训练任务 |
| 模型训练 | 使用 Qlib `LGBModel`（LightGBM） |
| 实验记录 | 通过 Qlib 的 MLflow 实验管理集成记录训练 run |
| 信号分析与回测 | 在历史 Walk-forward 实验中使用 Qlib 的信号分析和组合回测组件 |

Qlib 提供的策略模块、数据处理组件和其他模型能力并不意味着它们全部构成当前线上服务。项目的 FastAPI 推理服务不在请求过程中构造 Qlib `DatasetH`，也不在线计算 Alpha158。

### 3.2 项目自定义的 MLOps 工程层

| 环节 | 当前实现 | 与 Qlib / 其他组件的衔接 |
|---|---|---|
| 数据采集 | yfinance 增量采集脚本 | 输出原始日频 CSV |
| 数据质量 | 独立质量检查脚本 | 在后续 Qlib 数据更新和特征生成前检查原始数据 |
| Qlib 数据更新 | 项目脚本编排 Qlib `.bin` 更新 | 输出本地 Qlib 数据目录 |
| 数据血缘 | `manifest.json`、Lineage run records、质量报告 | 记录数据版本、运行信息和质量检查结果 |
| 特征生成 | Alpha158 离线计算并保存 Parquet | 供 FastAPI 按预测日期读取截面特征 |
| 模型注册 | MLflow Model Registry 与 Alias | 由训练产物注册模型；Alias 更新由人工控制 |
| 推理服务 | FastAPI 批量预测 | 加载注册模型并读取 Parquet 特征 |
| 预测记录 | 独立脚本写入 PostgreSQL | 保存预测结果，供后续实际收益反馈与监控 |
| 漂移与效果监控 | 独立脚本计算 PSI、IC、Rank IC | 将监控指标写入 PostgreSQL |
| 可视化与告警 | Grafana + Webhook | 查询数据库指标并按规则告警 |
| CI 与镜像发布 | GitHub Actions | 执行代码检查；按工作流触发条件构建并发布 GHCR 镜像 |

### 3.3 当前未实现的自动化能力

当前没有实现 Kubernetes 部署、云端自动部署、自动模型晋升、自动回滚，也没有实现由监控告警自动触发训练并自动部署的完整 Continuous Training 闭环。模型重训练、评估及是否更新 MLflow Alias，仍由人工决定。


## 4. 业务与数据流程

### 4.1 Universe 与模型实验范围

项目使用三大指数成分股去重形成目标股票池，当前目标股票池为 518 只，开发股票池为 500 只。该股票池用于限定历史数据采集、特征生成、训练和评估的数据范围。

当前 Universe 是基于当前成分股形成的静态快照，并非 Point-in-Time 历史成分股数据。因此，历史 Walk-forward 回测结果存在幸存者偏差风险，不应直接作为真实交易能力或未来收益能力的证明。

### 4.2 离线训练与历史评估流程

```text
Universe 快照
    → yfinance 历史行情采集
    → 原始数据质量检查
    → Qlib .bin 数据更新
    → Alpha158 离线特征生成
    → Parquet 特征文件
    → Qlib + LightGBM Walk-forward 训练与历史评估
    → MLflow Tracking 记录实验
    → MLflow Model Registry 注册模型
上述流程用于模型研发、历史实验和工程链路验证。历史回测结果不代表真实资金交易收益，也不能据此推断未来市场表现。
4.3 本地日更、推理与监控流程
yfinance 增量更新
    → 数据质量检查
    → Qlib .bin 更新及数据血缘记录
    → Alpha158 离线特征更新（Parquet）
    → FastAPI 重启及 /health 检查
    → FastAPI 批量推理
    → 预测结果写入 PostgreSQL
    → 成熟预测日期的实际收益反馈
    → IC / Rank IC 计算并写入 PostgreSQL
    → PSI 计算及必要的 Webhook 告警
    → Grafana 展示
该日更流程由本地 WSL2 环境执行。数据源限流、外部行情缺失或数据质量问题可能影响单次数据更新；流水线完成不等于外部数据源一定提供了完整、准确的最新行情。质量检查负责识别并报告异常，不应通过放宽核心数据规则来掩盖原始数据问题。
4.4 模型更新与人工决策
PSI 用于观察特征分布变化，IC / Rank IC 用于在预测日期具备实际收益反馈后评估预测表现。监控告警用于提示人工排查，不会自动启动重训练或自动替换线上模型。

需要更新模型时，由人工检查数据质量、监控结果和历史评估表现，再执行训练与评估；是否注册新版本或更新 MLflow Alias，也由人工决定。
5. 里程碑拆解
历史规划说明： 本节记录项目早期 M1–M5 阶段的原始规划和验收目标。后续实施过程中，部分设计已调整或取消，例如 Python 版本、在线特征计算方式、Kubernetes 部署及自动晋升方案。当前实际架构和完成状态以第 1–4 节及附录 F–J 的后续实施记录为准；本节内容不应被理解为当前全部已实现的功能。

M1：数据接入与 Qlib 数据底座

目标：产出 Qlib 可正常 init 并读取的 dev_universe（500 只）数据目录 + manifest + 行业映射。

步骤  内容  验收

0 venv（Python 3.10）+ 版本锁定（pyqlib / pandas / numpy / yfinance） import qlib 成功；pip freeze > requirements.lock

1 Universe 快照：target_universe 2000 只 + dev_universe 500 只（按成交额 Top 500）  universes.yaml 双轨记录；快照日期、来源、缩减规则明确

2 行业映射：11 GICS sector → 10 方向，industry_map.yaml 覆盖率 ≥95%；11 sector 全部有去向；10 方向均出现

3 yfinance 采集：分批 50 只，落盘归一化七列（date, symbol, O/H/L/C/V, factor）  失败 symbol < 5%；采集日志按批追加

4 dump_bin.py 单进程生成 .bin 目录  features/ / calendars/ / instruments/ 完整；日历覆盖 ≥15 年

5 写 manifest.json（快照 ID、行业版本、时间戳、字段、数量、bin 哈希） JSON 可解析；数量与 instruments 一致；哈希非空

6 Qlib 验收：qlib.init + D.features 单股票单年  DataFrame 非空；$close/$volume/$factor 合理；全程 available ≥3 GB

M1 内存安全规范：不用 conda base；Jupyter 非必需；采集与 Qlib 验收分进程；每批落盘即释放；dump 单进程无并行；qlib.init 启用磁盘缓存（DiskExpressionCache / DiskDatasetCache），mem_cache_size_limit 显式限制，drop_raw=True；每次操作前后 free -h 检查。

M2：Qlib 训练闭环 + MLflow 单机追踪

（历史规划）目标：用 qrun 跑通 LightGBM + Alpha158 + 回测全链路，所有实验进 MLflow。当前实现情况见附录 B、D。

配置 MLflowExpManager，统一 tracking URI 与 provider_uri。

至少一个 YAML workflow：train/valid/test 切分 → 训练 → SignalRecord → PortAnaRecord。

DataHandler 强制磁盘缓存；单次 qrun 单模型，不并行。

验收：连续 3 次相同配置产生 3 个可复现 MLflow run；params / IC / 回测指标 / 模型 artifact 均可见；R.get_recorder() 可定位 artifact。

M3：模型注册与 FastAPI 服务化

（历史规划）目标：Qlib 训练产物接入 MLflow Model Registry，FastAPI 提供批量打分服务。当前服务实现见附录 E、F。

从 Qlib MLflow run 提取模型 artifact，以 pyfunc 或项目内约定 flavor 注册。

FastAPI /predict：入参 date，返回 universe 全量排序分数；轻量适配层构造 DatasetH 调 model.predict。

门禁规则：首版手动 Staging → Production，或基于回测 IC 阈值自动晋升。

验收：Registry 至少 1 个 Production 版本；FastAPI 输出与 Qlib 本地 model.predict 一致；服务启动不构造全量 DatasetH，按请求构造即用即释放。

M4：容器化与 K8s 部署（概念规划级）

（历史规划，未按此方案实施）目标：FastAPI / MLflow / PostgreSQL / Grafana 部署到单节点 kind。当前采用本地 Docker Compose，不使用 Kubernetes。

镜像：Qlib 运行时（.bin 数据通过 PVC 挂载，不打进镜像）、FastAPI 服务。

Manifests：Deployment / Service / ConfigMap / PVC；MLflow + PG 用 StatefulSet 或本地持久卷。

GitHub Actions：lint + test → 镜像构建 → kind load → kubectl apply → /health + /predict 冒烟测试。

验收：kind 内服务可达；MLflow UI 可访问；重启 Pod 数据不丢。执行前停止所有 Qlib 训练进程，kind 节点只跑最小集合。

M5：监控闭环——数据漂移与模型失效

（历史规划）目标：建立训练后的监控流程。当前已实现独立批处理监控、PostgreSQL 指标存储和 Grafana 展示，但未实现自动重训练与自动部署闭环。

漂移 Job：按特征分批计算当前窗口 vs 训练基线 PSI / KS，写 PostgreSQL drift_metrics。

失效 Job：滚动计算近 N 日 IC / RankIC，与训练基线对比，阈值（如 IC 降 30% 或 PSI > 0.3）触发告警事件写 PostgreSQL。

Grafana：漂移面板（PSI 热力图/时序 + 阈值线）、模型健康面板（IC 滚动曲线 + 回撤/Sharpe 辅助展示）。

可选演示：漂移告警 → workflow_dispatch 触发重训练，不做生产级自动回滚。

验收：人为注入偏移数据后下个 Job 周期 PSI 上升可见；IC 面板展示 ≥30 个交易日滚动值；告警仅由 IC 衰减 + PSI 触发，回测指标不参与告警逻辑。
6. 核心难点与设计对策
难点  对策
1 .bin 格式不携带版本/来源/行业元数据 manifest.json 作为血缘契约；训练 run params 记录 manifest ID

2 Qlib Recorder 不自动调 mlflow.register_model，模型对象序列化不直接兼容 Registry 薄导出器：从 recorder 取模型，包装为 pyfunc 兼容类或按约定 module_path 重建；首版接受“Registry 存 artifact + 元数据，服务侧约定重建”，不在 Qlib 内打补丁

3 量化场景 PSI/IC 口径歧义（截面 vs 时序，基线来源）  M5 前先写“监控指标契约”文档：每指标的计算维度、窗口、基线来源（训练 run artifact 冻结统计量）、阈值

4 Qlib 与 pandas/numpy/yfinance 版本兼容  M1 步骤 0 先锁兼容矩阵：Python 3.10；只装 pyqlib+pandas+numpy 验证 import qlib，再装 yfinance 验证未被升级，最后 freeze

5 7.6 GB 内存约束 500 只 dev_universe；磁盘缓存 + 内存上限 + drop_raw；分进程执行；Swap 仅兜底；M1–M3 不启 Docker
7. 资源与边界说明
目标态 vs 开发态：README 描述“面向 2000 只美股三大指数”的终态设计；实际验收以 500 只 dev_universe 为准。取舍理由写入 README——资源约束下用子集验证工程链路，目标态通过磁盘缓存/分批处理支撑全量。这是真实工程决策。

明确不做：GPU 资源管理、Agent 开发、Prometheus（二期）、自动化数据调度采集（手动 yfinance）、K8s 生产级编排（HPA/Ingress/多节点）。

二期候选：Prometheus 服务指标、按股票列表查询的推理接口、自动重训练闭环、target_universe 全量性能摸底。
8. 技术栈清单
层  组件

语言/环境 Python 3.10，uv / venv，WSL2 Ubuntu

量化引擎  Qlib（pyqlib，锁定版本），Alpha158，LightGBM

实验追踪/注册 MLflow Tracking Server + Model Registry

数据存储  Qlib .bin（本地卷），PostgreSQL（元数据/指标/行业映射）

服务化  FastAPI

容器/编排 Docker，kind（单节点）

CI/CD GitHub Actions

监控可视化  Grafana（直连 PostgreSQL）

数据源  yfinance（手动采集），开源 GICS 映射表

版本：v1.0（定稿）

锁定日期：2026-10-19 09:30 am
附录 A：M1 实际产出（2026-10-21 封存）
A.1 版本矩阵（锁定）
| 组件 | 版本 | 说明 |

|---|---|---|

| Python | 3.11.16 | uv 管理的项目 venv |

| pyqlib | 0.9.7 | uv add "pyqlib>=0.9.7,<0.10" |

| pandas | 2.2.3 | 锁 >=2.1,<2.3，避开 3.0 破坏性变更 |

| numpy | 1.26.4 | 锁 >=1.26,<2.0，避开 numpy 2.x 与回测模块不兼容 |

| yfinance | 1.7.0 | 手动采集，不自动调度 |

| 构建工具 | uv | uv venv + uv add + uv run |
A.2 Universe 快照
| 项 | 值 |

|---|---|

| snapshot_id | us_2026-10-19_dev500 |

| target_universe | 518 只（S&P 500 ∪ NASDAQ 100 ∪ Dow Jones，去重） |

| dev_universe | 500 只（按 market_cap 取 Top 500） |

| 行业映射 | 100% 覆盖，10 方向；Materials=5、Communication Services=9 样本偏薄，M5 分组监控需注意 |
定稿修正：架构 v1.0 D1 原写 target=2000 只，实测三大指数去重后为 518 只。修正为 target=518、dev=500。2000 只对应“美股全市场按流动性筛选”，属二期范围。

A.3 数据产出
| 项 | 值 |

|---|---|

| 原始数据 | data/raw/us_daily/ 500 CSV，1,772,300 行，成功率 100% |

| Qlib .bin | data/qlib_bin/us_data/，47 MB，压缩比约 40:1 |

| 日历 | 2011-10-24 → 2026-10-16，3767 交易日 |

| 字段 | open, high, low, close, volume, factor |

| 质量检查 | close 无 NaN、factor>0、volume≥0，issues=0 |

| 峰值内存 | 0.22 GB（远低于 3 GB 安全线） |
A.4 数据契约（入库）
| 文件 | 作用 |

|---|---|

| data/universe/universes.yaml | target/dev 双轨快照 + 缩减规则 + 源 URL |

| data/universe/industry_mapping.csv | symbol → source_sector → 10 方向 |

| data/qlib_bin/us_data/manifest.json | 血缘锚点：snapshot_id、日历、字段、三个目录 sha256 |
A.5 脚本清单
| 脚本 | 职责 |

|---|---|

| scripts/m1_fetch_sources.sh | 下载三个指数源 CSV 到 data/universe/raw/ |

| scripts/m1_build_universe.py | 生成 universes.yaml + industry_mapping.csv |

| scripts/m1_collect_raw.py | 分批 50 只 yfinance 采集，落盘即释放 |

| scripts/qlib_tools/dump_bin.py | 从 Qlib GitHub 下载的 dump 工具（pip 包不含） |

| scripts/m1_write_manifest.py | 生成 manifest.json |

| scripts/m1_acceptance.py | 日历/instruments/样本/内存四项验收 |
A.6 M1 复现顺序

bash scripts/m1_fetch_sources.sh

uv run python scripts/m1_build_universe.py

uv run python scripts/m1_collect_raw.py

uv run python scripts/qlib_tools/dump_bin.py dump_all \\

  --data_path data/raw/us_daily --qlib_dir data/qlib_bin/us_data \\

  --include_fields open,high,low,close,volume,factor --max_workers 2 --freq day

uv run python scripts/m1_write_manifest.py

uv run python scripts/m1_acceptance.py





M4 架构修正（2026-10-30）：

原计划在 FastAPI 内实时计算 Alpha158，但在 K8s 部署时遭遇 OOM。深究原因，是因为特征计算与模型服务高度耦合，且加载了 15 年全量历史数据。现修正为“离线特征计算（Parquet）+ 在线轻量加载”。此修正彻底解决了内存瓶颈，也是真实量化私募的标准架构（特征与推理分离）。



---



## 附录 B：M2a 单窗口 qrun 闭环（2026-10-21 封存）



### B.1 目标与范围



M2a 的目标是验证 **qrun + Alpha158 + LGBModel + MLflow + 回测** 这条链路在 US 数据上能完整跑通，**不做调参、不做多窗口**。多窗口 walk-forward 属 M2b。



### B.2 配置



- 文件：`configs/workflow_config_lightgbm_Alpha158_us.yaml`

- 基线：Qlib 官方 `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`（71 行）

- 窗口：W1（train 2011-10-24→2017-12-31 / valid 2018 / test 2019）



相对官方版的改动：



| 字段 | 官方值 | 本项目值 | 原因 |

|---|---|---|---|

| provider_uri | `\~/.qlib/qlib_data/cn_data` | `data/qlib_bin/us_data` | 本地 US 数据 |

| region | `cn` | `us` | 美股交易规则 |

| market / benchmark | `csi300` / `SH000300` | `all` / `all` | 仅有 all.txt；无 SPX 数据，基准临时用 universe 自身 |

| 全部日期 | cn 区间 | W1 区间 | 对齐数据和 walk-forward 首窗口 |

| limit_threshold | `0.095` | 删除（region=us 默认 None） | 美股无统一涨跌停 |

| num_threads | `20` | `8` | 7.6 GB 内存约束 |



### B.3 MLflow 后端



- **文件后端（`./mlruns`）已不可用**：MLflow 3.13+ 将 filesystem tracking backend 标记为维护模式，默认拒绝。

- **采用 SQLite 后端**：YAML 的 `qlib_init.exp_manager` 配置 `uri: "sqlite:///mlflow.db"`。

- UI 启动必须与 qrun 指向同一后端：`mlflow ui --backend-store-uri sqlite:///mlflow.db`。

- `mlflow.db` 与 `mlflow.db-journal` 加入 `.gitignore`，不入库。

- 已装 MLflow 版本：3.17.1。



### B.4 运行结果



| 项 | 值 |

|---|---|

| 数据加载 | 37.3 s（Alpha158 全量因子计算） |

| 训练 | LightGBM early stopping，best iteration = 4 |

| IC / ICIR | 0.0058 / 0.0502 |

| Rank IC / Rank ICIR | 0.0063 / 0.0657 |

| Benchmark 年化（universe 自身） | 30.35% |

| 超额收益（无成本）年化 | 5.90%，IR 0.34 |

| 超额收益（含成本）年化 | 1.10%，IR 0.06 |

| Artifacts | `pred.pkl`、`port_analysis_1day.pkl`、`indicator_analysis_1day.pkl` |



**观察**：IC 极低、best iteration=4，说明模型在 valid 段几乎未学到有效信号。M2a 的目标是链路验证，此结果不阻塞验收；M2b 做 7 窗口时再评估 IC 分布是否稳定。



### B.5 已知警告（不影响链路）



- `ModuleNotFoundError: CatBoostModel / XGBModel / PyTorch models are skipped`：未安装可选模型依赖，Qlib 跳过，正常。

- `Gym has been unmaintained since 2022 and does not support NumPy 2.0`：Qlib 回测模块的传递依赖提示，当前 numpy 1.26.4 未触发实际问题。

- `load calendar error: freq=day, future=True; return current calendar!`：`.bin` 无 future calendar，回测回退到当前日历，T+1 模拟精度略降，不阻塞 M2a。

- `RuntimeWarning: Mean of empty slice`：回测中某些日期截面为空时的 nanmean 警告，正常。



### B.6 M2b 决策：逐窗口 qrun（方案 A）



M2b 采用**逐窗口独立 qrun**，不用 `RollingGen` + `TrainerRM`。理由：



1. 每个窗口一个 MLflow run，失败可单独重跑；

2. 内存峰值 = 单窗口峰值，符合 7.6 GB 约束；

3. M5 监控只需每窗口 IC 时间序列，不需要 `RollingEnsemble` 合并预测；

4. 符合小型私募“跑 N 次、每次可查、出问题单独修”的工程现实。



7 窗口规则（已锁定）：



| 窗口 | train | valid | test |

|---|---|---|---|

| W1 | 2011-10-24 → 2017-12-31 | 2018 | 2019 |

| W2 | 2011-10-24 → 2018-12-31 | 2019 | 2020（含疫情崩盘） |

| W3 | 2011-10-24 → 2019-12-31 | 2020 | 2021 |

| W4 | 2011-10-24 → 2020-12-31 | 2021 | 2022 |

| W5 | 2011-10-24 → 2021-12-31 | 2022 | 2023 |

| W6 | 2011-10-24 → 2022-12-31 | 2023 | 2024 |

| W7 | 2011-10-24 → 2023-12-31 | 2024 | 2025-01-01 → 2026-10-16 |



rtype = `ROLL_EX`（扩展窗口，训练起点固定）。





## 附录 D：M2 正式关闭与产出总结（2026-10-24）



### D.1 M2 核心目标达成情况



M2 的目标是：**验证 Qlib 训练闭环，打通 MLflow 追踪，并利用 walk-forward 评估模型在不同市场环境下的稳定性。**



- **M2a（单窗口）**：跑通 `qrun`，成功将 LGBModel + Alpha158 训练、预测、回测记录到 MLflow（SQLite 后端），解决文件后端维护模式问题。

- **M2b（7 窗口）**：完成 W1–W7 的扩展窗口（ROLL_EX）训练。发现 W3（2021）和 W5（2023）IC 为负，验证了模型在不同 regime 下的脆弱性。

- **M2b-3/4（特征分析）**：完成 7 窗口特征重要性对比与 W2 窗口的 SHAP 分析，定位了“稳定因子”（STD60）与“失效先兆因子”（如 CORR60、MA20）。



### D.2 关键工程决策与踩坑记录



1. **MLflow 后端迁移**：由于 MLflow 3.13+ 限制，放弃 `./mlruns` 文件后端，切换至 SQLite（`sqlite:///mlflow.db`），为 M3 Model Registry 铺路。

2. **基准切换**：M2a 临时使用 `all` 作为基准导致超额收益失真；M2b 引入 SPX（^GSPC）数据并剔除出训练集，还原真实基准。

3. **OOM 应对**：W7 训练集最长（12.2 年），叠加 Qoder 抢占内存触发 OOM Kill。通过关闭无关进程、单独重跑解决，验证了 7.6GB 环境下的内存边界。

4. **模型序列化**：默认 `qrun` 不保存模型对象，在 `task.record` 中追加 `SaveModel`，才使得特征重要性分析和 SHAP 成为可能。

5. **SHAP 数据对齐**：通过 `pred.pkl` 的索引按需拉取特征，规避了 `dataset` 序列化丢失 `_infer` 缓存的问题。



### D.3 业务洞察（用于 M5 监控设计）



- **模型回测表现**：7 窗口 IC 均值约 0.0076，标准差 0.0095，W3/W5 出现负 IC。这说明 Alpha158 + LGBM 的基线模型在风格切换年（2021 成长股见顶、2023 AI 行情）会失效。

- **幸存者偏差**：当前 Universe 为 2026-10 静态快照，离快照越近的窗口（W6/W7）超额收益虚高，不可作为模型能力证据。Point-in-Time Universe 列为二期扩展。

- **核心因子与漂移**：仅有 `STD60`（60日波动率）在 7 个窗口中持续排名前 20。`CORD10`、`CORR60`、`MA20` 等在 W1 重要，但在 W3/W5 中排名骤降，构成了 M5 监控的“失效先兆”指标。

- **SHAP 解释**：W2（疫情年）的模型极度依赖极端非线性特征（如 `MA20` 极端高值、`OPEN0` 跳空高开），表现出强烈的“追涨”倾向，这也解释了 W2 超额高但回撤大的原因。



### D.4 产出与状态



- **Configs**：`configs/workflow_config_lightgbm_Alpha158_us.yaml`、`configs/walkforward/w1-w7.yaml`

- **Scripts**：`m2_generate_walkforward.py`、`m2_run_walkforward.sh`、`m2_feature_importance.py`、`m2_shap_analysis.py`

- **Monitoring Data**：`data/monitoring/walkforward_summary.json`、`data/monitoring/feature_importance/`（分析 JSON + SHAP 图）

- **状态**：M2 正式关闭。M3 将基于 7 个 run，挑选相对稳定的模型进行 Registry 注册与 FastAPI 服务化。





## 附录 E：M3 模型注册与 FastAPI 服务化（2026-10-27 封存）

> **历史方案说明：** 本附录记录 M3 阶段的初始服务架构。当时曾尝试在 FastAPI 启动时加载 Qlib Alpha158 Handler 并在线计算特征。该方案后来因内存占用过高，在 M4 阶段被离线 Parquet 特征架构取代。当前架构以附录 F 为准。



### E.1 目标

将 Qlib 训练的黑盒模型封装为 MLflow 标准格式，注册到 Model Registry，并提供基于 FastAPI 的批量打分服务，确保推理特征与训练特征完全一致。



### E.2 核心决策与踩坑

- **Model Registry**：采用 MLflow `pyfunc.PythonModel` 包装 Qlib 模型。因为 Qlib 原生模型存在 `params.pkl` 中且无法直接生成 MLflow 标准签名，必须套壳（Wrapper）并暴露 `predict(df)` 接口。

- **别名控制**：不使用已废弃的 Stage 概念，改用 MLflow 3.x 的 **Alias**。模型先以 `@candidate` 别名人库，线上服务绑定该别名，实现训练与部署解耦。

- **FastAPI 启动预热**：在 lifespan 阶段实例化 `Alpha158` Handler，预加载 `.bin` 数据并建立特征计算缓存；同时加载 Registry 中的模型。保证每次请求能在毫秒级完成。

- **推理数据一致性（Training-Serving Skew）**：线上推理必须走与训练完全一致的 `Alpha158` 特征工程链路。在线推理时，从 `handler.fetch(selector=slice(date, date))` 取单日截面，输入给底层 LightGBM。

- **索引层级塌缩**：Qlib 的 `fetch` 在只取单日数据时，DataFrame 的列索引（Columns）会从两层 `(feature, factor)` 塌缩为一层 `(factor)`。服务代码中需做 `isinstance(df.columns, pd.MultiIndex)` 兼容判断，否则会报错。



### E.3 产出

- 脚本：`scripts/m3_register_model.py`（支持 `--run-id` 指定训练窗口注册）、`app/main.py`（推理服务）

- 注册模型：`us-stock-lgbm-1d`（别名 `@candidate`），描述中包含源 Run 的 IC 值。

- 接口：`GET /health`、`GET /predict?date=YYYY-MM-DD` 返回按打分降序排列的股票列表。





## 附录 F：M4 轻量化服务与本地容器化（2026-10-30 封存）

> 本附录记录 M4 阶段的历史实施过程。期间曾尝试使用 kind 部署并因此暴露资源瓶颈；该 Kubernetes 方案后来取消。当前服务采用 Docker Compose 在本地 WSL2 环境运行。*



### F.1 目标与架构重构说明

M4 的目标是将推理服务容器化，并解决此前在本地 K8s（kind）部署时引发的严重资源问题。

**核心重构**：废弃了原定“在 FastAPI 启动时使用 Alpha158 实时计算特征”的重型方案。该方案在 K8s 限制 4Gi 内存时被 `OOMKilled`（Exit Code 137），根本原因是加载 15 年全量历史数据并实时计算的特征工程与模型推理高度耦合。

**最终架构**：彻底解耦为“离线特征生成（Parquet）+ 在线轻量加载”。这也符合真实量化私募中特征计算与推理解耦的工业标准。



### F.2 核心实现

1. **轻量级 Feature Store 雏形**

   - 脚本：`scripts/m4_generate_features.py`

   - 逻辑：使用 Qlib `Alpha158` 计算近 2.5 年（`2024-01-01` 至 `2026-10-16`）的特征数据，并存储为 `data/features/features_latest.parquet`（232MB）。

   - 目的：覆盖因子的历史回看窗口（如 60 日），同时避免内存爆掉。

2. **FastAPI 极简改造**

   - 移除 `Alpha158` 和 `qlib.data` 的实例化。

   - 启动时直接用 Pandas 加载 Parquet 文件，截取目标日期的截面数据。

   - 效果：启动时间从十几秒降至 1 秒以内，内存占用降至百兆级别。

3. **Docker 多阶段构建与极致瘦身**

   - **多阶段**：在 `builder` 阶段安装依赖并清理死重包，只把 `.venv` 复制到运行阶段，彻底减小镜像分层体积。

   - **剔除依赖**：在 `uv sync` 时通过 `--no-install-package` 屏蔽了 Jupyter 全家桶；在 builder 中物理删除 `llvmlite`、`numba`、`yfinance`、`jedi` 等运行时无效的包。

   - **精确 COPY**：废弃 `COPY . .`，只复制 `app/` 目录，防止无用文件进入镜像。

   - **最终镜像**：`quant-qlib-api:v6`（约 1.2GB）。



### F.3 关键踩坑记录

1. **K8s 目录映射**：kind 节点无法直接读取宿主机目录，需在 `kind create cluster` 时通过 `extraMounts` 将项目根目录映射到节点容器内，否则 `hostPath` 会卡住。

2. **MLflow 绝对路径陷阱**：由于 SQLite 里记录的是宿主机的绝对路径（`/home/mlops/...`），在 Docker 中 `-v` 挂载时必须映射到相同的绝对路径，否则 MLflow 会报 `No such artifact: ''`。

3. **系统依赖缺失（`libgomp1`）**：极度精简的 `python:3.11-slim` 基础镜像缺失了 OpenMP 运行库。在加载 LightGBM 模型时报 `OSError: libgomp.so.1: cannot open shared object file`。必须在 Stage 2 的 Dockerfile 中加上 `apt-get install libgomp1`。



### F.4 产出清单

- `scripts/m4_generate_features.py`：离线特征生成脚本。

- `app/main.py`：轻量化 FastAPI 服务。

- `Dockerfile`：多阶段极简构建文件（含 `libgomp1` 修复）。

- `docker-compose.yml`（M5 底座）：用于启动 PostgreSQL 和 Grafana。



### F.5 后续演进规划（历史记录）



M4 阶段曾规划将推理服务部署至 AWS ECS/EC2，并通过 GitHub Actions 实现持续训练和模型更新。



该方案属于当时的演进设想，并未实际实施。当前项目已经完成 Docker 镜像构建及 GHCR 发布，但尚未实现 AWS 自动部署和云端持续训练。



当前系统的训练、数据处理和日更任务仍在本地环境执行。





## 附录 G：M5 监控与告警实现记录（2026-11-04 封存）

### G.1 目标与架构设计

M5 的目标是建立模型预测效果与特征分布的监控能力。当前采用：**独立 Python 批处理 + PostgreSQL 指标存储 + Grafana 可视化与 Webhook 告警**。

### G.2 核心模块与产出

- **指标记录**
  - `scripts/m5_record_metrics.py`：初始化监控表，导入历史 Walk-forward 窗口 IC / Rank IC。
  - `scripts/m5_prediction_nextday_return_record.py`：调用 FastAPI 预测接口，将预测结果写入 `predictions` 表，并为后续实际收益反馈提供数据基础。
- **漂移计算（PSI）**
  - 当前脚本：`scripts/m5_calculate_daily_psi.py`。
  - 采用横截面特征分布监控口径，计算结果写入 PostgreSQL。
- **预测效果监控**
  - 当前脚本：`scripts/m5_calculate_daily_ic.py`。
  - 仅对已具备实际收益反馈的成熟预测日期计算 IC / Rank IC，并按交易日进行日期对齐。
- **可视化（Grafana）**
  - 模型历史表现：展示 Walk-forward 窗口 IC / Rank IC。
  - 每日预测：展示最新预测结果及相关记录。
  - 数据漂移：展示 PSI 指标及其变化。
- **告警**
  - PSI 达到设定阈值时，监控脚本可通过 Webhook 发送告警。
  - 告警用于提示人工检查，不会自动启动训练、模型晋升或部署。

### G.3 历史监控与模型迭代验证

在 2026 年 11 月的历史行情数据分析中，监控系统检测到 `CNTP60`、`MAX60` 等特征的 PSI 显著升高，最高达到 **8.23**，并通过 Webhook 发出告警。

随后，结合监控结果开展了 W8 窗口的模型重新训练和回测评估。W8 在对应历史评估区间内取得了 **15.99% 的超额收益**，最大回撤为 **-9.04%**。

上述结果属于特定历史区间内的回测实验结果，用于记录模型重新训练和评估过程，不代表真实资金交易收益，也不能据此推断模型在未来市场环境下具有稳定的盈利能力。

本阶段完成了监控告警与人工模型迭代验证的工程实践，但未实现 PSI 告警自动触发训练、自动模型晋升和自动部署的完整 Continuous Training 闭环。

### G.4 重训练触发与处置原则

当前采用“监控提示、人工核查、人工决策”的处置方式：

1. **PSI 漂移提示**：核心特征 PSI 超过设定阈值时，记录并发送告警。需要先核查行情数据质量、特征生成结果及异常是否由短期市场波动造成。
2. **预测效果评估**：预测日期具备实际收益反馈后，计算 IC / Rank IC。结合滚动表现和特征漂移综合判断，不以单日 IC 或单次 PSI 结果直接认定模型失效。
3. **人工决定是否重训**：确认数据可靠且模型表现值得进一步调查后，由人工启动训练与 Walk-forward 评估。
4. **人工决定模型更新**：评估新模型后，由人工决定是否注册新版本及更新 MLflow Alias。

当前没有“PSI 告警 → 自动训练 → 自动评估 → 自动晋升 → 自动部署”的自动化流程，也没有实盘交易或灰度交易上线环节。

### G.5 产出清单

- `docker-compose.yml`：本地 PostgreSQL、Grafana、Adminer 服务配置。
- `monitoring/dashboards/mlops_dashboard.json`：Grafana 看板。
- `scripts/m5_calculate_daily_ic.py`：成熟预测日期 IC / Rank IC 计算。
- `scripts/m5_calculate_daily_psi.py`：每日 PSI 计算。
- `scripts/m5_prediction_nextday_return_record.py`：预测结果记录及实际收益反馈相关处理。


## 附录 H：每日自动化流水线与数据质量（M5/M6/M7，2026-11-05 起持续演进）*



### H.1 每日批处理流水线（Daily Pipeline）



通过 `scripts/m5_run_daily_pipeline.sh` 实现本地日更批处理流程，串联数据更新、质量检查、Qlib 数据更新、特征生成、模型推理及监控任务。



主要处理步骤：



1. **增量数据更新**：`m5_update_daily_data.py` 从 yfinance 获取美股日频行情数据。

2. **数据质量检查**：`data_quality_check.py` 执行数据质量门禁，检查 OHLC、价格、成交量等异常数据。

3. **Qlib 数据更新**：更新 Qlib `.bin` 数据集及相关数据版本记录。

4. **特征生成**：`m4_generate_features.py` 离线生成 Alpha158 特征，并更新 Parquet 特征文件。

5. **API 服务刷新**：特征更新后重启 FastAPI，通过 `/health` 接口检查服务恢复状态，确保使用最新特征数据。

6. **预测结果入库**：`m5_prediction_nextday_return_record.py` 调用 FastAPI 批量推理接口，将预测结果写入 PostgreSQL。

7. **IC 监控**：`m5_calculate_daily_ic.py` 对已具备实际收益反馈的预测日期进行评估，计算 IC 和 Rank IC，并写入数据库。

8. **PSI 漂移监控**：`m5_calculate_daily_psi.py` 计算特征分布漂移指标，异常时通过 Webhook 发出告警。



上述流程在本地 WSL2 环境执行。数据源可用性、交易日数据完整性及网络限流可能影响实际数据更新结果，流水线执行成功不等于所有外部数据源均提供了完整的最新行情。



### H.2 样本外 IC 与实际收益反馈



监控系统将模型预测记录与后续实际市场收益进行匹配，计算样本外 IC（Out-of-sample IC）和 Rank IC。



历史运行中观察到以下单日 IC：



- 2026-10-16：IC = -0.1784

- 2026-10-30：IC = 0.3857

- 2026-11-02：IC = -0.3303



单日 IC 存在明显波动，因此不能仅凭单日指标判断模型失效，需要结合滚动 IC、Rank IC 及特征分布变化进行综合评估。



这里的实际收益反馈是基于历史市场行情计算的模型预测评价，不代表真实资金交易结果。



### H.3 模型重训练与迭代验证（M7）



在 M7 阶段，结合 PSI 漂移监控和模型历史表现，开展了基于历史 Walk-forward 窗口的 W8/W9 模型重新训练与对比验证。



在对应历史回测区间内，W9 的最大回撤由此前记录的 -9.04% 变为 -7.48%。该结果仅用于比较特定历史区间内的回测表现。



该结果表明，在特定历史区间内，新模型的回测风险指标有所改善，但不能据此推断模型在未来市场中具有稳定的收益或防御能力。



当前已完成模型重新训练、评估和注册相关工程实践；训练、评估及模型 Alias 更新仍由人工控制，尚未实现由 PSI 告警自动触发训练、自动评估、自动晋升及自动部署的完整 Continuous Training 闭环。









---



## 附录 I：数据质量门禁升级与 W1-W9 批量重训实战（2026-11-06 封存）



### I.1 数据质量门禁升级（Data Quality Gate）

**痛点**：M1 初始采集仅检查 `close` 是否为空，导致在盘中/节假日采集到大量“半成品”数据（如 `open` 缺失但 `close` 有值，或 `volume=0`）。

**升级**：`data_quality_check.py` 与 `m5_update_daily_data.py` 统一实施严格门禁：

- **规则**：`open/high/low/close` 任一缺失，直接剔除；`close <= 0` 或 `volume <= 0`，直接剔除。

- **效果**：单次全量清洗剔除了 5800+ 行历史脏数据。彻底避免了 Alpha158 计算 K 线形态因子时产生 `NaN` 或 `Inf`，防止模型学到虚假信号。



### I.2 清洗前后的 W1-W9 回测指标对比

| 窗口 | 清洗前 IC | 清洗后 IC | 清洗前超额 | 清洗后超额 | 结论 |

|---|---|---|---|---|---|

| W4 (2022) | 0.0029 | -0.0030 | 1.08% | -4.61% | 剔除虚假盈利，暴露出熊市真实风险 |

| **W5 (2023)** | **-0.0004** | **0.0079** | **-2.38%** | **+9.69%** | **惊天反转，证明脏数据严重污染因子** |

| W6 (2024) | 0.0131 | 0.0140 | 14.47% | 6.00% | IC 略微提升，收益回归合理区间 |

| W8 (2025) | 0.0179 | 0.0158 | 15.99% | 11.78% | 回撤由 -9.04% 收缩至 -7.48% |

| W9 (2026) | 0.0158 | 0.0135 | 12.14% | 12.14% | 模型稳定性及防守能力显著增强 |



**核心结论**：Garbage In, Garbage Out。脏数据不仅会扭曲回测收益，还会掩盖模型真实的失效风险。数据质量门禁是 MLOps 体系的第一道防线。



### I.3 批量同步脚本

为应对开发期间的历史 run 堆积，新增 `dev_sync_batch_metrics.py`，支持精准抓取最新的 N 个 run 并全量覆盖 `walkforward_metrics` 表，保障 Grafana 面板数据一致性。







---



## 附录 J：CI、Docker 容器化与日更链路验收（2026-11-11）



### J.1 CI 自动化



建立 GitHub Actions 持续集成流程（`.github/workflows/ci.yml`）：



- 使用 Python 3.11 + uv，执行依赖锁定检查（`uv lock --check`）。

- 执行 Ruff 静态检查。

- 执行 Python 全项目编译检查。

- GitHub Actions 连续两次运行通过。



### J.2 Docker 容器化



完成 FastAPI 服务镜像构建配置：



- 采用多阶段 Dockerfile，分离依赖构建与运行环境。

- 仅安装 serving 依赖，排除 Jupyter 等非必要组件。

- 安装 LightGBM 所需系统依赖 `libgomp1`。

- 精确复制 `app/`，通过 `.dockerignore` 排除数据、模型和开发文件。

- Docker Compose 统一管理 API、PostgreSQL、Grafana 和 Adminer。



### J.3 数据库与安全配置



- 新增 `scripts/db_config.py`，统一管理 PostgreSQL 连接配置。

- 六个业务脚本统一使用环境变量读取数据库凭据，消除硬编码旧密码。

- 使用项目根目录 `.env` 管理本地密码，并通过 `.gitignore` 排除真实环境配置。

- Grafana 数据源改为容器环境变量注入数据库密码。

- PostgreSQL、Grafana 管理员密码完成更换。

- 四个服务的宿主机端口统一绑定 `127.0.0.1`，限制外部网络访问。



### J.4 日更流水线稳定性改进



- 特征生成后自动重启 FastAPI，解决 Parquet 文件更新后服务仍使用旧内存数据的问题。

- 增加 API 重启等待与 `/health` 健康检查，确认服务恢复后再执行预测。

- 保持原有 IC、PSI 独立监控流程，不扩大流水线改动范围。

- 修复 `m5_prediction_nextday_return_record.py` 中重复定义数据库连接变量、覆盖统一配置的问题。



### J.5 集成验收结果



2026-11-10 至 2026-11-12，完成多次本地日更流水线运行及集成验证。



主要验证结果：



- 数据更新、数据质量检查和 Qlib `.bin` 更新流程正常执行。

- Manifest、Lineage run records 和数据质量报告正常生成或更新。

- Parquet 特征生成正常。

- FastAPI 自动重启及 `/health` 健康检查正常。

- 批量预测及 PostgreSQL 结果写入正常。

- IC、Rank IC 和 PSI 监控脚本完成集成验证。

- Grafana PostgreSQL 数据源连接正常。

- GitHub Actions CI 检查通过。

- Docker 镜像构建及 GHCR 发布流程已完成验证。



2026-11-12 完成一次本地日更流水线运行，相关数据质量报告、Lineage 记录和 Manifest 已更新并同步至 GitHub。



**验收结论：** 当前已完成从数据更新、特征生成、模型推理到结果存储和监控的端到端本地工程链路验证。



### J.6 项目最终状态（v0.1.1）

| 模块 | 当前状态 |
|---|---|
| 数据处理与质量检查 | 已完成 |
| Qlib 数据底座与数据血缘 | 已完成 |
| Alpha158 离线特征工程与 Parquet 存储 | 已完成 |
| 模型训练与 Walk-forward 历史评估 | 已完成 |
| MLflow 实验追踪与模型注册 | 已完成 |
| FastAPI 批量推理服务 | 已完成 |
| IC、Rank IC、PSI 监控 | 已完成 |
| 本地每日批处理流水线 | 已完成并通过集成验证 |
| PostgreSQL + Grafana 监控 | 已完成 |
| Docker 多阶段构建与 Compose 配置 | 已完成 |
| GitHub Actions CI | 已完成 |
| GHCR Docker 镜像构建与发布 | 已完成 |
| Grafana Labs Public Snapshot 展示 | 已发布，用于项目成果展示 |
| AWS 完整 MLOps 系统部署 | 不实施 |
| AWS EC2 自动部署及云端日更 | 不实施 |
| Kubernetes / kind 部署 | 不属于当前项目范围 |
| 自动化 Continuous Training 闭环 | 尚未实现 |

当前版本：`v0.1.1`

当前主要运行环境为 Ubuntu on WSL2。数据更新、特征生成、模型训练、批量推理和监控批处理均在本地环境执行；Docker Compose 用于本地服务管理；GitHub Actions 负责 CI 检查及 Docker 镜像构建与发布，镜像存储于 GHCR。

Grafana 监控看板通过 Grafana Labs Public Snapshot 对外展示，不依赖 AWS EC2。当前没有将完整 MLOps 系统、日更流水线或数据库部署到云端的计划。

**项目后续方向：** 完成当前 MLOps 项目的文档整理与归档后，进入 LLMOps