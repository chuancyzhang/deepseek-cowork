"""Read-only worksheet snapshots, independent of the safe editing preflight."""
from __future__ import annotations

import os
import zipfile

from .deliverable_editing import (xlsx_to_univer_snapshot, rows_to_univer_snapshot,
                                 MAX_POPULATED_CELLS, MAX_WORKSHEETS)
from .workbench import WorkbenchError


def load_sheet_preview(path):
    if os.path.getsize(path) > 25 * 1024 * 1024:
        raise WorkbenchError("preview_too_large", "工作簿超过 25 MiB，请使用打印预览或系统应用打开。")
    warnings = []
    if os.path.splitext(path)[1].lower() == ".xlsx":
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 150 * 1024 * 1024:
                raise WorkbenchError("preview_too_large", "工作簿解压后规模过大，请使用系统应用打开。")
            if any(name.startswith(("xl/drawings/", "xl/charts/", "xl/pivotTables/")) for name in archive.namelist()):
                warnings.append("图表、图片和数据透视表的完整外观请查看打印预览。")
        snapshot = xlsx_to_univer_snapshot(path, preview=True)
        warnings.append("公式显示文件上次保存的计算结果。")
    else:
        import xlrd
        workbook = xlrd.open_workbook(path, formatting_info=True)
        snapshot = None
        try:
            if workbook.nsheets > MAX_WORKSHEETS:
                raise WorkbenchError("preview_too_large", "工作表数量超过阅读上限。")
            total = 0
            styles = {}
            for index, sheet in enumerate(workbook.sheets()):
                total += sheet.nrows * sheet.ncols
                if total > MAX_POPULATED_CELLS:
                    raise WorkbenchError("preview_too_large", "工作表规模超过阅读上限。")
                current = rows_to_univer_snapshot([], sheet.name)
                sid = current["sheetOrder"][0]
                item = current["sheets"][sid]
                item["hidden"] = int(sheet.visibility != 0)
                item["rowCount"] = max(100, sheet.nrows + 20)
                item["columnCount"] = max(26, sheet.ncols + 10)
                for row in range(sheet.nrows):
                    cells = {}
                    for col in range(sheet.ncols):
                        cell = sheet.cell(row, col)
                        if cell.ctype == xlrd.XL_CELL_EMPTY:
                            continue
                        value = cell.value
                        entry = {"v": value, "t": 2 if cell.ctype in {xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE} else 3 if cell.ctype == xlrd.XL_CELL_BOOLEAN else 1}
                        xf = workbook.xf_list[cell.xf_index]
                        font = workbook.font_list[xf.font_index]
                        style_id = f"s{cell.xf_index}"
                        style = {"ff": font.name, "fs": font.height / 20, "bl": int(font.bold), "it": int(font.italic),
                                 "ht": {1: 1, 2: 2, 3: 3}.get(xf.alignment.hor_align, 0), "tb": 3 if xf.alignment.text_wrapped else 1}
                        rgb = workbook.colour_map.get(font.colour_index)
                        if rgb:
                            style["cl"] = {"rgb": "#%02x%02x%02x" % rgb}
                        rgb = workbook.colour_map.get(xf.background.pattern_colour_index)
                        if rgb and xf.background.fill_pattern:
                            style["bg"] = {"rgb": "#%02x%02x%02x" % rgb}
                        fmt = workbook.format_map.get(xf.format_key)
                        if fmt:
                            style["n"] = {"pattern": fmt.format_str}
                        styles[style_id] = style
                        entry["s"] = style_id
                        cells[str(col)] = entry
                    if cells:
                        item["cellData"][str(row)] = cells
                for row, info in sheet.rowinfo_map.items():
                    item["rowData"][str(row)] = {"h": info.height / 20 / .75, "hd": int(info.hidden)}
                for col, info in sheet.colinfo_map.items():
                    item["columnData"][str(col)] = {"w": info.width / 256 * 7 + 5, "hd": int(info.hidden)}
                item["mergeData"] = [{"startRow": r0, "endRow": r1 - 1, "startColumn": c0, "endColumn": c1 - 1} for r0, r1, c0, c1 in sheet.merged_cells]
                if snapshot is None:
                    snapshot = current
                else:
                    snapshot["sheetOrder"].append(sid)
                    snapshot["sheets"][sid] = item
            if snapshot is None:
                raise WorkbenchError("empty_workbook", "工作簿没有工作表。")
            snapshot["styles"] = styles
            warnings.append("旧版 XLS 的图表及完整版式请查看打印预览。")
        finally:
            workbook.release_resources()
    return {"snapshot": snapshot, "warning": " ".join(warnings)}
