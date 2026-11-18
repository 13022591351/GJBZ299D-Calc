"""Optional BOM pre-mapping wizard; never infers quality grade or electrical stress."""

import re

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import configuration as _configuration
from ..configuration import nominal_parameter
from ..model_registry import NON_COMPONENT_PREFIXES, model_label, recommend
from ..nominal import parse_nominal
from ..prediction import model_state
from ..standards import MODELS
from ..table_layout import configure_table_combo, resizable_columns
from ..theme import section_label


def _is_parameter_value_column(components):
    helper = getattr(_configuration, 'is_parameter_value_column', None)
    if callable(helper) and not helper(components):
        return False
    passive_rows = []
    for comp in components:
        if not comp.bom:
            continue
        prefixes = set(recommend(comp).prefixes)
        if not (prefixes & {'R', 'RN', 'C', 'CN', 'L'}) or (prefixes & NON_COMPONENT_PREFIXES):
            continue
        row = comp.bom.get('row', {})
        val = str(row.get('Value', '') or '').strip()
        if val:
            footprint = str(row.get('Footprint', '') or '').upper()
            passive_rows.append((val, str(row.get('Index', '') or '').strip(), footprint))
    if len(passive_rows) >= 3:
        parsed_count = 0
        explicit_valid_count = 0
        unparseable_count = 0
        bare_rows = []
        for val, idx, footprint in passive_rows:
            bare_mark = re.fullmatch(r'(\d{3,4})[JKM]?', val, re.IGNORECASE)
            is_pkg = bool(
                bare_mark and re.search(rf'(?<!\d){re.escape(bare_mark.group(1))}(?!\d)', footprint)
            )
            ok = (not is_pkg) and any(parse_nominal(val, k) is not None for k in ('R', 'C', 'L'))
            if ok:
                parsed_count += 1
                if not bare_mark:
                    explicit_valid_count += 1
            else:
                unparseable_count += 1
            if re.fullmatch(r'\d+', val):
                bare_rows.append((val, idx))
        if parsed_count * 2 < len(passive_rows):
            return False
        if bare_rows and unparseable_count > 0 and unparseable_count >= explicit_valid_count:
            return False
        if bare_rows and all(idx and val == idx for val, idx in bare_rows):
            return False
    return True


def _extract_nominal(component, model, *, allow_bare_eia=True):
    try:
        return nominal_parameter(component, model, allow_bare_eia=allow_bare_eia)
    except TypeError:
        if not allow_bare_eia and component.bom:
            raw = str(component.bom.get('row', {}).get('Value', '') or '').strip()
            if re.fullmatch(r'\d{3,4}[JKM]?', raw, re.IGNORECASE):
                return None
        return nominal_parameter(component, model)

_SMD_SIZE_RE = re.compile(r'(?<![0-9.])(?:01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9.ΩR])')
_SMD_WORD_RE = re.compile(r'(?:贴片|(?<![A-Z])(?:SMD|SMT|CHIP)(?![A-Z]))')
_RES_ARRAY_RE = re.compile(r'(?<!\d)(?:0201|0402|0603|0805|1206)\s*[X×]\s*[248](?!\d)')
_TANT_WORD_RE = re.compile(r'(?:钽|(?<![A-Z])TANT(?:ALUM)?(?![A-Z]))')
_LED_WORD_RE = re.compile(r'(?:发光|(?<![A-Z])LED(?![A-Z]))')
_OSC_MARKER_RE = re.compile(
    r'(?:有源|振荡|(?<![A-Z])(?:OSC|XO|TCXO|VCXO|OCXO|DCXO|CMOS|LVDS|LVPECL)(?![A-Z])'
    r'|(?<![0-9.])(?:1\.2|1\.8|2\.5|3\.3|5(?:\.0)?)\s*V(?![A-Z0-9]))'
)
_REF_IC_RE = re.compile(r'(?:TL|AZ|LM|KA|ATL)431|LM4040|LM385|LT1009|REF[235]\d')
_BEAD_IMPEDANCE_RE = re.compile(r'\d+\s*[RΩ]\s*@\s*\d+\s*MHZ')


def suggest_model(component, *, domestic=True, prediction_state='working'):
    """Recommend a physical device model from BOM tokens alone; never guess stress or quality."""
    row = component.bom.get('row', {}) if component.bom else {}
    footprint = str(row.get('Footprint', '') or '').upper()
    desc = str(row.get('Description', '') or '').upper()
    value = str(row.get('Value', '') or '').upper()
    meta = f'{footprint} {desc}'
    blob = f'{footprint} {desc} {value}'
    prefixes = set(recommend(component).prefixes)
    if prefixes & NON_COMPONENT_PREFIXES:
        return '待配置', '非器件位号'

    is_smd = bool(_SMD_SIZE_RE.search(meta) or _SMD_WORD_RE.search(meta))
    candidate = '待配置'
    reason = '需人工确认细分器件类型'

    def _pick(dom_work, imp_work, dom_nonop, desc_text):
        if prediction_state == 'nonoperating':
            if not domestic or not dom_nonop:
                return '待配置', '非工作详细模型需人工核定'
            return dom_nonop, desc_text
        chosen = dom_work if domestic else imp_work
        return (chosen or '待配置'), desc_text

    if prefixes & {'R', 'RN'} and not prefixes & {'RT', 'RP'}:
        if ('RN' in prefixes
                or any(tok in blob for tok in ('排阻', '电阻网络', 'RES ARRAY', 'RESISTOR NETWORK', '4P2R', '8P4R', '2P1R'))
                or _RES_ARRAY_RE.search(meta)):
            candidate, reason = _pick('国产电阻网络', '进口电阻网络', '国产电阻网络（非工作详细）', '位号 RN / 电阻网络关键字')
        elif any(tok in blob for tok in ('合金', '锰铜', '康铜', '采样', '分流', 'SHUNT', 'CURRENT SENSE')):
            candidate, reason = '待配置', '合金/采样电阻需人工确认计算模型'
        elif '箔' in blob or 'FOIL' in blob:
            candidate, reason = _pick('国产箔式电阻器', None, '国产电阻器（非工作详细）', '位号 R + 箔式关键字')
        elif '功率线绕' in blob:
            candidate, reason = _pick('国产功率线绕电阻器', '进口电阻器', '国产电阻器（非工作详细）', '位号 R + 功率线绕关键字')
        elif '线绕' in blob or 'WIREWOUND' in blob:
            candidate, reason = _pick('国产线绕电阻器', '进口电阻器', '国产电阻器（非工作详细）', '位号 R + 线绕关键字')
        elif '玻璃釉' in blob or 'GLAZE' in blob:
            candidate, reason = _pick('国产玻璃釉电阻器', None, '国产电阻器（非工作详细）', '位号 R + 玻璃釉关键字')
        elif '碳膜' in blob or 'CARBON FILM' in blob:
            candidate, reason = _pick('国产碳膜电阻器', '进口电阻器', '国产电阻器（非工作详细）', '位号 R + 碳膜关键字')
        elif ('金属膜' in blob or 'METAL FILM' in blob) and not is_smd:
            candidate, reason = _pick('国产金属膜电阻器', '进口电阻器', '国产电阻器（非工作详细）', '位号 R + 引线金属膜关键字')
        elif is_smd:
            candidate, reason = _pick('国产片式膜电阻器', '进口电阻器', '国产电阻器（非工作详细）', '位号 R + 贴片封装关键字')
    elif 'C' in prefixes or ('CN' in prefixes and any(tok in blob for tok in ('排容', '电容网络', 'CAP ARRAY', 'CAPACITOR NETWORK', '4P2C', '8P4C'))):
        if any(tok in blob for tok in ('排容', '电容网络', 'CAP ARRAY', 'CAPACITOR NETWORK', '4P2C', '8P4C')):
            candidate, reason = _pick('国产电容网络', '进口电容网络', '国产电容网络（非工作详细）', '电容网络关键字')
        elif any(tok in blob for tok in ('X7R', 'X5R', 'X7S', 'X6S', 'X8R', 'Y5V', 'Z5U', '2类', 'II类', 'Ⅱ类')):
            candidate, reason = _pick('国产2类瓷介电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + 2类瓷介介质')
        elif any(tok in blob for tok in ('NP0', 'C0G', 'NPO', '1类')) or re.search(r'(?<![IⅠA-Z])(?:I类|Ⅰ类)', blob):
            candidate, reason = _pick('国产1类瓷介电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + NP0/C0G/1类介质')
        elif any(tok in blob for tok in ('MLCC', '瓷介', '陶瓷电容')):
            candidate, reason = _pick(None, '进口电容器', None, '位号 C + 瓷介关键字（需人工区分1/2类）')
        elif any(tok in blob for tok in ('非固体钽', '液体钽', 'WET TANT', 'CAK35')):
            candidate, reason = _pick('国产非固体钽电解电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + 非固体钽关键字')
        elif any(tok in blob for tok in ('氧化铌', '铌电解', 'NIOBIUM')):
            candidate, reason = _pick('国产氧化铌电解电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + 氧化铌关键字')
        elif any(tok in blob for tok in ('固体钽', 'CAK45')) or _TANT_WORD_RE.search(blob):
            candidate, reason = _pick('国产固体钽电解电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + 固体钽关键字')
        elif any(tok in blob for tok in ('铝电解', '铝电容', 'ALUM')) or ('铝' in blob and '电解' in blob):
            candidate, reason = _pick('国产铝电解电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + 铝电解关键字')
        elif '云母' in blob or 'MICA' in blob:
            candidate, reason = _pick('国产云母电容器', '进口电容器', '国产电容器（非工作详细）', '位号 C + 云母关键字')
    elif 'FB' in prefixes or any(tok in blob for tok in ('磁珠', 'BEAD', 'FERRITE')) or _BEAD_IMPEDANCE_RE.search(blob):
        candidate, reason = '待配置', '磁珠器件需人工确认计算模型'
    elif 'L' in prefixes:
        if '变压器' in blob or 'TRANSFORMER' in blob:
            candidate, reason = _pick('国产变压器', '进口变压器', '国产变压器（非工作详细）', '位号 L + 变压器关键字')
        elif is_smd:
            candidate, reason = _pick('国产片式电感器', '进口电感器', '国产片式电感器（非工作详细）', '位号 L + 贴片封装')
        else:
            candidate, reason = _pick('国产电感器', '进口电感器', '国产电感器（非工作详细）', '位号 L 感性元件')
    elif 'T' in prefixes and not any(tok in blob for tok in ('晶体管', 'MOSFET', 'BJT')):
        candidate, reason = _pick('国产变压器', '进口变压器', '国产变压器（非工作详细）', '位号 T 变压器关键字')
    elif prefixes & {'Y', 'X', 'XTAL', 'G'} and not any(tok in blob for tok in ('端子', '插座', 'HEADER', 'CONN', 'SOCKET', 'TERMINAL', '电池', 'BAT')):
        if _OSC_MARKER_RE.search(blob):
            candidate, reason = _pick('国产振荡器', '进口振荡器和谐振器', '国产振荡器（非工作详细）', '振荡器关键字')
        elif any(tok in blob for tok in ('无源', '谐振', 'XTAL', 'CRYSTAL', 'RESONATOR', 'HC-49', 'HC49')) or ('晶' in blob and 'PF' in blob):
            candidate, reason = _pick('国产谐振器', '进口振荡器和谐振器', '国产谐振器（非工作详细）', '无源谐振器关键字')
    elif 'U' in prefixes and any(tok in blob for tok in ('光耦', '光电耦合', 'OPTOCOUPLER', 'PHOTOCOUPLER')):
        candidate, reason = _pick('国产光电子器件', '进口光电子器件', '国产光电子器件（非工作详细）', '光耦关键字')
    elif prefixes & {'D', 'VD', 'VZ', 'ZD', 'DZ', 'LED', 'PC', 'OC'}:
        if prefixes & {'PC', 'OC'} or any(tok in blob for tok in ('光耦', '光电耦合', 'OPTOCOUPLER', 'PHOTOCOUPLER')):
            candidate, reason = _pick('国产光电子器件', '进口光电子器件', '国产光电子器件（非工作详细）', '光耦关键字')
        elif 'LED' in prefixes or _LED_WORD_RE.search(blob):
            candidate, reason = _pick('国产光电子器件', '进口光电子器件', '国产光电子器件（非工作详细）', 'LED 关键字')
        elif _REF_IC_RE.search(blob):
            candidate, reason = '待配置', '基准源芯片需人工确认计算模型'
        elif prefixes & {'VZ', 'ZD', 'DZ'} or any(tok in blob for tok in ('稳压', '齐纳', 'ZENER')):
            candidate, reason = _pick('国产电压调整、电压基准及电流调整二极管', '进口其他分立半导体', '国产二极管（非工作详细）', '稳压二极管关键字')
        elif any(tok in blob for tok in (
            '整流', 'RECTIFIER', '开关二极管', 'SWITCHING DIODE', '检波', '肖特基', 'SCHOTTKY', 'TVS', 'ESD', '瞬态抑制',
        )):
            candidate, reason = _pick('国产普通二极管', '进口普通二极管', '国产二极管（非工作详细）', '普通二极管关键字')

    if candidate not in MODELS or model_state(candidate) != prediction_state:
        return '待配置', '需人工确认计算模型'
    return candidate, reason


class BomPreMapDialog(QDialog):
    """Cluster newly imported BOM rows and let the user accept or skip pre-mapped models."""

    def __init__(self, components, indices, prediction_state='working', parent=None):
        super().__init__(parent)
        self.setWindowTitle('BOM 导入智能预映射向导（仅辅助选型）')
        self.resize(980, 560)
        self.components = components
        self.indices = list(indices)
        self.prediction_state = prediction_state
        self.clusters = []
        self._controls = []

        self._build_clusters(indices)

        layout = QVBoxLayout(self)
        layout.addWidget(section_label('BOM 智能预映射建议', 'Title'))
        layout.addWidget(section_label(
            '按位号前缀、封装和描述推荐计算模型并提取标称阻/容值；应用后请在主表中补充质量等级与工作应力。'
        ))

        self.table = QTableWidget(len(self.clusters), 4)
        self.table.setHorizontalHeaderLabels(['应用', '位号样本（数量）', 'BOM 封装 / 描述 / 标称值', '推荐失效率计算模型'])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        resizable_columns(
            self.table,
            'bom-premap',
            [62, 200, 380, 260],
            min_widths=[62, 140, 220, 210],
            fixed_columns=(0,),
        )

        valid_models = ['待配置'] + [
            name for name in MODELS
            if name not in ('待配置', '工程模块') and model_state(name) == prediction_state
        ]

        self.table.setUpdatesEnabled(False)
        try:
            for row_idx, cluster in enumerate(self.clusters):
                sample = cluster['sample']
                bom_row = sample.bom.get('row', {})
                suggested = cluster['suggested']

                check = QCheckBox()
                check.setChecked(suggested != '待配置')
                self.table.setCellWidget(row_idx, 0, check)

                names = ', '.join(self.components[i].name for i in cluster['indices'][:3])
                if len(cluster['indices']) > 3:
                    names += f' 等 {len(cluster["indices"])} 项'
                self.table.setItem(row_idx, 1, QTableWidgetItem(names))

                detail = ' | '.join(filter(None, (
                    bom_row.get('Footprint', ''),
                    bom_row.get('Value', ''),
                    bom_row.get('Description', ''),
                    cluster['reason'],
                )))
                self.table.setItem(row_idx, 2, QTableWidgetItem(detail))

                combo = QComboBox()
                for name in valid_models:
                    prefix = ('国产' if name.startswith('国产') else '进口' if name.startswith('进口') else '')
                    display = f'{prefix}{model_label(name)}' if prefix else name
                    combo.addItem(display, name)
                idx = combo.findData(suggested)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                configure_table_combo(combo, min_chars=8)
                combo.currentIndexChanged.connect(lambda _i, chk=check, cmb=combo: chk.setChecked(cmb.currentData() != '待配置'))
                self.table.setCellWidget(row_idx, 3, combo)
                self._controls.append((check, combo, cluster))
        finally:
            self.table.setUpdatesEnabled(True)

        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox()
        apply_btn = buttons.addButton('应用所选模型', QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn.setObjectName('Primary')
        buttons.addButton('跳过，稍后手动配置', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_clusters(self, indices):
        grouped = {}
        for idx in indices:
            comp = self.components[idx]
            if comp.model != '待配置':
                continue
            suggested, reason = suggest_model(comp, domestic=comp.is_domestic, prediction_state=self.prediction_state)
            bom_row = comp.bom.get('row', {}) if comp.bom else {}
            key = (recommend(comp).prefixes, bom_row.get('Footprint', ''), suggested)
            entry = grouped.setdefault(key, {
                'indices': [],
                'sample': comp,
                'suggested': suggested,
                'reason': reason,
            })
            entry['indices'].append(idx)
        self.clusters = list(grouped.values())

    def has_suggestions(self):
        return any(c['suggested'] != '待配置' for c in self.clusters)

    def apply_to(self, components):
        """Apply chosen model + parsed BOM nominal parameter without inventing stress/quality."""
        scope = [components[i] for i in self.indices if 0 <= i < len(components)]
        allow_bare_eia = _is_parameter_value_column(scope or components)
        applied_ids = []
        for check, combo, cluster in self._controls:
            if not check.isChecked():
                continue
            chosen = combo.currentData()
            if not chosen or chosen == '待配置':
                continue
            for idx in cluster['indices']:
                comp = components[idx]
                comp.model = chosen
                comp.rate_state = self.prediction_state
                if chosen.startswith('国产'):
                    comp.domestic = True
                elif chosen.startswith('进口'):
                    comp.domestic = False
                nominal = _extract_nominal(comp, chosen, allow_bare_eia=allow_bare_eia)
                if nominal and nominal[0] not in comp.parameters:
                    comp.parameters[nominal[0]] = nominal[1]
                comp.validate()
                applied_ids.append(comp.identifier)
        return applied_ids
