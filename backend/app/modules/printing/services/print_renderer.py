"""Memory-only A4 renderer for authorized, explicitly non-final snapshots."""
from __future__ import annotations

from html import escape
from io import BytesIO
from hashlib import sha256
from threading import RLock

from .print_policy import PrintDenied, PrintDocument
from .branding_payload import PrintBranding, brand_lines


_LAB_FONT_LOCK = RLock()


# L03: only approved patient laboratory copies use this embedded font path.
# Font paths are operator-installed OS files, never supplied by a request.
def _lab_unicode_font(text):
    import os
    import unicodedata as ud
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ReportLab's simple text path is not a general complex-script shaper.
    # Deny unsupported controls, combining sequences and RTL rather than
    # silently distort clinical text. Preserve every supported code point.
    for c in text:
        if c in '\n\t':
            continue
        if (ord(c) > 0xffff or ud.category(c)[0] in ('C', 'M')
                or ud.bidirectional(c) in ('R', 'AL', 'AN')):
            raise PrintDenied('Unsupported laboratory document characters')
    if os.name == 'nt':
        root = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        candidates = (('Arial', root / 'arial.ttf'), ('Segoe', root / 'segoeui.ttf'))
    else:
        candidates = (('DejaVu', Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')),)
    # Register under an app-specific family name; all markup variants use
    # the same validated face. No implicit fallback to Symbol/ZapfDingbats.
    for label, path in candidates:
        if not path.is_file():
            continue
        try:
            name = 'CSP_L03_' + label
            with _LAB_FONT_LOCK:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                    pdfmetrics.registerFontFamily(name, normal=name, bold=name,
                                                  italic=name, boldItalic=name)
                face = pdfmetrics.getFont(name).face
            if all(c in '\n\t' or face.charToGlyph.get(ord(c), 0) != 0 for c in text):
                return name
        except Exception:
            continue
    raise PrintDenied('Unsupported laboratory document characters')



def _brand_logo(canvas, branding):
    if branding is None or branding.rgb is None:
        return
    from reportlab.lib.utils import ImageReader
    scale = min(150 / branding.width, 50 / branding.height)
    w, h = branding.width*scale, branding.height*scale
    canvas.drawImage(ImageReader(BytesIO(branding.png())), 45, 800-h, width=w, height=h)

def _render_released_lab(doc, branding=None):
    """Bounded Unicode laboratory copy; no change to release policy."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

    headings = brand_lines(branding)
    disclaimer = ('This copy does not prove payment, clinical verification, '
                  'diagnosis, signature, delivery, or chart release.')
    metadata = [f'Clinic reference: {doc.clinic_id}   Record reference: {doc.record_id}',
                f'Source version: {doc.source_version}']
    digest = 'SOURCE SHA-256: ' + doc.source_digest
    font = _lab_unicode_font('\n'.join([*headings, doc.title, doc.watermark, disclaimer,
        *metadata, digest, 'Page 0123456789', *[s for pair in doc.fields for s in pair]]))
    row = ParagraphStyle('l03_row', fontName=font, fontSize=10, leading=14,
                         spaceAfter=3, splitLongWords=1)
    title = ParagraphStyle('l03_title', parent=row, fontSize=13, leading=18,
                           textColor=colors.HexColor('#17354A'), spaceAfter=7)
    note = ParagraphStyle('l03_note', parent=row, fontSize=9, leading=13,
                          textColor=colors.HexColor('#842020'), spaceAfter=8)
    def para(value, style=row):
        return Paragraph(escape(value).replace('\n', '<br/>'), style)
    story = [*[para(line, title if i < 2 else row) for i, line in enumerate(headings)], para(doc.title, title), para(doc.watermark, note),
             *[para(v) for v in metadata], Spacer(1, 8)]
    for key, value in doc.fields:
        if key != 'Report Text':
            story.append(para(key + ': ' + value))
            continue
        story.append(para(key + ':', title))
        # The service supplies a blank separator between individual result
        # blocks. Keep each bounded block together where space permits.
        for block in value.split('\n\n'):
            if block:
                story.append(KeepTogether([*[para(line) for line in block.split('\n') if line], Spacer(1, 7)]))
    story.extend([Spacer(1, 8), para(digest, note), para(disclaimer, note)])
    stream = BytesIO()
    def footer(canvas, document):
        canvas.saveState()
        _brand_logo(canvas, branding)
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor('#842020'))
        canvas.drawString(45, 34, doc.watermark)
        canvas.drawRightString(A4[0]-45, 21, f'Page {document.page}')
        canvas.restoreState()
    try:
        SimpleDocTemplate(stream, pagesize=A4, rightMargin=45, leftMargin=45,
                          topMargin=111 if branding is not None and branding.rgb is not None else 48, bottomMargin=56, pageCompression=1).build(
                              story, onFirstPage=footer, onLaterPages=footer)
    except Exception as exc:
        raise PrintDenied('Print renderer failed safely') from exc
    result = stream.getvalue()
    if not result.startswith(b'%PDF-') or not result.rstrip().endswith(b'%%EOF') or len(result)>3*1024*1024:
        raise PrintDenied('PDF output exceeded safety bounds')
    return result


def _render_staged_radiology(doc, branding=None):
    """L04: synthetic non-diagnostic patient copy, with bounded pagination."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    headings = brand_lines(branding)
    disclaimer = ('This copy does not prove payment, clinical verification, '
                  'diagnosis, signature, delivery, or chart release.')
    metadata = [f'Clinic reference: {doc.clinic_id}   Record reference: {doc.record_id}',
                f'Source version: {doc.source_version}']
    digest = 'SOURCE SHA-256: ' + doc.source_digest
    text = '\n'.join([*headings, doc.title, doc.watermark, disclaimer, *metadata,
                     digest, 'Page 0123456789', *[s for pair in doc.fields for s in pair]])
    # The existing synthetic-only reporting lifecycle is ASCII-only. Do not
    # expand it here, even though the shared font helper supports more glyphs.
    if not '\n'.join([doc.title, doc.watermark, *[v for pair in doc.fields for v in pair]]).isascii():
        raise PrintDenied('Unsupported staged radiology document characters')
    font = _lab_unicode_font(text)
    row = ParagraphStyle('l04_row', fontName=font, fontSize=10, leading=14,
                         spaceAfter=3, splitLongWords=1)
    title = ParagraphStyle('l04_title', parent=row, fontSize=13, leading=18,
                           textColor=colors.HexColor('#17354A'), spaceAfter=7)
    note = ParagraphStyle('l04_note', parent=row, fontSize=9, leading=13,
                          textColor=colors.HexColor('#842020'), spaceAfter=8)
    def para(value, style=row):
        return Paragraph(escape(value), style)
    story = [*[para(line, title if i < 2 else row) for i, line in enumerate(headings)], para(doc.title, title), para(doc.watermark, note),
             *[para(v) for v in metadata], Spacer(1, 8)]
    for key, value in doc.fields:
        if key != 'Report Text':
            story.append(para(key + ': ' + value))
            continue
        story.append(para(key + ':', title))
        for line in value.split('\n'):
            # A long line may split across pages; never keep an entire report
            # together, truncate it, or interpret source markup.
            story.append(para(line) if line.strip() else Spacer(1, 7))
    story.extend([Spacer(1, 8), para(digest, note), para(disclaimer, note)])
    stream = BytesIO()
    def footer(canvas, document):
        canvas.saveState()
        _brand_logo(canvas, branding)
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor('#842020'))
        canvas.drawString(45, 34, doc.watermark)
        canvas.drawRightString(A4[0]-45, 21, f'Page {document.page}')
        canvas.restoreState()
    try:
        SimpleDocTemplate(stream, pagesize=A4, rightMargin=45, leftMargin=45,
                          topMargin=111 if branding is not None and branding.rgb is not None else 48, bottomMargin=56, pageCompression=1).build(
                              story, onFirstPage=footer, onLaterPages=footer)
    except Exception as exc:
        raise PrintDenied('Print renderer failed safely') from exc
    result = stream.getvalue()
    if not result.startswith(b'%PDF-') or not result.rstrip().endswith(b'%%EOF') or len(result)>3*1024*1024:
        raise PrintDenied('PDF output exceeded safety bounds')
    return result


def render_pdf(doc: PrintDocument, *, branding=None) -> bytes:
    if not isinstance(doc, PrintDocument) or doc.is_signed or doc.released_to_chart:
        raise PrintDenied('Unsigned print policy required')
    if branding is not None and (not isinstance(branding, PrintBranding) or branding.clinic_id != doc.clinic_id):
        raise PrintDenied('Branding ownership mismatch')
    if branding is not None and doc.kind in ('released_lab_result', 'staged_radiology_copy'):
        from .professional_layout import render_professional
        return render_professional(doc, branding)
    if doc.kind == 'released_lab_result':
        return _render_released_lab(doc, branding)
    if doc.kind == 'staged_radiology_copy':
        return _render_staged_radiology(doc, branding)
    if branding is not None:
        raise PrintDenied('Staff branding requires Rust renderer')
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

    stream = BytesIO()
    def annotate(canvas, _):
        canvas.saveState()
        canvas.setFillColor(colors.Color(.72, .18, .18, alpha=.15))
        canvas.setFont('Helvetica-Bold', 9)
        canvas.translate(65, 65)
        canvas.rotate(37)
        canvas.drawString(0, 0, doc.watermark[:100])
        canvas.restoreState()
    styles=getSampleStyleSheet()
    note=ParagraphStyle('rad6_note', parent=styles['Normal'], fontSize=9,
                        textColor=colors.HexColor('#842020'), spaceBefore=12, spaceAfter=12)
    row=ParagraphStyle('rad6_field', parent=styles['Normal'], fontSize=9, leading=13,
                       spaceAfter=8, wordWrap='CJK')
    story=[Paragraph('CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY',styles['Heading2']),
           Paragraph(escape(doc.title),styles['Heading3']),
           Paragraph(escape(doc.watermark),note),
           Paragraph(f'Clinic reference: {doc.clinic_id} &nbsp;&nbsp; Record reference: {doc.record_id}',row),
           Paragraph('Source version: '+(str(doc.source_version) if doc.source_version else 'UNVERSIONED LIVE SNAPSHOT'),row)]
    for key,value in doc.fields:
        # Escape *all* server text, preventing PDF markup or arbitrary URLs.
        text=escape(value).replace('\n','<br/>')
        story.append(Paragraph('<b>'+escape(key)+':</b> '+text,row))
    story.extend([Spacer(1,12),Paragraph('SOURCE SHA-256: '+doc.source_digest,note),
                  Paragraph('This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.',note)])
    try:
        SimpleDocTemplate(stream,pagesize=A4,rightMargin=45,leftMargin=45,
            topMargin=48,bottomMargin=48,pageCompression=0).build(
            story,onFirstPage=annotate,onLaterPages=annotate)
    except Exception as exc:
        raise PrintDenied('Print renderer failed safely') from exc
    value=stream.getvalue()
    if not value.startswith(b'%PDF-') or len(value)>3*1024*1024:
        raise PrintDenied('PDF output exceeded safety bounds')
    return value
