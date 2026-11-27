"""Professional staged patient-copy templates. No clinical interpretation."""
import json
from html import escape
from io import BytesIO
from .print_policy import PrintDenied


def canonical_lab_text(rows, correction=None):
    """The exact existing L03 source serialization, including its trailing newline."""
    if not isinstance(rows, tuple) or len(rows) > 100:
        raise PrintDenied('Invalid structured laboratory rows')
    lines = []
    if correction is not None:
        if not isinstance(correction, str):
            raise PrintDenied('Invalid correction evidence')
        lines.append('Correction verified at: ' + correction)
    for i, row in enumerate(rows, 1):
        if not isinstance(row, tuple) or len(row) != 7 or any(not isinstance(v,str) for v in row):
            raise PrintDenied('Invalid structured laboratory row')
        name, loinc, result, unit, bounds, flag, recorded = row
        lines.extend([f'{i}. Test: {name}', f'LOINC: {loinc}', f'Result: {result}   Unit: {unit}',
                      f'Reference range: {bounds}   Flag: {flag}', f'Recorded at: {recorded}', ''])
    return '\n'.join(lines)


def display_lab_value(value):
    """Remove JSON serialization quotes only for a printable nonblank string.

    Keep encoded control sequences visible instead of injecting layout controls.
    The canonical source text and digest never change.
    """
    import unicodedata
    try:
        decoded=json.loads(value)
    except (TypeError,ValueError):
        return value
    if isinstance(decoded,str) and decoded.strip() and not any(unicodedata.category(c)[0] in ('C','M') or unicodedata.bidirectional(c) in ('R','AL','AN') or ord(c)>0xffff for c in decoded):
        return decoded
    return value


def render_polished_patient(doc, branding, font_for):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, KeepTogether
    from app.modules.clinic.services.branding_service import validate_palette
    from app.core.exceptions import DomainError
    try:
        palette = validate_palette(branding.primary_color, branding.secondary_color)
        if palette[0] is None or palette != (branding.primary_color, branding.secondary_color):
            raise PrintDenied('Noncanonical print palette')
        primary, secondary = map(colors.HexColor, palette)
    except DomainError as exc:
        raise PrintDenied('Invalid print palette') from exc
    if doc.kind == 'staged_radiology_copy' and not '\n'.join([doc.title,doc.watermark,*[s for pair in doc.fields for s in pair]]).isascii():
        raise PrintDenied('Unsupported staged radiology document characters')
    disclaimer = 'This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.'
    refs = f'Clinic reference: {doc.clinic_id}   Record reference: {doc.record_id}'
    version = f'Source version: {doc.source_version}'
    digest = 'SOURCE SHA-256: ' + doc.source_digest
    brand_digest = 'BRANDING SHA-256: ' + branding.branding_sha256
    headings = branding.lines
    fixed = 'CONTROLLED DOCUMENT COPY Laboratory Results Test / LOINC Result Unit Reference range Flag Recorded at Page 0123456789'
    displayed = [display_lab_value(v) for row in (branding.lab_rows or ()) for v in row]
    font = font_for('\n'.join([*headings,doc.title,doc.watermark,disclaimer,refs,version,digest,brand_digest,fixed,*displayed,*[s for pair in doc.fields for s in pair]]))
    W,H=A4; left=45; right=W-45; width=right-left; body_width=width-12
    ink=colors.HexColor('#18252F'); warn=colors.HexColor('#842020')
    line=colors.HexColor('#D7E0E5'); pale=colors.HexColor('#F0F3F5')
    def style(name, fs, lead, color=ink, **kw):
        return ParagraphStyle(name,fontName=font,fontSize=fs,leading=lead,textColor=color,splitLongWords=1,**kw)
    body=style('pbody',10,14,spaceAfter=4)
    small=style('psmall',8,11,spaceAfter=3)
    identity=style('pidentity',15,19,primary)
    clinic=style('pclinic',11,15,ink)
    contact=style('pcontact',8,11,secondary)
    title=style('ptitle',22,27,primary,spaceAfter=8)
    section=style('psection',12,16,secondary,spaceAfter=6)
    warning=style('pwarning',9,12,warn,spaceAfter=6)
    head=style('phead',9,12,colors.white)
    cell=style('pcell',9,12,ink)
    alert_cell=style('palert',9,12,warn)
    def para(v, st=body): return Paragraph(escape(v).replace('\n','<br/>'),st)
    # Identity is repeated in full, with measured variable-height reserved space.
    has_logo=branding.rgb is not None
    x=left+104 if has_logo else left
    hw=right-x
    hp=[para(headings[0],identity),para(headings[1],clinic)]
    if headings[2:]: hp.append(para('  |  '.join(headings[2:]),contact))
    hh=sum(p.wrap(hw,1000)[1]+3 for p in hp)
    reserve=max(hh,54 if has_logo else 0)
    top=36+reserve+22
    if top>300: raise PrintDenied('Branding header exceeds page bounds')
    story=[para('Laboratory Results' if doc.kind=='released_lab_result' else doc.title,title), *([para(doc.title,small)] if doc.kind=='released_lab_result' else []),para('CONTROLLED DOCUMENT COPY',section),para(doc.watermark,warning),para(refs,small),para(version,small),Spacer(1,9)]
    report_text=dict(doc.fields)['Report Text']
    for key,value in doc.fields:
        if key!='Report Text': story.append(para(key+': '+value))
    story.append(Spacer(1,6));story.append(para('Report Text:',section))
    if doc.kind=='released_lab_result' and branding.lab_rows is not None:
        if canonical_lab_text(branding.lab_rows,branding.correction)!=report_text:
            raise PrintDenied('Structured laboratory source mismatch')
        if branding.correction is not None:
            story.append(para('Correction verified at: '+display_lab_value(branding.correction)))
        # Keep each source value and its qualifiers, including literal JSON quoting,
        # LOINC and recorded time. No inference of abnormal flags.
        rows=[[para(v,head) for v in ('Test / LOINC / Recorded at','Result','Unit','Reference range','Flag')]]
        for i,(name,loinc,result,unit,bounds,flag,recorded) in enumerate(branding.lab_rows,1):
            name,loinc,result,unit,bounds,flag,recorded=map(display_lab_value,(name,loinc,result,unit,bounds,flag,recorded))
            flag_style=alert_cell if flag.strip().casefold() in ('high','low','critical','critical high','critical low','abnormal','h','l','hh','ll') else cell
            rows.append([para(f'{i}. Test: {name}\nLOINC: {loinc}\nRecorded at: {recorded}',cell),para(result,cell),para(unit,cell),para(bounds,cell),para(flag,flag_style)])
        if len(rows)>1:
            table=LongTable(rows,colWidths=[body_width*x for x in (.33,.16,.13,.23,.15)],repeatRows=1,splitByRow=1,splitInRow=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),primary),('ROWBACKGROUNDS',(0,1),(-1,-1),[pale,colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),1,secondary),('LINEBELOW',(0,1),(-1,-1),.4,line)]))
            story.append(table)
    elif doc.kind=='released_lab_result':
        # Historical internal report-text fixtures remain supported without guessing columns.
        for block in report_text.split('\n\n'):
            if block:
                story.append(KeepTogether([*[para(v) for v in block.split('\n') if v],Spacer(1,6)]))
    else:
        # The staged radiology source remains verbatim ASCII clinical text.
        for v in report_text.split('\n'):
            story.append(para(v) if v.strip() else Spacer(1,7))
    story.extend([Spacer(1,12),para(digest,small),para(brand_digest,small),para(disclaimer,warning)])
    stream=BytesIO()
    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFillColor(primary);canvas.rect(left,H-26,width,4,fill=1,stroke=0)
        if has_logo:
            scale=min(88/branding.width,50/branding.height);w=branding.width*scale;h=branding.height*scale
            canvas.drawImage(ImageReader(BytesIO(branding.png())),left,H-36-h,width=w,height=h)
        y=H-36
        for p in hp:
            _,h=p.wrap(hw,1000);p.drawOn(canvas,x,y-h);y-=h+3
        canvas.setStrokeColor(secondary);canvas.line(left,H-top+10,right,H-top+10)
        canvas.setStrokeColor(line);canvas.line(left,47,right,47)
        canvas.setFont(font,8);canvas.setFillColor(warn)
        canvas.drawString(left,34,doc.watermark)
        canvas.setFillColor(ink);canvas.drawString(left,21,f'Clinic reference: {doc.clinic_id} | Record reference: {doc.record_id}')
        canvas.drawRightString(right,21,f'Page {document.page}')
        canvas.restoreState()
    try:
        SimpleDocTemplate(stream,pagesize=A4,leftMargin=45,rightMargin=45,topMargin=top,bottomMargin=59,pageCompression=1).build(story,onFirstPage=decorate,onLaterPages=decorate)
    except Exception as exc:
        raise PrintDenied('Polished patient renderer failed safely') from exc
    result=stream.getvalue()
    if not result.startswith(b'%PDF-') or not result.rstrip().endswith(b'%%EOF') or len(result)>3*1024*1024:
        raise PrintDenied('PDF output exceeded safety bounds')
    return result
