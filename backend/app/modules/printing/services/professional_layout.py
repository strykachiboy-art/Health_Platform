"""Measured A4 presentation for authorized staged documents. No database access.
The same page plan is intended for native Rust and ReportLab painting.
No caller-supplied layout, HTML, asset path, or clinical interpretation.
"""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import RLock
import json
import os
import re
import unicodedata
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from .print_policy import PrintDenied, PrintDocument, build_document, ordered_test_rows

W, H = 595.2756, 841.8898
L, R = 45., W-45.
WIDTH = R-L
INK, GRAY, RULE, PALE, WARN, WARN_BG = '#142A3A','#536575','#D6DFE6','#F1F5F7','#842020','#FCF2F1'
DISCLAIMER = 'This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.'
FOOT_TOP = 132.
_LOCK = RLock()

@dataclass(frozen=True)
class Faces:
    family: str
    regular: str
    bold: str
    paths: tuple


def _font_options():
    if os.name == 'nt':
        root = Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'
        return [('DejaVu',root/'DejaVuSans.ttf',root/'DejaVuSans-Bold.ttf'),('Arial',root/'arial.ttf',root/'arialbd.ttf'),('Segoe',root/'segoeui.ttf',root/'segoeuib.ttf')]
    root=Path('/usr/share/fonts/truetype/dejavu')
    return [('DejaVu',root/'DejaVuSans.ttf',root/'DejaVuSans-Bold.ttf')]


def faces_for(text):
    for c in text:
        if c in '\n\t': continue
        if ord(c)>0xffff or unicodedata.category(c)[0] in ('C','M') or unicodedata.bidirectional(c) in ('R','AL','AN'):
            raise PrintDenied('Unsupported presentation character')
    for family,regular,bold in _font_options():
        if not regular.is_file() or not bold.is_file(): continue
        try:
            names=[]
            with _LOCK:
                for role,path in [('Regular',regular),('Bold',bold)]:
                    name='CSP_R2C_'+family+'_'+role
                    if name not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont(name,str(path)))
                    names.append(name)
            if all(c in '\n\t' or all(pdfmetrics.getFont(n).face.charToGlyph.get(ord(c),0) for n in names) for c in text):
                return Faces(family,*names,(regular,bold))
        except Exception:
            continue
    raise PrintDenied('Professional font pair unavailable')


def _contrast(c):
    if not isinstance(c,str) or not re.fullmatch(r'#[0-9A-F]{6}',c): raise PrintDenied('Noncanonical palette')
    def f(i):
        n=int(c[i:i+2],16)/255
        return n/12.92 if n<=.04045 else ((n+.055)/1.055)**2.4
    if 1.05/(.2126*f(1)+.7152*f(3)+.0722*f(5)+.05)<4.5: raise PrintDenied('Unsafe palette')
    return c


def _norm(s): return ''.join(c for c in s if not c.isspace())


def _wrap(s, width, font, size):
    # Wrap literal text without markup or hyphen insertion. Every non-space
    # codepoint is preserved. Long unbroken identifiers split by character.
    result=[]
    for raw in str(s).split('\n'):
        raw=raw.replace('\t','    ')
        if not raw.strip(): result.append('');continue
        line=''
        for token in re.findall(r'\s+|\S+',raw):
            if not token.strip():
                if line: line+=' '
                continue
            if pdfmetrics.stringWidth(line+token,font,size)<=width:
                line+=token;continue
            if line.strip(): result.append(line.rstrip());line=''
            while pdfmetrics.stringWidth(token,font,size)>width:
                cut=0;used=0.
                for ch in token:
                    step=pdfmetrics.stringWidth(ch,font,size)
                    if used+step>width: break
                    cut+=1;used+=step
                if not cut: raise PrintDenied('Presentation glyph exceeds column')
                result.append(token[:cut]);token=token[cut:]
            line=token
        if line.strip(): result.append(line.rstrip())
    if _norm(''.join(result))!=_norm(str(s)): raise PrintDenied('Presentation text mismatch')
    return result or ['']


class Layout:
    def __init__(self, doc, branding, faces):
        self.doc=doc;self.b=branding;self.faces=faces;self.pages=[]
        self.enriched = "Patient Name" in dict(doc.fields)
        self.verification = branding.verification_url is not None
        self.primary=_contrast(branding.primary_color);self.secondary=_contrast(branding.secondary_color)
        self.logo=None
        if branding.rgb is not None:
            if not (1<=branding.width<=256 and 1<=branding.height<=256 and len(branding.rgb)==branding.width*branding.height*3): raise PrintDenied('Invalid logo pixels')
            scale=min(85/branding.width,48/branding.height)
            self.logo=(L,58.,branding.width*scale,branding.height*scale)
        self.new_page()
    def font(self,bold): return self.faces.bold if bold else self.faces.regular
    def lines(self,s,width,size,bold=False): return _wrap(s,width,self.font(bold),size)
    def text(self,s,x,top,size=10,bold=False,color=INK,tag='body',align='left',width=None):
        if not s: return
        length=pdfmetrics.stringWidth(s,self.font(bold),size)
        if align=='right': x=x+width-length
        # top-down baseline coordinates; generous leading below the baseline.
        baseline=top+size
        if not (L-.1<=x and x+length<=R+.1 and 22<=baseline<=H-18): raise PrintDenied('Presentation text out of bounds')
        self.page['text'].append(dict(text=s,x=round(x,4),y=round(baseline,4),size=size,bold=bold,color=color,tag=tag))
    def rect(self,x,top,w,h,color):
        if not (L-.1<=x and x+w<=R+.1 and 22<=top and top+h<=H-18 and w>0 and h>0):raise PrintDenied('Presentation box out of bounds')
        self.page['rects'].append(dict(x=round(x,4),y=round(top,4),w=round(w,4),h=round(h,4),color=color))
    def paragraph(self,s,x,top,width,size=10,bold=False,color=INK,tag='body',lead=None):
        lead=lead or size*1.42
        for line in self.lines(s,width,size,bold):
            self.text(line,x,top,size,bold,color,tag);top+=lead
        return top
    def new_page(self):
        if len(self.pages)>=100: raise PrintDenied('Too many presentation pages')
        self.page={'text':[],'rects':[],'logo':self.logo};self.pages.append(self.page)
        self.rect(L,45,WIDTH,4,self.primary)
        x=L+104 if self.logo else L;hw=R-x;top=58.
        top=self.paragraph(self.b.lines[0].removeprefix('Organisation: '),x,top,hw,17 if len(self.pages)==1 else 14,True,self.primary,'header')+2
        top=self.paragraph(self.b.lines[1].removeprefix('Clinic: '),x,top,hw,11,True,INK,'header')+2
        if self.b.lines[2:]:
            top=self.paragraph('  |  '.join(self.b.lines[2:]),x,top,hw,8,False,GRAY,'header',11)
        compact=self.enriched and len(self.pages)>1
        top=max(top,108 if compact and self.logo else 112 if self.logo else 82 if compact else 88)+(5 if compact else 10)
        self.rect(L,top,WIDTH,.8,RULE);top+=8 if compact else 14
        title='Laboratory Results' if self.doc.kind=='released_lab_result' else self.doc.title
        title_size=25 if len(self.pages)==1 else 12 if compact else 14
        top=self.paragraph(title,L,top,WIDTH,title_size,True,INK,'header',title_size+5)+(3 if compact else 6)
        warning=self.lines(self.doc.watermark,WIDTH-20,8,True)
        h=max(22 if compact else 28,len(warning)*11+(10 if compact else 14))
        self.rect(L,top,WIDTH,h,WARN_BG)
        self.paragraph(self.doc.watermark,L+10,top+(5 if compact else 7),WIDTH-20,8,True,WARN,'warning',11)
        top+=h+(6 if compact else 12)
        top=self.paragraph('CONTROLLED DOCUMENT COPY',L,top,WIDTH,8,True,GRAY,'header',11)+(4 if compact else 8)
        self.y=top;self.body_start=top
        if self.body_start>H-FOOT_TOP-145: raise PrintDenied('Letterhead exceeds printable page')
    def ensure(self,h):
        if self.y+h>H-FOOT_TOP-10:self.new_page()
        if self.y+h>H-FOOT_TOP-10:raise PrintDenied('Atomic presentation block exceeds page')
    def grid(self, pairs):
        gap=24.;cw=(WIDTH-gap)/2
        for i in range(0,len(pairs),2):
            values=pairs[i:i+2];heights=[len(self.lines(k,cw,8,True))*11+3+len(self.lines(v,cw,11,k=="Patient Name"))*15+9 for k,v in values]
            self.ensure(max(heights))
            for j,(k,v) in enumerate(values):
                x=L+j*(cw+gap)
                yy=self.paragraph(k,x,self.y,cw,8,True,GRAY,lead=11)+3
                self.paragraph(v,x,yy,cw,11,k=="Patient Name",INK,lead=15)
            self.y+=max(heights)
    def section(self,label):
        self.ensure(35);self.y=self.paragraph(label,L,self.y,WIDTH,11,True,self.secondary,lead=15)+5
        self.rect(L,self.y,WIDTH,.7,RULE);self.y+=10
    def table(self,headers,rows,fractions,numeric=(),alerts=(),reserve_last=0):
        widths=[WIDTH*f for f in fractions];pad=5. if self.enriched else 7.;lead=12. if self.enriched else 13.;font_size=9
        def measure(row,bold):return max(len(self.lines(v,w-2*pad,font_size,bold)) for v,w in zip(row,widths))*lead+2*pad
        def draw(row,h,bold,bg,tag):
            self.rect(L,self.y,WIDTH,h,bg);x=L
            for i,(v,w) in enumerate(zip(row,widths)):
                col='#FFFFFF' if bold else (WARN if i in alerts and v.strip().casefold() in ('high','low','critical','critical high','critical low','abnormal','h','l','hh','ll') else INK)
                lines=self.lines(v,w-2*pad,font_size,bold)
                for j,line in enumerate(lines): self.text(line,x+pad,self.y+pad+j*lead,font_size,bold,col,tag,'right' if i in numeric else 'left',w-2*pad)
                x+=w
            self.y+=h
        header_h=measure(headers,True);first=True
        for i,row in enumerate(rows):
            if len(row)!=len(headers):raise PrintDenied('Invalid presentation row')
            h=measure(row,False)
            reserve=reserve_last if i==len(rows)-1 else 0
            if self.y+h+reserve+(header_h if first else 0)>H-FOOT_TOP-10:
                self.new_page();first=True
            self.ensure(h+reserve+(header_h if first else 0))
            if first:draw(headers,header_h,True,self.primary,'table_header');first=False
            draw(row,h,False,PALE if i%2==0 else '#FFFFFF','body')
        self.y+=12
    def block(self,text):
        for line in self.lines(text,WIDTH,11):
            self.ensure(16);self.text(line,L,self.y,11);self.y+=16
    def balance(self,value):
        lines=self.lines(value,WIDTH-28,20,True);h=32+len(lines)*26+10
        self.ensure(h);self.rect(L,self.y,WIDTH,h,PALE)
        self.text('Balance Due' if self.enriched else 'Outstanding Balance',L+14,self.y+10,9,True,GRAY)
        color = WARN if self.enriched and not value.endswith(' 0.00') else self.primary
        self.paragraph(value,L+14,self.y+28,WIDTH-28,20,True,color,lead=26);self.y+=h+10
    def qr(self, url):
        from .verification_qr import rows_for
        matrix=rows_for(url)
        size=84.;step=size/len(matrix);x=R-size;top=H-FOOT_TOP+12
        self.rect(x,top,size,size,'#FFFFFF')
        for i,row in enumerate(matrix):
            j=0
            while j<len(row):
                if row[j]!='1':j+=1;continue
                start=j
                while j<len(row) and row[j]=='1':j+=1
                self.rect(x+start*step,top+i*step,(j-start)*step,step,INK)

    def finish(self):
        for index,page in enumerate(self.pages):
            self.page=page;top=H-FOOT_TOP
            self.rect(L,top,WIDTH,.8,RULE);top+=8
            fw=WIDTH-96 if self.verification else WIDTH
            top=self.paragraph('SOURCE SHA-256: '+self.doc.source_digest,L,top,fw,7,False,GRAY,'footer',10)+2
            top=self.paragraph('BRANDING SHA-256: '+self.b.branding_sha256,L,top,fw,7,False,GRAY,'footer',10)+4
            top=self.paragraph(DISCLAIMER,L,top,fw,7 if self.verification else 8,False,WARN,'disclaimer',9 if self.verification else 11)+(3 if self.verification else 5)
            if self.verification:
                top=self.paragraph('Generated: '+self.b.issued_at,L,top,fw,7,False,GRAY,'footer',9)
                top=self.paragraph('Verification: '+self.b.verification_url.rsplit('/',1)[1],L,top,fw,7,False,GRAY,'footer',9)
                top=self.paragraph('Powered by Stryka-Max',L,top,fw,8,True,GRAY,'footer',10)
                self.qr(self.b.verification_url)
            self.text(f'Page {index+1} of {len(self.pages)}',L,H-34,8,False,GRAY,'footer','right',WIDTH)
            if top>H-42:raise PrintDenied('Presentation footer overflow')
        return {'family':self.faces.family,'pages':self.pages}


def make_plan(doc,branding):
    from .polished_patient import canonical_lab_text, display_lab_value
    if not isinstance(doc,PrintDocument) or doc.is_signed or doc.released_to_chart or branding.clinic_id!=doc.clinic_id:raise PrintDenied('Unauthorized presentation')
    rebuilt=build_document(kind=doc.kind,clinic_id=doc.clinic_id,record_id=doc.record_id,source_version=doc.source_version,source={k.lower().replace(' ','_'):v for k,v in doc.fields})
    if rebuilt!=doc:raise PrintDenied('Canonical source mismatch')
    if doc.kind=='staged_radiology_copy' and not ('\n'.join([doc.title,doc.watermark,*[s for pair in doc.fields for s in pair]]) if 'Patient Name' not in dict(doc.fields) else dict(doc.fields)['Report Text']).isascii():raise PrintDenied('Unsupported staged radiology document characters')
    if len(branding.lines)<2 or len(branding.lines)>7 or not re.fullmatch('[0-9a-f]{64}',branding.branding_sha256):raise PrintDenied('Invalid branding identity')
    labels=('Organisation: ','Clinic: ','Address: ','City: ','Country: ','Phone: ','Email: ')
    positions=[]
    for line in branding.lines:
        matches=[i for i,label in enumerate(labels) if line.startswith(label) and line[len(label):].strip()]
        if len(matches)!=1 or any(c in line for c in ('\n','\t')):raise PrintDenied('Invalid branding line')
        positions+=matches
    if positions[:2]!=[0,1] or positions!=sorted(set(positions)):raise PrintDenied('Invalid branding identity order')
    vals=dict(doc.fields)
    if branding.lab_rows is not None:
        if doc.kind!='released_lab_result' or canonical_lab_text(branding.lab_rows,branding.correction)!=vals.get('Report Text'):raise PrintDenied('Structured laboratory source mismatch')
    if (branding.verification_url is None) != (branding.issued_at is None):raise PrintDenied('Incomplete verification metadata')
    if branding.verification_url is not None and (not re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::[0-9]{1,5})?/api/v1/printing/verify/[0-9a-f]{32}',branding.verification_url) or not isinstance(branding.issued_at,str) or len(branding.issued_at)>100 or any(ord(c)<32 for c in branding.issued_at)):raise PrintDenied('Invalid verification metadata')
    text='\n'.join([branding.issued_at or '', branding.verification_url or '', 'Generated: Verification: Powered by Stryka-Max', *branding.lines,doc.title,doc.watermark,DISCLAIMER,doc.source_digest,branding.branding_sha256,*[s for pair in doc.fields for s in pair],*[display_lab_value(v) for row in (branding.lab_rows or ()) for v in row], 'Laboratory Results Clinic reference Record reference Source version CONTROLLED DOCUMENT COPY SOURCE SHA-256 BRANDING SHA-256 Description Quantity Unit price Subtotal Test LOINC Recorded at Result Unit Reference range Flag Page of 0123456789 No itemized charges recorded Ordered Tests Specimen Priority Balance Due , |'])
    faces=faces_for(text);p=Layout(doc,branding,faces)
    pairs=[(k,v) for k,v in doc.fields if k not in ('Invoice Items','Outstanding Balance','Report Text','Ordered Tests')]
    if p.enriched:
        pairs.sort(key=lambda pair: 0 if pair[0]=='Patient Name' else 1 if pair[0]=='Patient Id' else 2)
    p.grid(pairs+[('Clinic reference',str(doc.clinic_id)),('Record reference',str(doc.record_id)),('Source version',str(doc.source_version))])
    if 'Invoice Items' in vals:
        p.section('Invoice Items')
        rows=json.loads(vals['Invoice Items'])
        if rows:p.table(('Description','Quantity','Unit price','Subtotal'),rows,(.49,.13,.19,.19),numeric=(1,2,3),reserve_last=90 if p.enriched else 0)
        else:p.block('No itemized charges recorded')
        amount=vals['Outstanding Balance']
        if p.enriched:
            from decimal import Decimal
            amount=format(Decimal(amount),',.2f')
        p.balance(vals['Currency']+' '+amount)
    if 'Ordered Tests' in vals:
        p.section('Ordered Tests')
        p.table(('Test','Specimen','Priority'),ordered_test_rows(vals['Ordered Tests']),(.54,.26,.20))
    if 'Report Text' in vals:
        p.section('Report Text')
        if doc.kind=='released_lab_result' and branding.lab_rows is not None:
            if branding.correction is not None:p.block('Correction verified at: '+display_lab_value(branding.correction))
            rows=[]
            for i,(name,loinc,result,unit,bounds,flag,recorded) in enumerate(branding.lab_rows,1):
                name,loinc,result,unit,bounds,flag,recorded=map(display_lab_value,(name,loinc,result,unit,bounds,flag,recorded))
                rows.append((f'{i}. Test: {name}\nLOINC: {loinc}\nRecorded at: {recorded}',result,unit,bounds,flag))
            if rows:p.table(('Test / LOINC / Recorded at','Result','Unit','Reference range','Flag'),rows,(.33,.16,.13,.23,.15),alerts=(4,))
        else:p.block(vals['Report Text'])
    return p.finish(),faces


def paint_reportlab(plan,faces,branding):
    """Exact coordinates, not a second template engine."""
    out=BytesIO();c=canvas.Canvas(out,pagesize=(W,H),pageCompression=1)
    c.setTitle('Controlled document copy')
    image=ImageReader(BytesIO(branding.png())) if branding.rgb is not None else None
    for page in plan['pages']:
        for r in page['rects']:
            c.setFillColor(HexColor(r['color']));c.rect(r['x'],H-r['y']-r['h'],r['w'],r['h'],stroke=0,fill=1)
        if page['logo'] is not None:
            x,y,w,h=page['logo'];c.drawImage(image,x,H-y-h,w,h)
        for t in page['text']:
            c.setFillColor(HexColor(t['color']));c.setFont(faces.bold if t['bold'] else faces.regular,t['size']);c.drawString(t['x'],H-t['y'],t['text'])
        c.showPage()
    c.save();data=out.getvalue()
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF') or len(data)>3*1024*1024:raise PrintDenied('Presentation exceeds PDF bounds')
    return data


def render_professional(doc, branding):
    plan,faces=make_plan(doc,branding)
    return paint_reportlab(plan,faces,branding)
