"""Concise quality choices, with the complete requirements read from the standard."""

import re

from .reference_data import compact, fragments

# These are summaries of the named standard tables, not interchangeable grades.
IC = {
    'A3': 'QPL B级／企业军用标准',
    'A4': 'QPL B1级（旧标准）',
    'A5': '规定军用标准，未列QPL',
    'A6': 'GB/T 4589.1 Ⅲ类',
    'B1': '军用筛选B2等',
    'B2': 'GB/T 4589.1 Ⅰ类',
    'C': '低档产品',
}
FET = {
    'A2': 'QPL JCT级', 'A3': 'QPL JT级',
    'A4': 'QPL JP级／企业军用标准',
    'A5': 'GJB 33A，未列QPL', 'A6': 'GB/T 4589.1 Ⅲ类',
    'B1': '军用筛选B2／Ⅱ类', 'B2': 'GB/T 4589.1 Ⅰ类', 'C': '低档产品',
}
RESISTOR = {
    'A1P': 'P/L级等QPL产品', 'A1M': 'M/W级等QPL产品',
    'A2': '规定军用标准，未列QPL', 'B1': 'B2产品经军用筛选',
    'B2': 'GB/T 5729等规定产品', 'C': '低档产品',
}
CAPACITOR = {
    'A1P': 'QPL P级／GJB4157A未列QPL',
    'A1M': 'M/K级等QPL产品', 'A2': '规定军用标准，未列QPL',
    'B1': 'B2产品经军用筛选', 'C': '低档产品',
}
IMPORTED_IC = {
    'S': '宇航级／类宇航级', 'B': '军级',
    'B-1': '非塑封QML其他等级', 'B-2': '塑封QML／规定条件非军级',
    'B-3': 'C-1产品经军用筛选', 'C-1': '规定质量控制的工业级',
    'C-2': '其他民用／未知筛选',
}
IMPORTED_DISCRETE = {
    'JANS': '宇航级', 'JANJ': '准宇航级', 'JANTXV': '超特军级',
    'JANTX': '特军级', 'JAN': '军级', 'B-1（经筛选）': 'B-2产品经军用筛选',
    'B-2（受控工业级）': '规定生产与试验流程的工业级', 'C': '其他民用／未知筛选',
}
PASSIVE = {**dict.fromkeys(('S', 'R', 'P', 'M'), '有可靠性指标'),
           '军规无可靠性指标': '执行军用规范，无可靠性指标',
           '民用或未知筛选': '其他民用／未知筛选'}

DEFINITIONS = {
    '国产普通数字集成电路': (('3',), IC),
    '国产硅场效应晶体管': (('46',), FET),
    '国产片式膜电阻器': (('124',), RESISTOR),
    '国产1类瓷介电容器': (('219',), CAPACITOR | {'B2': 'GB/T 2693、5966等规定产品'}),
    '国产2类瓷介电容器': (('224',), CAPACITOR | {'B2': 'GB/T 2693、5968等规定产品'}),
    '国产印制板': (('306',), {'A1': 'GJB 362A，已列QPL',
                             'A2': 'GJB 362A，未列QPL',
                             'B': '规定国家／行业标准产品', 'C': '低档产品'}),
    '国产焊接点': (('310',), {'A': '严格质量监控并修补缺陷',
                             'B': '常规程序焊接', 'C': '低档'}),
    '进口模拟集成电路': (('C.15',), IMPORTED_IC),
    '进口微处理器': (('C.15',), IMPORTED_IC),
    '进口静态随机存储器（SRAM）': (('C.15',), IMPORTED_IC),
    '进口普通二极管': (('C.32',), IMPORTED_DISCRETE),
    '进口双极型晶体管': (('C.32',), IMPORTED_DISCRETE),
    '进口其他分立半导体': (('C.32',), IMPORTED_DISCRETE),
    '进口电阻器': (('C.46',), PASSIVE),
    '进口电容器': (('C.60',), PASSIVE),
    '进口电感器': (('C.66', 'C.69'), PASSIVE),
}


def quality_requirement(number, value):
    """Read the grade's actual description, including merged and continued cells."""
    grade = {'军规无可靠性指标': '执行军用规范但无可靠性指标的产品',
             '民用或未知筛选': ('民品或未知筛选等级的产品' if number == 'C.66'
                           else '其他民用或未知筛选等级的产品'),
             'B-1（经筛选）': 'B-1', 'B-2（受控工业级）': 'B-2'}.get(value, value)
    records = []
    for table in fragments(number):
        for cell in table['cells']:
            if compact(cell['text']) != compact(grade):
                continue
            row = cell['row_no']
            if number in ('3', '46', '124', '219', '224', '306'):
                columns = range(2, min(table['columns'], 4))
            elif number in ('C.15', 'C.32', '310'):
                columns = (1,)
            else:
                columns = range(table['columns'])
            details = []
            for column in columns:
                source = table['grid'][row, column]
                if source['kind'] != 'text' or source['text'] in details:
                    continue
                details.append(source['text'])
            if details:
                records.append(f'表 {number}\n' + '\n'.join(details))
    return '\n\n'.join(dict.fromkeys(records))


def quality_option(model, value):
    """Return display/tooltip text. Callers must keep ``value`` as item data."""
    from .standards import MODELS
    spec = MODELS[model]
    tables, summaries = spec.quality_tables, dict(spec.quality_summaries)
    tooltip = '\n\n'.join(
        req for table in tables if (req := quality_requirement(str(table), value).strip())
    )
    summary = summaries.get(value)
    if not summary:
        details = [line for line in tooltip.splitlines()
                   if line and not line.startswith('表 ') and compact(line) != compact(value)
                   and not re.fullmatch(r'[A-Z][A-Z0-9-]*', compact(line))]
        summary = details[0] if details else ''
        if len(summary) > 30:
            summary = summary[:29] + '…'
    title = str(value).split('（', 1)[0]
    return (f'{title} · {summary}' if summary else str(value), tooltip or '请按相应器件质量等级表确认。')
