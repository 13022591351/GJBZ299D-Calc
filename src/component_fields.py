"""Field identities shared by the component view and its saved preferences."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    width: int
    groupable: bool = False


COLUMNS = (
    Column('index', '序号', 50),
    Column('designators', '位号 / 名称', 120),
    Column('origin', '国产', 46, True),
    Column('Value', '标称值', 96, True),
    Column('Footprint', '封装', 72, True),
    Column('Manufacturer Part Number', '型号', 110, True),
    Column('quantity', '数量', 50),
    Column('model', '失效率预计模型', 140, True),
    Column('parameters', '器件参数', 140, True),
    Column('stress', '工作应力', 145, True),
    Column('status', '配置状态', 74),
    Column('rate', '失效率 λp (10⁻⁶/h)', 145),
    Column('weighted', '失效率合计 (10⁻⁶/h)', 145),
    Column('share', '占比 (%)', 78),
    Column('Manufacturer', '厂商', 110, True),
    Column('Description', '描述', 170, True),
)
SORT_MODES = ('bom', 'model', 'weighted_desc', 'rate_desc', 'quantity_desc')
SORT_LABELS = {
    'bom': '原 BOM 顺序',
    'model': '计算模型顺序',
    'weighted_desc': '失效率合计降序',
    'rate_desc': '单件失效率降序',
    'quantity_desc': '数量降序',
}
DEFAULT_GROUP_FIELDS = ['Value', 'Footprint', 'Manufacturer Part Number', 'model', 'origin', 'parameters', 'stress']
DEFAULT_VISIBLE_FIELDS = [c.key for c in COLUMNS if c.key not in (
    'Manufacturer', 'Description', 'rate')]
GROUP_FIELDS = {c.key for c in COLUMNS if c.groupable}
FIELD_KEYS = {c.key for c in COLUMNS}
