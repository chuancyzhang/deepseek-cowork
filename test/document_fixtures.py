"""Small valid Office/PDF documents generated inside each test's temporary folder."""

from pathlib import Path


def write_pptx(path):
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "Fixture presentation"
    presentation.save(path)
    return Path(path)


def write_pdf(path):
    from reportlab.pdfgen.canvas import Canvas

    canvas = Canvas(str(path), pagesize=(320, 240))
    canvas.drawString(24, 200, "Fixture PDF document")
    canvas.showPage()
    canvas.save()
    return Path(path)


def write_xls_batches(path, long_cell):
    """Build the string-only BIFF8 workbook used by bounded-reading tests.

    xlrd accepts a raw Workbook stream, so no binary sample or OLE writer is
    needed. Long strings use real SST CONTINUE records under the BIFF limit.
    """
    import struct

    record = lambda kind, data=b"": struct.pack("<HH", kind, len(data)) + data
    bof = lambda kind: record(0x0809, struct.pack("<HHHHII", 0x0600, kind, 0x0DBB, 0x07CC, 0x41, 0x06))
    sheets = [("数据", [("Name", "Value"), ("长单元格", long_cell), ("tail", "done")]),
              ("第二表", [("最后一项",)])]
    values = [value for _, rows in sheets for row in rows for value in row]
    strings = list(dict.fromkeys(values))
    chunks = []
    chunk = bytearray(struct.pack("<II", len(values), len(strings)))
    for value in strings:
        raw = value.encode("utf-16le")
        if len(chunk) + 3 > 8000:
            chunks.append(bytes(chunk))
            chunk = bytearray()
        chunk.extend(struct.pack("<HB", len(raw) // 2, 1))
        while raw:
            count = min(len(raw), (8000 - len(chunk)) // 2 * 2)
            chunk.extend(raw[:count])
            raw = raw[count:]
            if raw:
                chunks.append(bytes(chunk))
                chunk = bytearray(b"\x01")
    chunks.append(bytes(chunk))
    sst = b"".join(record(0x00FC if index == 0 else 0x003C, data) for index, data in enumerate(chunks))
    bodies = []
    for _, rows in sheets:
        cells = b"".join(record(0x00FD, struct.pack("<HHHI", r, c, 0, strings.index(value)))
                         for r, row in enumerate(rows) for c, value in enumerate(row))
        dimensions = record(0x0200, struct.pack("<IIHHH", 0, len(rows), 0, max(map(len, rows)), 0))
        bodies.append(bof(0x10) + dimensions + cells + record(0x000A))
    def bound_sheet(name, offset):
        return record(0x0085, struct.pack("<IBBBB", offset, 0, 0, len(name), 1) + name.encode("utf-16le"))
    globals_start = bof(0x05) + record(0x0042, struct.pack("<H", 1200))
    offset = len(globals_start) + sum(len(bound_sheet(name, 0)) for name, _ in sheets) + len(sst) + 4
    bounds = []
    for (name, _), body in zip(sheets, bodies):
        bounds.append(bound_sheet(name, offset))
        offset += len(body)
    Path(path).write_bytes(globals_start + b"".join(bounds) + sst + record(0x000A) + b"".join(bodies))
    return Path(path)
