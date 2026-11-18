"""Read-only BOM sources, explicit region/column mapping, and per-location records."""

import copy
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .domain import Component
from .model_registry import NON_COMPONENT_PREFIXES, PREFIX_CATEGORIES
from .nominal import parse_nominal

MAX_ROW_DESIGNATORS = 10_000
MAX_IMPORT_DESIGNATORS = 50_000
_KNOWN_DESIGNATOR_PREFIXES = frozenset(PREFIX_CATEGORIES) | NON_COMPONENT_PREFIXES
_FOOTPRINT_TOKEN_RE = re.compile(
    r'^(?:[RCLDUJ][_-]?)?(?:01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:[_-].*)?$'
    r'|^(?:SOT|SOIC|SOP|SSOP|TSSOP|MSOP|QFP|LQFP|TQFP|QFN|DFN|BGA|DIP|SIP|PLCC|TO)[_-]?\d',
    re.IGNORECASE,
)

# Retained canonical keys keep existing projects and source provenance readable.
BOM_FIELDS = ['Index', 'Designator', 'Value', 'Description', 'Footprint', 'Quantity',
              'Manufacturer', 'Manufacturer Part Number', 'MPN replace']
FIELD_LABELS = ['序号', '位号', '标称值', '描述', '封装', '数量', '厂商', '型号', '替代料号']
IMPORT_FIELDS = BOM_FIELDS[1:8]
IMPORT_LABELS = FIELD_LABELS[1:8]
ALIASES = {
    'Designator': ('designator', 'designators', 'reference', 'references', 'ref', 'refs',
                   'refdes', 'ref des', 'reference designator', 'reference designators',
                   'component reference', 'part reference', '位号', '位号名称', '元件位号', '器件位号', '元件标号', '标号'),
    'Value': ('value', 'val', 'comment', 'nominal value', 'component value', '标称值', '值', '参数值', '规格值'),
    'Description': ('description', 'component description', '描述', '说明', '物料描述', '器件描述', '元件描述', '名称及规格'),
    'Footprint': ('footprint', 'pcb footprint', 'package', 'pcb package', 'land pattern',
                  '封装', '封装名称', '元件封装', '器件封装'),
    'Quantity': ('quantity', 'qty', 'qty.', "q'ty", 'count', '数量', '用量'),
    'Manufacturer': ('manufacturer', 'manufacturer name', 'mfr', 'mfg', 'mfg name', '厂商', '制造商', '生产厂家', '品牌', '厂家'),
    'Manufacturer Part Number': ('manufacturer part number', 'manufacturerpartnumber', 'manufacturer pn',
                                 'manufacturer part no', 'manufacturer part no.',
                                 'mfr part number', 'mfr pn', 'mpn', 'part number', 'part no', 'part no.',
                                 'part #', '型号', '主料号', '制造商料号', '厂家型号', '器件型号', '规格型号', '制造商型号'),
}


def header_key(text):
    return ' '.join(text.casefold().replace('_', ' ').split())


def column_name(index):
    result = ''
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


_DESIGNATOR_RANGE = re.compile(
    r'^([A-Za-z_]+)(\d+)\s*(?:-|~|～|—|–|\.\.)\s*([A-Za-z_]*?)(\d+)$'
)


def _expand_designator(token):
    match = _DESIGNATOR_RANGE.fullmatch(token)
    if not match:
        return [token]
    prefix, start_s, end_prefix, end_s = match.groups()
    if end_prefix and end_prefix.upper() != prefix.upper():
        return [token]
    start, end = int(start_s), int(end_s)
    if end < start:
        if end_prefix:
            raise ValueError(f'位号范围无效：{token}')
        return [token]
    if end == start:
        return [token]
    if end - start + 1 > MAX_ROW_DESIGNATORS:
        raise ValueError(f'位号范围过大：{token}')
    width = len(start_s) if len(start_s) == len(end_s) and start_s.startswith('0') else 0
    return [f'{prefix}{num:0{width}d}' if width else f'{prefix}{num}' for num in range(start, end + 1)]


def designators(text):
    if not text.strip():
        return []
    result = []
    for token in re.split(r'[,，、;；\s]+', text.strip()):
        cleaned = token.strip()
        if cleaned:
            expanded = _expand_designator(cleaned)
            if len(result) + len(expanded) > MAX_ROW_DESIGNATORS:
                raise ValueError(f'单行位号总数超过上限（{MAX_ROW_DESIGNATORS:,}）')
            result.extend(expanded)
    return result


def _passive_kind(designator_text):
    text = (designator_text or '').strip()
    if not text:
        return None
    try:
        refs = designators(text)
    except ValueError:
        refs = [tok.strip() for tok in re.split(r'[,，、;；\s]+', text) if tok.strip()]
    prefixes = set()
    for ref in refs:
        matched = re.match(r'^([A-Za-z]+)[_-]?(?=\d)', ref)
        if matched:
            prefixes.add(matched.group(1).upper())
    if prefixes & {'R', 'RN'}:
        return 'R'
    if 'C' in prefixes:
        return 'C'
    if 'L' in prefixes:
        return 'L'
    return None


def _is_sequential_integers(values):
    if len(values) < 2 or not all(re.fullmatch(r'\d+', v) for v in values):
        return False
    nums = [int(v) for v in values]
    return nums == list(range(nums[0], nums[0] + len(nums)))


def _cell_nominal_info(text, kind_hint=None):
    stripped = (text or '').strip()
    if not stripped or _FOOTPRINT_TOKEN_RE.search(stripped):
        return False, False
    # Single prefix+number tokens without leading digits like R1 or L2 look like PCB designators unless 0R/R47M.
    if re.fullmatch(r'[RrLl]\d{1,2}', stripped):
        return False, False
    kinds = (kind_hint,) if kind_hint in ('R', 'C', 'L') else ('R', 'C', 'L')
    if not any(parse_nominal(stripped, k) is not None for k in kinds):
        return False, False
    if not re.fullmatch(r'\d+', stripped):
        return True, True
    return True, bool(re.fullmatch(r'[1-9]\d{2,3}|0|000|0000', stripped))


def _parse_quantity(text):
    normalized = text.replace(',', '').strip()
    if re.fullmatch(r'\d+', normalized):
        return int(normalized)
    if re.fullmatch(r'\d+\.0+', normalized):
        return int(normalized.split('.', 1)[0])
    raise ValueError('数量须为整数或留空')


def row_quantity(row):
    text = row['Quantity'].strip()
    refs = designators(row['Designator'])
    if text:
        count = _parse_quantity(text)
        if not 1 <= count <= 1_000_000:
            raise ValueError('数量须为 1～1,000,000 的整数')
        if refs and len(refs) != count:
            raise ValueError('位号数与数量不一致')
        return count
    return len(refs) or None


@dataclass
class BomImport:
    file_name: str
    sha256: str
    rows: list[dict]
    provenance: list[dict] = field(default_factory=list)

    @property
    def quantity(self):
        counts = [row_quantity(r) for r in self.rows]
        return sum(counts) if all(n is not None for n in counts) else None

    def components(self):
        grouped = []
        total_expanded = 0
        for i, row in enumerate(self.rows):
            refs = designators(row['Designator'])
            qty = row_quantity(row)
            total_expanded += len(refs) if len(refs) >= 2 and len(refs) == qty else 1
            if total_expanded > MAX_IMPORT_DESIGNATORS:
                raise ValueError(f'单次导入位号总数超过上限（{MAX_IMPORT_DESIGNATORS:,}）')
            name = row['Designator'] or row['Manufacturer Part Number'] or row['Value'] or f'BOM 条目 {i + 1}'
            if row['Designator'] and (row['Manufacturer Part Number'] or row['Value']):
                name += ' · ' + (row['Manufacturer Part Number'] or row['Value'])
            bom = {'file_name': self.file_name, 'sha256': self.sha256, 'row': dict(row),
                   'assigned_designators': refs}
            if self.provenance:
                bom['source'] = copy.deepcopy(self.provenance[i])
            grouped.append(Component(name, '待配置', qty, {},
                reference=f"BOM 原描述：{row['Description']}。待填写实际工作条件与取值依据。",
                bom=bom).validate())
        return [item for component in grouped for item in designator_components(component)]


@dataclass
class BomSource:
    file_name: str
    sha256: str
    cells: list[list[str]]
    header_row: int | None
    sheet_name: str = ''
    cell_errors: dict = field(default_factory=dict)
    merges: list = field(default_factory=list)

    @property
    def width(self):
        return max(map(len, self.cells), default=0)

    def cell(self, row, column):
        return self.cells[row][column] if column < len(self.cells[row]) else ''

    def header_mapping(self, columns):
        result = {}
        if self.header_row is not None:
            for key, aliases in ALIASES.items():
                matches = [col for col in columns if header_key(self.cell(self.header_row, col)) in aliases]
                if len(matches) > 1:
                    raise ValueError(f'表头存在多个“{FIELD_LABELS[BOM_FIELDS.index(key)]}”列，请手动选择映射')
                if matches:
                    result[key] = matches[0]
        return result

    def default_region(self):
        if self.header_row is not None:
            columns = [col for col in range(self.width)
                       if any(header_key(self.cell(self.header_row, col)) in a for a in ALIASES.values())]
            first = self.header_row + 1
            left, right = min(columns), max(columns)
        else:
            nonempty = [(r, c) for r, row in enumerate(self.cells) for c, value in enumerate(row) if value.strip()]
            first, left = min(r for r, c in nonempty), min(c for r, c in nonempty)
            right = max(c for r, c in nonempty)
        last = max((r for r in range(first, len(self.cells))
                    if any(self.cell(r, c).strip() for c in range(left, right + 1))), default=first - 1)
        if last < first:
            raise ValueError('BOM 没有元器件记录')
        return first, last, list(range(left, right + 1))

    def _infer_headerless_mapping(self, columns):
        mapping = {}
        active_rows = [r for r in range(len(self.cells)) if any(self.cell(r, c).strip() for c in columns)]
        if not active_rows:
            return mapping

        used_cols = set()
        row_refs = {}

        # 1. Infer Designator column by valid unique PCB designators.
        for col in columns:
            nonempty = [(r, self.cell(r, col).strip()) for r in active_rows if self.cell(r, col).strip()]
            if not nonempty:
                continue
            parsed_rows = {}
            all_refs_upper = []
            valid_cells = 0
            duplicate = False
            for r, val in nonempty:
                if _FOOTPRINT_TOKEN_RE.search(val):
                    continue
                try:
                    refs = designators(val)
                except ValueError:
                    continue
                if not refs:
                    continue
                tokens_ok = True
                for ref in refs:
                    m = re.fullmatch(r'([A-Za-z]{1,5})_?(\d+)', ref)
                    if not m or m.group(1).upper() not in _KNOWN_DESIGNATOR_PREFIXES:
                        tokens_ok = False
                        break
                if not tokens_ok:
                    continue
                uppers = [ref.upper() for ref in refs]
                if len(set(uppers)) != len(uppers):
                    duplicate = True
                    break
                valid_cells += 1
                parsed_rows[r] = refs
                all_refs_upper.extend(uppers)
            if not duplicate and valid_cells / len(nonempty) >= 0.8 and len(all_refs_upper) == len(set(all_refs_upper)):
                mapping['Designator'] = col
                used_cols.add(col)
                row_refs = parsed_rows
                break

        # 2. Infer Quantity column by small positive integers matching designator counts.
        for col in columns:
            if col in used_cols:
                continue
            nonempty = [(r, self.cell(r, col).strip()) for r in active_rows if self.cell(r, col).strip()]
            if not nonempty:
                continue
            quantities = []
            ok = True
            for r, val in nonempty:
                try:
                    qty = _parse_quantity(val)
                except ValueError:
                    ok = False
                    break
                if not 1 <= qty <= 100_000:
                    ok = False
                    break
                if row_refs and r in row_refs and qty != len(row_refs[r]):
                    ok = False
                    break
                quantities.append((val, qty))
            if not ok:
                continue
            raw_vals = [v for v, _ in quantities]
            if not row_refs:
                if _is_sequential_integers(raw_vals):
                    continue
                if any(q > 1000 for _, q in quantities):
                    continue
                if sum(1 for _, q in quantities if q >= 100) / len(quantities) >= 0.5:
                    continue
            else:
                # Reject sequential 1..N index column when multiple rows all have 1 designator.
                if _is_sequential_integers(raw_vals) and any(
                    r in row_refs and int(v) != len(row_refs[r]) for (r, v) in nonempty
                ):
                    continue
            mapping['Quantity'] = col
            used_cols.add(col)
            break

        # 3. Infer Footprint column before Value so package codes stay in Footprint.
        for col in columns:
            if col in used_cols:
                continue
            nonempty = [self.cell(r, col).strip() for r in active_rows if self.cell(r, col).strip()]
            if nonempty and sum(1 for v in nonempty if _FOOTPRINT_TOKEN_RE.search(v)) / len(nonempty) >= 0.5:
                mapping['Footprint'] = col
                used_cols.add(col)
                break

        # 4. Infer Value column ONLY if >= 50% parse as valid R/C/L nominal values (not sequential 1,2,3...).
        des_col = mapping.get('Designator')
        for col in columns:
            if col in used_cols:
                continue
            nonempty = [(r, self.cell(r, col).strip()) for r in active_rows if self.cell(r, col).strip()]
            if not nonempty:
                continue
            raw_vals = [v for _, v in nonempty]
            if _is_sequential_integers(raw_vals):
                continue
            target_cells = nonempty
            if des_col is not None:
                passive_subset = [
                    (r, v, k)
                    for r, v in nonempty
                    if (k := _passive_kind(self.cell(r, des_col))) is not None
                ]
                if passive_subset:
                    infos = [_cell_nominal_info(v, k) for _r, v, k in passive_subset]
                    valid_count = sum(1 for ok, _pat in infos if ok)
                    pattern_count = sum(1 for _ok, pat in infos if pat)
                    if valid_count / len(passive_subset) >= 0.5 and pattern_count >= 1:
                        mapping['Value'] = col
                        used_cols.add(col)
                        break
                    continue
            infos = [_cell_nominal_info(v, None) for _r, v in target_cells]
            valid_count = sum(1 for ok, _pat in infos if ok)
            pattern_count = sum(1 for _ok, pat in infos if pat)
            if valid_count / len(target_cells) >= 0.5 and pattern_count >= 1:
                mapping['Value'] = col
                used_cols.add(col)
                break

        # 5. If no field was inferred at all, map the first non-sequential text column to Description (never Value).
        if not mapping:
            for col in columns:
                vals = [self.cell(r, col).strip() for r in active_rows if self.cell(r, col).strip()]
                if vals and not _is_sequential_integers(vals):
                    mapping['Description'] = col
                    break
            if not mapping and columns:
                mapping['Description'] = columns[0]
        return mapping

    def default_mapping(self, columns):
        if self.header_row is None:
            mapping = self._infer_headerless_mapping(columns)
            return {key: mapping.get(key) for key in IMPORT_FIELDS}

        mapping = self.header_mapping(columns)
        val_col = mapping.get('Value')
        if val_col is not None and header_key(self.cell(self.header_row, val_col)) == 'comment':
            data_rows = range(self.header_row + 1, len(self.cells))
            des_col = mapping.get('Designator')
            reject_comment_as_value = False
            if des_col is not None:
                passive_cells = [
                    (self.cell(r, val_col).strip(), k)
                    for r in data_rows
                    if (k := _passive_kind(self.cell(r, des_col))) is not None and self.cell(r, val_col).strip()
                ]
                if passive_cells:
                    valid_cnt = sum(1 for v, k in passive_cells if _cell_nominal_info(v, k)[0])
                    if valid_cnt / len(passive_cells) < 0.5 or _is_sequential_integers([v for v, _ in passive_cells]):
                        reject_comment_as_value = True
            else:
                nonempty = [self.cell(r, val_col).strip() for r in data_rows if self.cell(r, val_col).strip()]
                if nonempty:
                    valid_cnt = sum(1 for v in nonempty if _cell_nominal_info(v, None)[0])
                    if valid_cnt / len(nonempty) < 0.5 or _is_sequential_integers(nonempty):
                        reject_comment_as_value = True
            if reject_comment_as_value:
                mapping.pop('Value', None)
                if mapping.get('Description') is None:
                    mapping['Description'] = val_col
        return {key: mapping.get(key) for key in IMPORT_FIELDS}

    def mapped_rows(self, first, last, mapping):
        return [(row, {**dict.fromkeys(BOM_FIELDS, ''),
                       **{key: self.cell(row, mapping[key]) if mapping.get(key) is not None else ''
                          for key in IMPORT_FIELDS}})
                for row in range(first, last + 1)]

    def build_import(self, first, last, columns, mapping):
        if not (0 <= first <= last < len(self.cells)) or not columns or any(c < 0 or c >= self.width for c in columns):
            raise ValueError('请选择有效的数据区域')
        assigned = [c for c in mapping.values() if c is not None]
        if not assigned or any(c not in columns for c in assigned):
            raise ValueError('请至少映射一列，且仅使用已选区域中的列')
        if len(assigned) != len(set(assigned)):
            raise ValueError('同一源列不能重复分配给多个字段，请调整映射')
        for (row, col), message in self.cell_errors.items():
            if first <= row <= last and col in assigned:
                raise ValueError(f'{self.sheet_name}!{column_name(col)}{row + 1}：{message}')
        rows, provenance, seen = [], [], set()
        for source_row, row in self.mapped_rows(first, last, mapping):
            if not any(value.strip() for value in row.values()):
                continue
            row['Index'] = str(len(rows) + 1)
            try:
                row_quantity(row)
                refs = [r.upper() for r in designators(row['Designator'])]
                if len(set(refs)) != len(refs) or seen.intersection(refs):
                    raise ValueError('存在重复位号')
                if len(seen) + len(refs) > MAX_IMPORT_DESIGNATORS:
                    raise ValueError(f'单次导入位号总数超过上限（{MAX_IMPORT_DESIGNATORS:,}）')
                seen.update(refs)
            except ValueError as error:
                raise ValueError(f'源表第 {source_row + 1} 行：{error}') from error
            rows.append(row)
            provenance.append({'row_number': source_row + 1, 'cells': list(self.cells[source_row]),
                               'mapping': dict(mapping), 'columns': list(columns), 'sheet_name': self.sheet_name})
        if not rows:
            raise ValueError('所选区域没有可导入的元器件记录')
        return BomImport(self.file_name, self.sha256, rows, provenance)


def detect_header(cells):
    names = {name for aliases in ALIASES.values() for name in aliases}
    single_column_names = {*ALIASES['Designator'], *ALIASES['Manufacturer Part Number'], *ALIASES['Value']}
    for i, row in enumerate(cells[:50]):
        texts = [header_key(v) for v in row if v.strip()]
        matches = sum(v in names for v in texts)
        if matches >= 2:
            return i
        if matches == 1 and all(v in names or v in ('index', '序号') for v in texts):
            if len(texts) >= 2 or texts[0] in single_column_names:
                return i
    return None


def read_bom_sources(path):
    from .excel_input import check_file_size
    from .excel_limits import MAX_CELLS, check_cells

    path = Path(path)
    check_file_size(path.stat().st_size)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if path.suffix.lower() in ('.xlsx', '.xls'):
        from .excel_input import read_xls, read_xlsx
        sheets = (read_xls if path.suffix.lower() == '.xls' else read_xlsx)(data)
        if not sheets:
            raise ValueError('工作簿中没有非空工作表')
        return [BomSource(path.name, digest, cells, detect_header(cells), name, errors, merges)
                for name, cells, errors, merges in sheets]
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            text = data.decode('utf-16')
        except UnicodeDecodeError as error:
            raise ValueError('BOM 需为 UTF-8、UTF-16 或 GB18030 编码的 CSV / TSV 文件') from error
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = data.decode('gb18030')
            except UnicodeDecodeError as error:
                raise ValueError('BOM 需为 UTF-8、UTF-16 或 GB18030 编码的 CSV / TSV 文件') from error
    if '\x00' in text:
        raise ValueError('BOM 文件包含无效空字符，请确认文本编码格式')
    try:
        delimiter = csv.Sniffer().sniff(text[:65536], delimiters=',;\t').delimiter
    except csv.Error:
        delimiter = '\t' if path.suffix.lower() == '.tsv' else ','
    # Sniffer can mistake escaped triple quotes for doublequote=False. Keep CSV
    # quoting rules explicit, and infer only the delimiter.
    cells = []
    width = 0
    stored = 0
    has_nonempty = False
    for row in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True):
        stored += max(1, len(row))
        width = max(width, len(row))
        check_cells(stored, maximum=MAX_CELLS)
        check_cells((len(cells) + 1) * max(1, width), maximum=MAX_CELLS)
        if not has_nonempty and any(value.strip() for value in row):
            has_nonempty = True
        cells.append(row)
    if not has_nonempty:
        raise ValueError('BOM 文件为空')
    return [BomSource(path.name, digest, cells, detect_header(cells))]


def read_bom_source(path):
    return read_bom_sources(path)[0]


def read_bom(path):
    source = read_bom_source(path)
    first, last, columns = source.default_region()
    return source.build_import(first, last, columns, source.default_mapping(columns))


def append_bom(project, imported):
    components = imported.components()
    existing_refs = {ref.upper() for c in project.components for ref in c.bom.get('assigned_designators', [])}
    new_refs = {ref.upper() for c in components for ref in c.bom.get('assigned_designators', [])}
    if existing_refs & new_refs:
        raise ValueError('当前工程已有相同位号，重复导入会重复计数：' + ', '.join(sorted(existing_refs & new_refs)))
    source_rows = {(c.bom.get('source', {}).get('sheet_name', ''), c.bom.get('source', {}).get('row_number')) for c in components}
    for c in project.components:
        if c.bom.get('sha256') == imported.sha256:
            row = c.bom.get('source', {}).get('row_number')
            sheet = c.bom.get('source', {}).get('sheet_name', '')
            if row is None or any(r is None for _, r in source_rows) or (sheet, row) in source_rows:
                raise ValueError('所选 BOM 数据已在当前工程中，重复导入会重复计数')
    project.components.extend(components)
    return len(components)


def designator_components(component):
    """Expand only when quantity and designators agree, preserving old inputs."""
    references = component.bom.get('assigned_designators', [])
    if len(references) < 2 or len(references) != component.quantity:
        return [component]
    split = []
    for index, reference in enumerate(references):
        item = copy.deepcopy(component)
        if index:
            item.identifier = str(uuid5(NAMESPACE_URL, component.identifier + '/' + reference))
        item.quantity = 1
        item.bom['assigned_designators'] = [reference]
        suffix = component.name.partition(' · ')[2] or component.name
        item.name = f'{reference} · {suffix}'
        item.validate()
        split.append(item)
    return split


def expand_designators(project):
    project.components = [item for c in project.components for item in designator_components(c)]
