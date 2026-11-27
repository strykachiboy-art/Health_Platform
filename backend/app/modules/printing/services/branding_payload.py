"""One staged branding envelope for already-authorized print snapshots."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import unicodedata
from flask import current_app
from PIL import Image
from app.core.exceptions import DomainError
from .print_policy import PrintDenied, PrintDocument


@dataclass(frozen=True)
class PrintBranding:
    clinic_id: int
    lines: tuple[str, ...]
    branding_sha256: str
    logo_sha256: str | None
    logo_source: str
    organization_version: int
    clinic_version: int
    rgb: bytes | None
    width: int = 0
    height: int = 0
    primary_color: str = "#17354A"
    secondary_color: str = "#475569"
    lab_rows: tuple | None = None
    correction: str | None = None

    def wire(self):
        return {'clinic_id': self.clinic_id, 'lines': list(self.lines),
                'branding_sha256': self.branding_sha256, 'primary_color': self.primary_color, 'secondary_color': self.secondary_color,
                'logo': None if self.rgb is None else {'width': self.width, 'height': self.height, 'rgb_hex': self.rgb.hex()}}

    def png(self):
        if self.rgb is None:
            return None
        stream = BytesIO()
        Image.frombytes('RGB', (self.width, self.height), self.rgb).save(stream, format='PNG')
        return stream.getvalue()


def _line(label, value, required=False):
    if value is None or value == '':
        if required:
            raise PrintDenied('Branding identity unavailable')
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 255:
        raise PrintDenied('Invalid branding identity')
    # Identity is single-line, bounded and must never spoof a document label.
    if any(ord(c)>0xffff or unicodedata.category(c)[0] in ('C','M') or unicodedata.bidirectional(c) in ('R','AL','AN') for c in value):
        raise PrintDenied('Unsupported branding identity')
    return label + ': ' + value


def prepare_branding(doc, *, owning_clinic_id):
    if not isinstance(doc, PrintDocument) or type(owning_clinic_id) is not int or doc.clinic_id != owning_clinic_id:
        raise PrintDenied('Print ownership mismatch')
    if current_app.config.get('PRINT_BRANDING_STAGED_ENABLED') is not True:
        return None
    if not current_app.testing:
        raise PrintDenied('Branding unavailable')
    from app.modules.clinic.services.branding_service import resolve_branding
    try:
        snap = resolve_branding(owning_clinic_id=owning_clinic_id)
    except DomainError as exc:
        raise PrintDenied('Branding unavailable') from exc
    m = snap.metadata
    if m['clinic_id'] != owning_clinic_id:
        raise PrintDenied('Branding ownership mismatch')
    lines = tuple(line for line in (
        _line('Organisation', m['organization_name'], True),
        _line('Clinic', m['clinic_name'], True),
        _line('Address', m['address']), _line('City', m['city']),
        _line('Country', m['country']), _line('Phone', m['phone']),
        _line('Email', m['email'])) if line is not None)
    rgb = None; w = h = 0
    if snap.logo_png is not None:
        try:
            with Image.open(BytesIO(snap.logo_png)) as im:
                if im.format != 'PNG' or not all(1 <= n <= 512 for n in im.size) or getattr(im,'n_frames',1)!=1:
                    raise ValueError('Invalid canonical PNG')
                im.load()
                rgba = im.convert('RGBA')
                rgba.thumbnail((256,256), Image.Resampling.LANCZOS)
                white = Image.new('RGBA', rgba.size, 'white')
                white.alpha_composite(rgba)
                rgb = white.convert('RGB').tobytes(); w,h = white.size
        except Exception as exc:
            raise PrintDenied('Branding asset unavailable') from exc
    return PrintBranding(owning_clinic_id, lines, snap.branding_sha256, m['logo_sha256'], m['logo_source'],
        m['organization_version'], m['clinic_version'], rgb, w, h, m['primary_color'], m['secondary_color'])


def brand_lines(branding):
    if branding is None:
        return ('CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY',)
    return (*branding.lines, 'CONTROLLED DOCUMENT COPY', 'BRANDING SHA-256: '+branding.branding_sha256)


def render_authorized(doc, renderer, *, owning_clinic_id, lab_rows=None, correction=None):
    branding = prepare_branding(doc, owning_clinic_id=owning_clinic_id)
    if branding is None:
        return renderer(doc), {}
    if lab_rows is not None:
        from dataclasses import replace
        from .polished_patient import canonical_lab_text
        if doc.kind != 'released_lab_result' or canonical_lab_text(lab_rows, correction) != dict(doc.fields)['Report Text']:
            raise PrintDenied('Structured laboratory source mismatch')
        branding = replace(branding, lab_rows=lab_rows, correction=correction)
    data = renderer(doc, branding=branding)
    if not isinstance(data, bytes) or not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF') or len(data)>3*1024*1024:
        raise PrintDenied('Print renderer unavailable')
    return data, {'branding_sha256': branding.branding_sha256, 'logo_sha256': branding.logo_sha256,
                  'logo_source': branding.logo_source, 'organization_branding_version': branding.organization_version,
                  'clinic_branding_version': branding.clinic_version, 'pdf_sha256': sha256(data).hexdigest()}
