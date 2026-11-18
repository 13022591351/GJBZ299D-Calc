"""Standard chapter taxonomy and non-authoritative BOM recommendations."""

import re
from dataclasses import dataclass
from functools import lru_cache

from .standards import MODELS, model_name

# Chapter 5 taxonomy transcribed from the user's PDF contents, physical page 2.
CATEGORIES = {
    'IC': ('微电路', '5.1', 17), 'SAW': ('声表面波器件', '5.2', 38),
    'D': ('二极管', '5.3', 39), 'Q': ('晶体管', '5.3', 39),
    'LED': ('光电子器件', '5.4', 70), 'V': ('真空电子器件', '5.5', 73),
    'R': ('电阻器', '5.6', 78), 'RT': ('敏感电阻器', '5.7', 96),
    'RP': ('电位器', '5.8', 98), 'C': ('电容器', '5.9', 112),
    'L': ('感性元件', '5.10', 136), 'K': ('继电器', '5.11', 142),
    'S': ('开关', '5.12', 152), 'J': ('电连接器', '5.13', 154),
    'M': ('微特电机', '5.14', 159), 'PCB': ('印制板、焊点与SMT互连', '5.15', 161),
    'MAG': ('磁性器件', '5.16', 165), 'Y': ('振荡器和谐振器', '5.17', 166),
    'FL': ('滤波器', '5.18', 168), 'BT': ('电池', '5.19', 169),
    'HL': ('灯', '5.20', 170), 'LASER': ('激光器', '5.21', 171),
    'GYRO': ('压电陀螺', '5.22', 172), 'OPT': ('光纤连接器', '5.23', 172),
    'OTHER': ('其他电子元器件', '5.24', 173),
}
NON_COMPONENT_PREFIXES = frozenset({'TP', 'FID', 'MARK', 'MH', 'H', 'MP'})
PREFIX_CATEGORIES = {
    'IC': ('IC',), 'U': ('IC', 'LED'), 'N': ('IC',), 'C': ('C',), 'R': ('R',),
    'RN': ('R',), 'RT': ('RT',), 'RV': ('RT', 'RP'), 'RP': ('RP',), 'VR': ('RP', 'RT', 'D', 'IC'),
    'L': ('L',), 'T': ('L',), 'FB': (),
    'D': ('D', 'LED'), 'VD': ('D', 'LED'), 'VZ': ('D',), 'ZD': ('D',), 'DZ': ('D',), 'TVS': ('D',),
    'Q': ('Q',), 'VT': ('Q',),
    'J': ('J',), 'P': ('J',), 'CN': ('J', 'C'), 'CON': ('J',), 'XS': ('J',), 'XP': ('J',), 'XT': ('J',),
    'K': ('K',), 'RY': ('K',), 'KA': ('K',), 'KM': ('K',), 'RL': ('K',), 'RLY': ('K',),
    'SW': ('S',), 'S': ('S',), 'SA': ('S',), 'SB': ('S',),
    'Y': ('Y', 'SAW'), 'X': ('Y', 'J'), 'XTAL': ('Y',), 'G': ('Y', 'BT'),
    'FL': ('FL',), 'F': ('OTHER',), 'FU': ('OTHER',), 'BT': ('BT',), 'BAT': ('BT',),
    'LED': ('LED',), 'DS': ('LED', 'HL'), 'HL': ('HL', 'LED'), 'PC': ('LED',), 'OC': ('LED',),
    'M': ('M', 'IC', 'FL'), 'PCB': ('PCB',),
}


def model_origin(model):
    """Use the selected standard model; BOM manufacturer does not establish origin."""
    return next((origin for origin in ('国产', '进口') if model.startswith(origin)), '未确定')


def model_label(model):
    """Origin is edited separately in the component table."""
    return model_name(model).removeprefix('国产').removeprefix('进口')


def matches_domestic(model, domestic):
    origin = model_origin(model)
    if origin == '未确定':
        return True
    return domestic is not None and (origin == '国产') == domestic


def category_for_model(model):
    return MODELS[model].category


@lru_cache(maxsize=256)
def model_sort_key(model):
    if model == '待配置':
        return (999, 999, model)
    category = category_for_model(model)
    return (list(CATEGORIES).index(category), 0 if model.startswith('国产') else 1, model)


@dataclass(frozen=True)
class Recommendation:
    categories: tuple
    reason: str
    prefixes: tuple

    @property
    def label(self):
        return ' / '.join(f'{code} · {CATEGORIES[code][0]}' for code in self.categories) or '待识别'


def recommend(component):
    references = component.bom.get('assigned_designators', []) if component.bom else []
    if not references:
        references = [component.name.split('·')[0].strip()]
    prefixes = []
    for reference in references:
        match = re.match(r'^([A-Za-z]+)[_-]?(?=\d)', reference.strip())
        if match and match[1].upper() not in prefixes:
            prefixes.append(match[1].upper())
    categories = []
    for prefix in prefixes:
        if prefix in NON_COMPONENT_PREFIXES:
            continue
        for category in PREFIX_CATEGORIES.get(prefix, ()):
            if category not in categories:
                categories.append(category)
    if prefixes and all(p in NON_COMPONENT_PREFIXES for p in prefixes):
        reason = '非器件类位号前缀：' + ', '.join(prefixes)
    else:
        reason = '位号前缀：' + ', '.join(prefixes) if prefixes else '未识别位号前缀'
        if 'M' in prefixes:
            reason += '；M 也可能表示模块'
    return Recommendation(tuple(categories), reason, tuple(prefixes))
