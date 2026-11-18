"""Shared workbook resource budgets; content policy belongs to each file protocol."""

import zipfile

MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_CELLS = 2_000_000
MAX_PACKAGE_MEMBERS = 10_000


def check_package(archive, *, maximum_bytes=MAX_PACKAGE_BYTES):
    members = archive.infolist()
    if len(members) > MAX_PACKAGE_MEMBERS or sum(member.file_size for member in members) > maximum_bytes:
        raise ValueError('Excel 解压数据过大，请拆分工作簿后导入')
    if len({member.filename for member in members}) != len(members):
        raise ValueError('Excel 压缩包包含重复文件')
    if any(member.flag_bits & 0x1 for member in members):
        raise ValueError('无法读取加密的 Excel 压缩包')
    if any(member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for member in members):
        raise ValueError('Excel 压缩包使用了不受支持的压缩算法')
    return members


def check_cells(count, *, maximum=MAX_CELLS):
    if count > maximum:
        raise ValueError('Excel 工作簿数据范围过大，请删除无关数据或拆分后导入')
