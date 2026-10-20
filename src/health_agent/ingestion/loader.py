"""把 JSON / CSV / TSV 读成统一的原始行。

只做"读文件"这一件事：不改值、不猜字段、不做单位换算。所有判断都在 validation 层。
文件级结构问题（不是数组、不是对象、编码不可识别）直接抛 LoadError 让用户修文件，
而不是带着半个文件继续跑。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 文件级错误。区别于"某一行不合法"——那属于校验结果，不是加载失败。
class LoadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RawRow:
    """一条原始记录。index 是文件内的数据行序号（从 1 开始，不含表头）。"""

    index: int
    data: dict[str, Any]


_ENCODINGS = ("utf-8-sig", "gb18030")


def decode_bytes(data: bytes) -> str:
    """按 utf-8-sig → gb18030 顺序尝试解码。

    utf-8-sig 会顺带吃掉 BOM，避免首列键名变成 '\\ufeffmetric_name'。
    gb18030 用来兜 Excel 导出的中文 CSV。
    """
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LoadError("文件编码无法识别（已尝试 UTF-8 / GB18030），请另存为 UTF-8 后重试")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_key(raw: Any) -> str:
    return str(raw).replace("\ufeff", "").strip()


def load_rows(path: str | Path) -> list[RawRow]:
    file_path = Path(path)
    if not file_path.is_file():
        raise LoadError(f"文件不存在: {file_path}")

    suffix = file_path.suffix.lower()
    text = decode_bytes(file_path.read_bytes())
    if suffix == ".json":
        return _from_json(text, file_path.name)
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        return _from_delimited(text, file_path.name, delimiter)
    raise LoadError(f"不支持的文件类型 {suffix!r}，仅支持 .json / .csv / .tsv")


def _from_json(text: str, name: str) -> list[RawRow]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoadError(f"{name} 不是合法 JSON: {exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列）") from exc

    if isinstance(payload, dict):
        items = payload.get("records")
        if items is None and "metric_name" in payload:
            items = [payload]
        if items is None:
            raise LoadError(f"{name} 缺少 records 数组，也不是单条记录对象")
    elif isinstance(payload, list):
        items = payload
    else:
        raise LoadError(f"{name} 顶层必须是对象或数组")

    if not isinstance(items, list):
        raise LoadError(f"{name} 的 records 必须是数组")

    rows: list[RawRow] = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise LoadError(f"{name} 第 {position} 条记录不是对象")
        rows.append(RawRow(index=position, data={_clean_key(k): v for k, v in item.items()}))
    return rows


def _from_delimited(text: str, name: str, delimiter: str) -> list[RawRow]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise LoadError(f"{name} 缺少表头")

    rows: list[RawRow] = []
    for position, raw in enumerate(reader, start=1):
        data: dict[str, Any] = {}
        for key, value in raw.items():
            if key is None:
                # 行尾多出的多余列，无处归属，直接忽略
                continue
            text_value = value.strip() if isinstance(value, str) else value
            # 空单元格 = 本次没有这个值（缺失），不是"不可读"
            data[_clean_key(key)] = text_value if text_value != "" else None
        if all(v is None for v in data.values()):
            continue
        rows.append(RawRow(index=position, data=data))
    return rows
