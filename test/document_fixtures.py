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
