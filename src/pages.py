"""Software scope, calculation conventions, and offline data summary."""

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from . import APP_VERSION
from .standards import AUTOMATIC_MODELS
from .theme import section_label as label
from .theme import text_browser

COVERAGE = """
<h2>数据与使用说明</h2>
<p>软件版本：{version}　｜　依据标准：GJB/Z 299D—2024《电子设备可靠性预计手册》。共内置 {model_count} 个自动计算模型与完整系数数据表。</p>
<h3>开发者与开源信息</h3>
<p>开发者：DuoYuWang<br>联系邮箱：thirteenking.wang@gmail.com<br>开源地址：https://github.com/13022591351/GJBZ299D-Calc<br>许可协议：MIT License（Copyright © 2026 DuoYuWang）<br>辅助开发 LLM：GPT6-Astra、Gemini 4 Carbon</p>
<h3>离线承诺（软件使用全流程）</h3>
<p>本软件运行全流程离线：BOM 导入、参数配置、标准查询、可靠性预计计算、工程保存与读取、Excel 导出均在本机完成，标准数据随软件提供。</p>
<p>软件不建立互联网连接，不上传 BOM、工程文件、元器件参数、工作环境或计算结果；不含遥测、在线激活、远程验证和自动更新功能。文件按用户选择的路径读写，不发送至开发者或第三方服务器。</p>
<h3>预计方法与支持范围</h3>
<ul>
<li>工作状态：第 5 章及附录 C.2 应力分析法、第 6 章及附录 C.3 元器件计数法。</li>
<li>非工作状态：附录 B.2 详细预计法、附录 B.3 元器件计数法；支持工作/非工作参数双向复用与独立维护。</li>
<li>专项分析：附录 A 全寿命任务剖面、附录 E 经验失效率修正、附录 F 失效模式贡献分解、附录 D 失效物理单项计算。</li>
<li>外部来源：支持已知单件失效率及供应商整份工程快照模块，兼容等效门换算、结温计算与外贴微电路。</li>
</ul>
<h3>单位与应力表达式</h3>
<ul>
<li>失效率单位：10⁻⁶/h（1 FIT = 0.001 × 10⁻⁶/h）；MTBF = 10⁶ / λ总。</li>
<li>电应力比：支持数值、四则运算、科学计数法、括号及自定义变量（如 28/100、(U*I/1000)/Pr），计算时向上取至标准档位。</li>
<li>温度取值与查表：除工程环境温度外，微电路、分立器件、感性元件、电阻网络、低频连接器等模型分别提供结温/沟道温度（支持热阻与功耗辅助计算）、壳温、热点温度或温升输入框；温度查表时若介于标准表列温度点之间或低于起始温度档，自动向上取整至下一高温度档（超过温度表上限仍拒算提示）。</li>
<li>BOM 标称值：支持阻值（mR/R/k/M）、电容（pF/nF/uF）及电感（uH/nH）缩写与数码识别，并自动转换为计算单位。</li>
</ul>
<h3>文件打开、报告导出与脱敏互通</h3>
<ul>
<li>支持向主窗口拖入单个文件直接打开 .json / 工作簿工程或导入 BOM 表格。</li>
<li>支持 .xlsx 与 .xls 多工况汇总导出：当前导出范围覆盖首页“预计总览”与各启用工况明细表（如“工况 1_GB@25°C”）；“全寿命状态可靠性预计”等专项分析在软件界面内交互计算并随 .json 工程文件保存，暂不随 Excel 导出。</li>
<li>脱敏工程导出隐藏元器件位号、原型号、内部标称参数与原始应力公式，正常保留计算模型、标准参数档位及各工况预计结果。</li>
</ul>
"""


def coverage_page():
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(6, 5, 6, 5)
    layout.setSpacing(4)
    layout.addWidget(label("数据与使用说明", "Title"))
    browser = text_browser()
    browser.setOpenLinks(False)
    browser.document().setDocumentMargin(10)
    browser.setHtml(COVERAGE.replace('{model_count}', str(len(AUTOMATIC_MODELS))).replace('{version}', APP_VERSION))
    layout.addWidget(browser, 1)
    return page
