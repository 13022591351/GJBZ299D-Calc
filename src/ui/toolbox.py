"""Engineering toolbox tabs: dual-transistor ratio, Tj estimator, gate count, and conversions."""

import math

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..domain import dual_transistor
from ..input_sources import gate_count, junction_temperature, thermal_packages
from ..numeric import formatted_number as number
from ..numeric import number_text
from ..standards import ENVIRONMENTS, strip_cell_ids
from ..theme import section_label


def _parse_float(field):
    text = field.text().replace(',', '').strip()
    try:
        value = float(text)
    except ValueError as error:
        raise ValueError('请填写有效的有限数值') from error
    if not math.isfinite(value):
        raise ValueError('请填写有效的有限数值')
    return value


def _display_num(value):
    return number(round(value, 8) if abs(value) >= 1e-4 else value)


def _tab_container(title, formula_text, description):
    """Build a crisp framed card container for one toolbox calculator tab."""
    tab = QWidget()
    outer = QVBoxLayout(tab)
    outer.setContentsMargins(8, 8, 8, 8)

    card = QFrame()
    card.setObjectName("Card")
    card.setMaximumWidth(860)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    layout.addWidget(section_label(title, "Section"))
    if formula_text:
        layout.addWidget(section_label(formula_text, "Formula"))
    layout.addWidget(section_label(description))

    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    form.setSpacing(6)
    layout.addLayout(form)

    outer.addWidget(card)
    outer.addStretch()
    return tab, layout, form


def _action_row(primary_text, on_calc, on_reset):
    row = QHBoxLayout()
    calc_btn = QPushButton(primary_text)
    calc_btn.setObjectName("Primary")
    calc_btn.setMinimumWidth(110)
    calc_btn.clicked.connect(on_calc)
    reset_btn = QPushButton("恢复示例值")
    reset_btn.clicked.connect(on_reset)
    row.addWidget(calc_btn)
    row.addWidget(reset_btn)
    row.addStretch()
    return row, calc_btn, reset_btn


class ToolboxPage(QWidget):
    """Unified engineering toolbox replacing the single dual-transistor stress page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 5, 6, 5)
        root.setSpacing(4)
        header = QHBoxLayout()
        header.addWidget(section_label("工程工具箱", "Title", wrap=False))
        header.addSpacing(12)
        header.addWidget(section_label("常用辅助计算工具，均按 GJB/Z 299D—2024 条款与物理定义直接计算。"), 1)
        root.addLayout(header)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self._updating_conv = False
        self._conv_source = "lambda"
        self.dual_tab = self._build_dual_tab()
        self.tj_tab = self._build_tj_tab()
        self.gate_tab = self._build_gate_tab()
        self.convert_tab = self._build_convert_tab()

        self.tabs.addTab(self.dual_tab, "双晶体管应力比 (式24)")
        self.tabs.addTab(self.tj_tab, "微电路结温 (式2)")
        self.tabs.addTab(self.gate_tab, "等效门数换算 (5.1.1.3)")
        self.tabs.addTab(self.convert_tab, "失效率与MTBF换算")

    def _build_dual_tab(self):
        tab, layout, form = _tab_container(
            "双晶体管应力比",
            "S = [P1 / Ps + P2 × (2 × Ps − PT) / (PT × Ps)] × C",
            "按标准式（24）（第 69 页 / PDF 第 74 页）计算。结果大于 1 时需核查额定功率与使用工况。",
        )
        self.dual_fields = {}
        defaults = (
            ("p1", "P1 · 第一管耗散功率（W）", "1"),
            ("ps", "Ps · 单管额定功率（W）", "10"),
            ("p2", "P2 · 第二管耗散功率（W）", "1"),
            ("pt", "PT · 双管总额定功率（W）", "15"),
            ("c", "C · 调整系数", "1"),
        )
        for key, title, value in defaults:
            field = QLineEdit(value)
            field.setMaximumWidth(400)
            field.textChanged.connect(lambda: self.dual_result.setText("参数已修改，请点击“计算应力比”。"))
            self.dual_fields[key] = field
            form.addRow(title, field)

        def _reset_dual():
            for k, _t, v in defaults:
                self.dual_fields[k].setText(v)
            self.dual_result.setText("已恢复示例值，请点击“计算应力比”。")

        row, self.dual_calc_btn, _reset_btn = _action_row("计算应力比", self.calculate_dual, _reset_dual)
        layout.addLayout(row)
        self.dual_result = section_label("填写参数后点击“计算应力比”。", "Formula")
        layout.addWidget(self.dual_result)
        return tab

    def _build_tj_tab(self):
        tab, layout, form = _tab_container(
            "微电路结温估算",
            "Tj = Tc + θJC × P",
            "按标准式（2）结合表 24（典型管壳温度）与表 25（典型热阻）计算；进口器件需填入实测 Tc 与 θJC。",
        )
        self.tj_form = form

        def _mark_tj_dirty():
            self.tj_result.setText("参数已修改，请点击“计算结温”。")

        self.tj_env = QComboBox()
        self.tj_env.setMaximumWidth(400)
        for code, name in ENVIRONMENTS.items():
            self.tj_env.addItem(f"{code} · {name}", code)
        self.tj_env.currentIndexChanged.connect(_mark_tj_dirty)
        form.addRow("环境类别", self.tj_env)

        self.tj_case_source = QComboBox()
        self.tj_case_source.setMaximumWidth(400)
        self.tj_case_source.addItem("按环境查表 24（国产微电路）", "table24")
        self.tj_case_source.addItem("手动指定管壳温度 Tc", "specified")
        self.tj_case_source.currentIndexChanged.connect(self._on_tj_mode_changed)
        form.addRow("管壳温度来源", self.tj_case_source)

        self.tj_case_edit = QLineEdit("65")
        self.tj_case_edit.setMaximumWidth(400)
        self.tj_case_edit.textChanged.connect(_mark_tj_dirty)
        form.addRow("管壳温度 Tc（℃）", self.tj_case_edit)

        self.tj_res_source = QComboBox()
        self.tj_res_source.setMaximumWidth(400)
        self.tj_res_source.addItem("按封装查表 25（典型热阻）", "table25")
        self.tj_res_source.addItem("手动指定结到管壳热阻 θJC", "specified")
        self.tj_res_source.currentIndexChanged.connect(self._on_tj_mode_changed)
        form.addRow("热阻来源", self.tj_res_source)

        self.tj_package = QComboBox()
        self.tj_package.setMaximumWidth(400)
        for pkg in sorted(thermal_packages()):
            self.tj_package.addItem(pkg, pkg)
        self.tj_package.currentIndexChanged.connect(_mark_tj_dirty)
        form.addRow("封装形式（表 25）", self.tj_package)

        self.tj_res_edit = QLineEdit("40")
        self.tj_res_edit.setMaximumWidth(400)
        self.tj_res_edit.textChanged.connect(_mark_tj_dirty)
        form.addRow("结到管壳热阻 θJC（℃/W）", self.tj_res_edit)

        self.tj_power = QLineEdit("0.5")
        self.tj_power.setMaximumWidth(400)
        self.tj_power.textChanged.connect(_mark_tj_dirty)
        form.addRow("实际耗散功率 P（W）", self.tj_power)

        def _reset_tj():
            self.tj_env.setCurrentIndex(0)
            self.tj_case_source.setCurrentIndex(0)
            self.tj_case_edit.setText("65")
            self.tj_res_source.setCurrentIndex(0)
            self.tj_res_edit.setText("40")
            self.tj_power.setText("0.5")
            self._sync_tj_inputs()
            self.tj_result.setText("已恢复示例值，请点击“计算结温”。")

        row, self.tj_calc_btn, _reset_btn = _action_row("计算结温", self.calculate_tj, _reset_tj)
        layout.addLayout(row)
        self.tj_result = section_label("填写参数后点击“计算结温”。", "Formula")
        layout.addWidget(self.tj_result)
        self._sync_tj_inputs()
        return tab

    def _build_gate_tab(self):
        tab, layout, form = _tab_container(
            "数字集成电路等效门数换算",
            "双极型 / 其他MOS：NG = Nt / 3  ·  CMOS：NG = Nt / 3.75  ·  触发器：NG = 8×N(J-K/R-S) + 6×N(T/D)",
            "按条款 5.1.1.3 按晶体管数或触发器数换算等效门数。",
        )
        self.gate_form = form

        def _mark_gate_dirty():
            self.gate_result.setText("参数已修改，请点击“计算等效门数”。")

        self.gate_mode = QComboBox()
        self.gate_mode.setMaximumWidth(400)
        self.gate_mode.addItem("按晶体管数量换算", "transistors")
        self.gate_mode.addItem("按触发器数量换算", "flip_flops")
        self.gate_mode.currentIndexChanged.connect(self._on_gate_mode_changed)
        form.addRow("换算方式", self.gate_mode)

        self.gate_tech = QComboBox()
        self.gate_tech.setMaximumWidth(400)
        for tech in ("双极型", "CMOS", "其他MOS"):
            self.gate_tech.addItem(tech, tech)
        self.gate_tech.currentIndexChanged.connect(_mark_gate_dirty)
        form.addRow("电路工艺", self.gate_tech)

        self.gate_transistors = QLineEdit("120")
        self.gate_transistors.setMaximumWidth(400)
        self.gate_transistors.textChanged.connect(_mark_gate_dirty)
        form.addRow("晶体管总数 Nt", self.gate_transistors)

        self.gate_jk_rs = QLineEdit("0")
        self.gate_jk_rs.setMaximumWidth(400)
        self.gate_jk_rs.textChanged.connect(_mark_gate_dirty)
        form.addRow("J-K / R-S 触发器个数", self.gate_jk_rs)

        self.gate_t_d = QLineEdit("8")
        self.gate_t_d.setMaximumWidth(400)
        self.gate_t_d.textChanged.connect(_mark_gate_dirty)
        form.addRow("T / D 触发器个数", self.gate_t_d)

        def _reset_gates():
            self.gate_mode.setCurrentIndex(0)
            self.gate_tech.setCurrentIndex(0)
            self.gate_transistors.setText("120")
            self.gate_jk_rs.setText("0")
            self.gate_t_d.setText("8")
            self._sync_gate_inputs()
            self.gate_result.setText("已恢复示例值，请点击“计算等效门数”。")

        row, self.gate_calc_btn, _reset_btn = _action_row("计算等效门数", self.calculate_gates, _reset_gates)
        layout.addLayout(row)
        self.gate_result = section_label("填写参数后点击“计算等效门数”。", "Formula")
        layout.addWidget(self.gate_result)
        self._sync_gate_inputs()
        return tab

    def _build_convert_tab(self):
        tab, layout, form = _tab_container(
            "失效率 · FIT · MTBF · 可靠度换算",
            "1 FIT = 10⁻⁹/h = 0.001 × 10⁻⁶/h  ·  MTBF = 10⁶ / λ  ·  R(t) = exp(-λ × 10⁻⁶ × t)",
            "填写 λ、FIT 或 MTBF 中的任意一项及任务时间 t，点击“开始换算”同步计算其余指标与可靠度。",
        )

        self.conv_lambda = QLineEdit("1")
        self.conv_lambda.setMaximumWidth(400)
        self.conv_lambda.textChanged.connect(lambda: self._on_conv_edit("lambda"))
        form.addRow("失效率 λ（10⁻⁶/h）", self.conv_lambda)

        self.conv_fit = QLineEdit("1000")
        self.conv_fit.setMaximumWidth(400)
        self.conv_fit.textChanged.connect(lambda: self._on_conv_edit("fit"))
        form.addRow("FIT（10⁻⁹/h）", self.conv_fit)

        self.conv_mtbf = QLineEdit("1000000")
        self.conv_mtbf.setMaximumWidth(400)
        self.conv_mtbf.textChanged.connect(lambda: self._on_conv_edit("mtbf"))
        form.addRow("平均无故障时间 MTBF（h）", self.conv_mtbf)

        self.conv_hours = QLineEdit("1000")
        self.conv_hours.setMaximumWidth(400)
        self.conv_hours.textChanged.connect(lambda: self._on_conv_hours_edit())
        form.addRow("任务工作时间 t（h）", self.conv_hours)

        def _reset_conv():
            self._updating_conv = True
            try:
                self._conv_source = "lambda"
                self.conv_lambda.setText("1")
                self.conv_fit.setText("1000")
                self.conv_mtbf.setText("1000000")
                self.conv_hours.setText("1000")
            finally:
                self._updating_conv = False
            self.conv_result.setText("已恢复示例值，请点击“开始换算”。")

        row, self.conv_calc_btn, _reset_btn = _action_row("开始换算", self.calculate_conversion, _reset_conv)
        layout.addLayout(row)
        self.conv_result = section_label("填写参数后点击“开始换算”。", "Formula")
        layout.addWidget(self.conv_result)
        return tab

    def _on_tj_mode_changed(self):
        self._sync_tj_inputs()
        self.tj_result.setText("参数已修改，请点击“计算结温”。")

    def _on_gate_mode_changed(self):
        self._sync_gate_inputs()
        self.gate_result.setText("参数已修改，请点击“计算等效门数”。")

    def _sync_tj_inputs(self):
        specified_case = self.tj_case_source.currentData() == "specified"
        res_mode = self.tj_res_source.currentData()
        self.tj_case_edit.setEnabled(specified_case)
        self.tj_form.setRowVisible(self.tj_case_edit, specified_case)
        self.tj_package.setEnabled(res_mode == "table25")
        self.tj_form.setRowVisible(self.tj_package, res_mode == "table25")
        self.tj_res_edit.setEnabled(res_mode == "specified")
        self.tj_form.setRowVisible(self.tj_res_edit, res_mode == "specified")

    def _sync_gate_inputs(self):
        by_transistors = self.gate_mode.currentData() == "transistors"
        self.gate_tech.setEnabled(by_transistors)
        self.gate_transistors.setEnabled(by_transistors)
        self.gate_jk_rs.setEnabled(not by_transistors)
        self.gate_t_d.setEnabled(not by_transistors)
        self.gate_form.setRowVisible(self.gate_tech, by_transistors)
        self.gate_form.setRowVisible(self.gate_transistors, by_transistors)
        self.gate_form.setRowVisible(self.gate_jk_rs, not by_transistors)
        self.gate_form.setRowVisible(self.gate_t_d, not by_transistors)

    def calculate_dual(self):
        try:
            values = {k: _parse_float(field) for k, field in self.dual_fields.items()}
            result = dual_transistor(**values)
            suffix = "  ·  超过 1，请核查工况" if result > 1 else ""
            self.dual_result.setText(f"应力比 S = {_display_num(result)}{suffix}")
        except (ValueError, OverflowError) as error:
            self.dual_result.setText(f"无法计算：{error}")

    def calculate_tj(self):
        try:
            env = self.tj_env.currentData() or "GB"
            case_source = self.tj_case_source.currentData()
            res_source = self.tj_res_source.currentData()
            inputs = {
                "case_source": case_source,
                "resistance_source": res_source,
                "power_W": _parse_float(self.tj_power),
            }
            if case_source == "specified":
                inputs["case_temperature_C"] = _parse_float(self.tj_case_edit)
            if res_source == "table25":
                inputs["package"] = self.tj_package.currentData()
            else:
                inputs["thermal_resistance_C_W"] = _parse_float(self.tj_res_edit)
            value, trace = junction_temperature(inputs, env)
            self.tj_result.setText(f"结温 Tj = {_display_num(value)} ℃\n依据：{strip_cell_ids(trace)}")
        except (ValueError, OverflowError) as error:
            self.tj_result.setText(f"无法计算：{error}")

    def calculate_gates(self):
        try:
            mode = self.gate_mode.currentData()
            if mode == "transistors":
                inputs = {
                    "mode": "transistors",
                    "technology": self.gate_tech.currentData(),
                    "transistors": _parse_float(self.gate_transistors),
                }
            else:
                inputs = {
                    "mode": "flip_flops",
                    "jk_rs": _parse_float(self.gate_jk_rs),
                    "t_d": _parse_float(self.gate_t_d),
                }
            value, trace = gate_count(inputs)
            self.gate_result.setText(f"等效门数 NG = {_display_num(value)}\n依据：{trace}")
        except (ValueError, OverflowError) as error:
            self.gate_result.setText(f"无法计算：{error}")

    def _on_conv_edit(self, source):
        if self._updating_conv:
            return
        self._conv_source = source
        self.conv_result.setText("参数已修改，请点击“开始换算”。")

    def _on_conv_hours_edit(self):
        if self._updating_conv:
            return
        self.conv_result.setText("参数已修改，请点击“开始换算”。")

    def calculate_conversion(self):
        if self._updating_conv:
            return
        self._updating_conv = True
        try:
            source = self._conv_source
            if source == "lambda":
                lam = _parse_float(self.conv_lambda)
                if not math.isfinite(lam) or lam < 0:
                    raise ValueError("失效率须为非负有限数")
                self.conv_fit.setText(number_text(lam * 1000.0))
                self.conv_mtbf.setText("∞" if lam == 0 else number_text(1e6 / lam))
            elif source == "fit":
                fit = _parse_float(self.conv_fit)
                if not math.isfinite(fit) or fit < 0:
                    raise ValueError("FIT 须为非负有限数")
                lam = fit / 1000.0
                self.conv_lambda.setText(number_text(lam))
                self.conv_mtbf.setText("∞" if lam == 0 else number_text(1e6 / lam))
            else:
                mtbf = _parse_float(self.conv_mtbf)
                if not math.isfinite(mtbf) or mtbf <= 0:
                    raise ValueError("MTBF 须为大于 0 的有限数")
                lam = 1e6 / mtbf
                self.conv_lambda.setText(number_text(lam))
                self.conv_fit.setText(number_text(lam * 1000.0))
            hours = _parse_float(self.conv_hours)
            if not math.isfinite(hours) or hours < 0:
                raise ValueError("失效率与时间均须为非负有限数")
            r_t = math.exp(-lam * 1e-6 * hours)
            f_t = 1.0 - r_t
            mtbf_text = "∞" if lam == 0 else f"{_display_num(1e6 / lam)} h"
            self.conv_result.setText(
                f"MTBF = {mtbf_text}  ·  R({number(hours)}h) = {r_t:.6%}"
                f"  ·  累积失效概率 F(t) = {f_t:.6%}"
            )
        except (ValueError, OverflowError) as error:
            self.conv_result.setText(f"无法计算：{error}")
        finally:
            self._updating_conv = False
