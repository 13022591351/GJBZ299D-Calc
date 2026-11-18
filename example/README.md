# GJB299D-Calc 综合示例集（100% 合成工程验证套件）

本目录包含面向 **GJB299D-Calc (v1.0)** 全功能的合成工程示例与回归验证数据集，涵盖 BOM 导入、第 5 章及附录 C 工作状态应力法、附录 B.2 非工作状态详细法、第 6 章及附录 B.3/C.3 元器件计数法、子电路与外贴微电路 SHA-256 级联绑定、整机模块化集成、专项分析（附录 A/D/E/F），以及完整报告与供应商脱敏交付物。

> [!NOTE]
> 本目录内所有器件位号、料号、厂商、参数与工程场景均为**人工合成的工程测试数据**，仅用于软件功能验证与使用示范。

---

## 目录总览

```text
example/
├── README.md                                  # 本指南文档
├── 01_bom_import/                             # 全格式、多编码 BOM 导入示例
│   ├── power_servo_bom_utf8.csv               # UTF-8 CSV（位号范围、多种标称值写法、国产/进口混排）
│   ├── rf_frontend_bom_gb18030.csv            # GB18030 编码中文表头 CSV
│   ├── avionics_io_bom.xlsx                   # 多工作表 .xlsx（含标题行与额外元数据列，演示预映射）
│   ├── legacy_control_bom.xls                 # Excel 97-2003 .xls（xlwt 生成）
│   └── sensor_board_bom.tsv                   # 制表符分隔 .tsv（传感器调理板）
├── 02_projects/                               # 可直接打开并完整计算的 GJB299D-Project 工程
│   ├── 01_working_stress_comprehensive.json   # 旗舰级综合工作应力法工程（含双状态与4项专项分析）
│   ├── 02_nonoperating_detailed.json          # 附录 B.2 非工作详细预计工程（常温 + 低温 -10℃）
│   ├── 03_parts_count_early_design.json       # 第6章国产 + 附录C.3进口计数法 + 附录B.3非工作双状态
│   ├── sub_mounted_microcircuits.json         # 表40 外贴微电路组成子工程（裸芯片 Tc=35℃）
│   ├── sub_lc_filter_circuit.json             # 组成子电路工程（用于 LC 滤波器与陀螺后部电路）
│   ├── 04_hybrid_and_subcircuits.json         # 混合集成电路与子电路 SHA-256 级联工程
│   └── 05_system_module_integration.json      # 整机级模块化集成工程（含工程模块与已知单件失效率）
└── 03_reports_and_redacted/                   # 由 01 综合工程导出的报告与脱敏交付件
    ├── full_report_sample.xlsx                # 完整计算数据工作簿（.xlsx）
    ├── full_report_sample.xls                 # 完整计算数据工作簿（.xls）
    ├── redacted_delivery_sample.json          # 供应商脱敏交付工程（.json）
    ├── redacted_delivery_sample.xlsx          # 供应商脱敏交付工作簿（.xlsx）
    └── redacted_delivery_sample.xls           # 供应商脱敏交付工作簿（.xls）
```

---

## 1. BOM 导入示例（[`01_bom_import/`](01_bom_import/)）

| 文件名 | 编码 / 格式 | 覆盖场景与测试要点 |
| :--- | :--- | :--- |
| [`power_servo_bom_utf8.csv`](01_bom_import/power_servo_bom_utf8.csv) | UTF-8 CSV | 覆盖范围位号展开（`R10-R13`、`C1~C4`、`U1、U2`、`R2, R3`、`C5, C6`）、阻/容/感标称值解析（`0R`、`4.7R`、`10k`、`5.62M`、`4.7nF`、`100uF`、`47uH`）与国产/进口器件混合清单 |
| [`rf_frontend_bom_gb18030.csv`](01_bom_import/rf_frontend_bom_gb18030.csv) | GB18030 CSV | 全中文表头（`位号,标称值,型号,封装,数量,制造商,说明`）与 GB18030 字符集（如 `～`、`±`、`℃`、`Ⅱ`），验证编码自动回退识别 |
| [`avionics_io_bom.xlsx`](01_bom_import/avionics_io_bom.xlsx) | Excel `.xlsx` | 首行工程标题横幅 + 额外元数据列（`物料编码`、`工艺等级`）+ 多工作表（`主BOM清单` 与 `线束接插件表`），验证区域自动定位、列映射与智能模型预映射 |
| [`legacy_control_bom.xls`](01_bom_import/legacy_control_bom.xls) | Excel `.xls` | 使用 `xlwt` 生成的传统英文表头 BOM，涵盖碳膜电阻、线绕电位器、云母/薄膜电容、晶体谐振器、LED 与固体继电器 |
| [`sensor_board_bom.tsv`](01_bom_import/sensor_board_bom.tsv) | UTF-8 TSV | 制表符 `\t` 分隔的传感器调理板清单，涵盖热敏电阻、箔式电阻、仪表运放、模数混合 IC 与压电陀螺 |

**GUI 测试步骤：**
1. 启动软件后点击 **文件 → 导入 BOM…**（`Ctrl+I`），或直接将任意 BOM 文件拖入主窗口。
2. 在两步式导入向导中确认数据区域与列映射（对 `avionics_io_bom.xlsx` 可切换工作表查看）。
3. 进入智能预映射向导，勾选推荐模型后查看阻/容/感标称值自动带入结果。

---

## 2. 工程计算示例（[`02_projects/`](02_projects/)）

### 2.1 [`01_working_stress_comprehensive.json`](02_projects/01_working_stress_comprehensive.json)（旗舰级综合工程）
- **启用工况（3 项）：**
  1. `地面良好 GB @ 25℃`：测试 30℃ 起表模型（附录 C 进口器件等）的**温度向上取整**边界。
  2. `一般地面固定 GF1 @ 55℃`：常规机载/地面固定热应力工况。
  3. `战斗机座舱 AIF @ 70℃`：高温高环境系数工况。
- **涵盖条款与核心能力：**
  - **集成电路（5.1.2 & C.2.2.2）：** `国产普通数字集成电路`（绑定 `input_sources`：5.1.1.3 晶体管换算等效门数 + 式(2) 表24/表25结温估算）、`国产模拟集成电路`（非表列结温 `Tj=47.6℃` 自动向上取整）、`进口微处理器`。
  - **分立半导体（5.3 & C.2.4）：** `国产双极型双管`（式 24 交叉热耦合，散热片管壳温度 `Tc=55℃`）、`国产硅场效应晶体管`（带散热片管壳温度 `Tc=50℃`）、`国产普通二极管`、`进口普通二极管`。
  - **无源元件（5.6 / 5.9 / 5.10 & C.2.7 / C.2.10）：** `国产1类瓷介电容器`、`国产2类瓷介电容器`、`进口电容器`（铝电解）、`国产金属膜电阻器`（含 `stress_variables` 变量 `Pr=0.25, Vbus=12` 及表达式 `"Vbus*0.01/Pr"`）、`国产片式膜电阻器`、`国产功率线绕电阻器`（变量表达式 `"Pload/Pr_w"`）、`进口电阻器`、`国产电感器`与`国产变压器`（热点温度 `THS`）。
  - **机电与互连（5.11–5.15）：**
    * `国产电连接器`（3 种典型场景）：① 低频实际温升 + `Ⅱ类` 材料上限 `120℃`（表 289 合规值）+ `contacts=22` 向上取档至 `25`（表 292）；② 低频按接触件电流查表 + `Ⅰ类` 材料上限 `200℃` + `contact_current_A=2.5` 向上取档至 `3`（表 288）；③ `射频同轴连接器`（按 `插座` 计量）。
    * `国产机电继电器`、`国产开关`、`国产印制板`（6 层、320 金属化孔）、`国产焊接点`（再流焊 180 点）、`国产SMT互连`（多连接类别取最薄弱最大值）。
  - **工作 / 非工作双状态（`profiles.nonoperating`）：** 全部元器件均配置完整非工作档案（国产器件使用对应附录 B.2 详细模型，双管及进口器件保留非工作经验失效率），切换至 `非工作` 状态可零待配置完整求和。
  - **全 4 项专项分析（`analysis`）：**
    * **任务剖面（附录 A.3.1）：** 引用当前工程工作态（`GF1 @ 55℃`、`AIF @ 70℃`）与非工作态（`GB @ 25℃`）实时联动计算全寿命失效率。
    * **失效率修正（附录 E）：** 基于 `GB @ 25℃` 初始率结合 ESS 筛选与外场数据做贝叶斯更新。
    * **失效模式分解（附录 F）：** 表 `F.1` 线性/运放器件失效模式贡献计算。
    * **失效物理单项（附录 D）：** 共晶焊料热循环寿命（D.2.6 式 D.15/D.16）。

### 2.2 [`02_nonoperating_detailed.json`](02_projects/02_nonoperating_detailed.json)（附录 B.2 非工作详细法）
- **启用工况：** `贮存常温 GB @ 25℃`、`低温贮存 GB @ -10℃`（演示 `-10℃` 向上归入表列 `0～25℃` / `0℃` 低温档）。
- **元器件覆盖：** 涵盖非工作详细 IC（数字/模拟）、二极管、晶体管、电阻、电容、电感、继电器、开关、电连接器、印制板、焊接点及多行 SMT 互连。

### 2.3 [`03_parts_count_early_design.json`](02_projects/03_parts_count_early_design.json)（早期计数法双状态工程）
- **启用工况：** `地面良好计数 GB @ 25℃`、`平稳地面移动 GM1 @ 45℃`。
- **内容：** 混合第 6 章国产工作计数模型（IC、分立器件、电阻、电容、电感、继电器、开关、连接器）与附录 C.3 进口工作计数模型；所有国产项均配对附录 B.3 非工作计数档案，进口项保留非工作已知失效率，支持一键切换工作/非工作完整预计。

### 2.4 [`04_hybrid_and_subcircuits.json`](02_projects/04_hybrid_and_subcircuits.json) + 子工程
- [`sub_mounted_microcircuits.json`](02_projects/sub_mounted_microcircuits.json)：包含带 `gate_count` 与 `junction_temperature`（`Tc=35℃`）的 `5.1.2` 半导体集成电路裸芯片。
- [`sub_lc_filter_circuit.json`](02_projects/sub_lc_filter_circuit.json)：`GB @ 25℃` 与 `GF1 @ 55℃` 两工况完整计算的 LC 组成子电路。
- [`04_hybrid_and_subcircuits.json`](02_projects/04_hybrid_and_subcircuits.json)：通过 SHA-256 快照绑定上述两个子工程，演示 `国产混合集成电路`（表 40 外贴微电路 + 外贴分立/无源数量）、`国产LC滤波器`（组成法详细求和）与 `国产压电陀螺`（5.22 后部电路同工况求和）。

### 2.5 [`05_system_module_integration.json`](02_projects/05_system_module_integration.json)（整机级模块化集成）
- 组合以下 3 类整机预计对象：
  1. `工程模块`：通过 `create_project_module` 引入 [`01_working_stress_comprehensive.json`](02_projects/01_working_stress_comprehensive.json) 的完整快照与 SHA-256 摘要校验。
  2. `已知单件失效率`：外购高压辅助电源单元按 3 种工况分别录入已知失效率。
  3. 机箱级接口：`国产电连接器`（低频及射频同轴）与`国产开关`（旋转式）。

---

## 3. 报告与脱敏交付示例（[`03_reports_and_redacted/`](03_reports_and_redacted/)）

均由 [`01_working_stress_comprehensive.json`](02_projects/01_working_stress_comprehensive.json) 实时导出：
- **完整计算数据工作簿：** [`full_report_sample.xlsx`](03_reports_and_redacted/full_report_sample.xlsx)、[`full_report_sample.xls`](03_reports_and_redacted/full_report_sample.xls)
- **供应商脱敏交付物：** [`redacted_delivery_sample.json`](03_reports_and_redacted/redacted_delivery_sample.json)、[`redacted_delivery_sample.xlsx`](03_reports_and_redacted/redacted_delivery_sample.xlsx)、[`redacted_delivery_sample.xls`](03_reports_and_redacted/redacted_delivery_sample.xls)

---

## 4. 自动化校验方式（CLI）

```bash
# 运行示例套件专项测试
env -u DISPLAY -u WAYLAND_DISPLAY QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_examples.py

# 通过图形界面直接打开综合工程示例
.venv/bin/python main.py example/02_projects/01_working_stress_comprehensive.json
```
