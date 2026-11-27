"""Staged logo settings, independent of clinical access and subscription billing."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from types import MappingProxyType
from typing import Mapping

from flask import current_app
from sqlalchemy import select
from PIL import Image, ImageOps
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.audit_enums import AuditAction
from app.core.audit.services.audit_service import create_audit_log
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.models.organization_model import HospitalOrganization
from app.modules.clinic.models.branding_model import OrganizationBranding, ClinicBranding
from app.modules.clinic.services.tenant_boundary import (
    TenantAccessDenied, _parse_organization_id, require_tenant_membership,
    require_tenant_clinic_management,
)

MAX_UPLOAD = 2 * 1024 * 1024
MAX_EDGE = 2048
NORMALIZED_EDGE = 512


def _enabled():
    if not current_app.testing or current_app.config.get('PRINT_BRANDING_STAGED_ENABLED') is not True:
        raise NotFoundError('Branding unavailable')


def _deny():
    raise NotFoundError('Branding unavailable')


def normalize_logo(raw, mime):
    """Decode exactly one PNG/JPEG, strip metadata, bound decoded and output size."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_UPLOAD or mime not in ('image/png', 'image/jpeg'):
        raise ValidationError('Invalid logo upload')
    expected = {'image/png': 'PNG', 'image/jpeg': 'JPEG'}[mime]
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.format != expected or getattr(im, 'n_frames', 1) != 1 or not all(1 <= n <= MAX_EDGE for n in im.size):
                raise ValueError('Unsupported image')
            im.verify()
        with Image.open(BytesIO(raw)) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert('RGBA')
            im.thumbnail((NORMALIZED_EDGE, NORMALIZED_EDGE), Image.Resampling.LANCZOS)
            # A fresh image avoids copying source EXIF, ICC, comments or text chunks.
            clean = Image.new('RGBA', im.size)
            clean.paste(im)
            out = BytesIO()
            clean.save(out, format='PNG', optimize=True)
            result = out.getvalue()
        if not 0 < len(result) <= MAX_UPLOAD:
            raise ValueError('Output too large')
        return result
    except Exception as exc:
        raise ValidationError('Invalid or unsupported logo image') from exc


def _org(oid):
    try:
        oid = _parse_organization_id(oid)
    except TenantAccessDenied:
        _deny()
    # Serialize all changes under the existing parent, including first creation.
    org = db.session.execute(select(HospitalOrganization).where(HospitalOrganization.id == oid).with_for_update()).scalar_one_or_none()
    if org is None:
        _deny()
    return org


def _actor(user_id):
    if type(user_id) is not int or user_id <= 0:
        _deny()
    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        _deny()
    return user


def _authorize(user_id, org, clinic_id=None):
    user = _actor(user_id)
    if user.role is Role.SUPER_ADMIN:
        _deny()
    try:
        member = require_tenant_membership(user_id=user.id, organization_id=org.id)
        if clinic_id is None:
            if member.role != 'organization_admin':
                _deny()
        else:
            require_tenant_clinic_management(user_id=user.id, organization_id=org.id, clinic_id=clinic_id)
            clinic = db.session.get(Clinic, clinic_id)
            if clinic.status is not ClinicStatus.ACTIVE:
                _deny()
    except TenantAccessDenied:
        _deny()
    # Shared AuditLog scopes to the actor's home clinic. Until tenant audit
    # scoping is extended, do not permit cross-home-organization administration.
    if user.clinic_id is not None:
        home = db.session.get(Clinic, user.clinic_id)
        if home is None or home.organization_id != org.id:
            _deny()
    return user


def _row(org, clinic_id=None):
    model = OrganizationBranding if clinic_id is None else ClinicBranding
    q = select(model).where(model.organization_id == org.id)
    if clinic_id is not None:
        q = q.where(model.clinic_id == clinic_id)
    return db.session.execute(q).scalar_one_or_none()


def _version(row):
    return row.version if row is not None else 0


def _allowed(row):
    return row is not None and row.clinic_override_allowed is True


def _check_version(row, expected):
    if type(expected) is not int or expected < 0 or expected > 2147483646:
        raise ValidationError('Invalid branding version')
    if _version(row) != expected:
        raise ConflictError('Branding changed; refresh and retry')


def _bytes(row):
    if row is None:
        return None
    raw = row.logo_png
    if raw is None and row.logo_sha256 is None:
        return None
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_UPLOAD or sha256(raw).hexdigest() != row.logo_sha256:
        raise ConflictError('Branding asset integrity unavailable')
    # Verify stored canonical PNG again without ever following an external path.
    try:
        with Image.open(BytesIO(raw)) as im:
            if im.format != 'PNG' or getattr(im, 'n_frames', 1) != 1 or not all(1 <= n <= NORMALIZED_EDGE for n in im.size):
                raise ValueError('Invalid stored image')
            im.verify()
    except Exception as exc:
        raise ConflictError('Branding asset integrity unavailable') from exc
    return raw


def _audit(user, org, row, clinic_id, before, action):
    create_audit_log(action=AuditAction.UPDATE, entity_type='OrganizationBranding' if clinic_id is None else 'ClinicBranding',
        entity_id=row.id, user_id=user.id, description=action,
        old_value=before, new_value={'organization_id': str(org.id), 'target_clinic_id': clinic_id,
        'version': row.version, 'logo_sha256': row.logo_sha256, 'primary_color': row.primary_color, 'secondary_color': row.secondary_color,
        **({'clinic_override_allowed': row.clinic_override_allowed} if clinic_id is None else {})})



DEFAULT_PALETTE = ('#17354A', '#475569')

def validate_palette(primary, secondary):
    """Canonical uppercase hex, dark enough for white paper and white text."""
    import re
    if primary is None and secondary is None:
        return None, None
    values = []
    for value in (primary, secondary):
        if not isinstance(value, str) or re.fullmatch(r'#[0-9A-Fa-f]{6}', value) is None:
            raise ValidationError('Both branding colors must be six-digit hex values')
        rgb = [int(value[i:i+2],16)/255 for i in (1,3,5)]
        lum = sum(w*(x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4) for x,w in zip(rgb,(.2126,.7152,.0722)))
        if 1.05/(lum+.05) < 4.5:
            raise ValidationError('Branding colors need at least 4.5:1 contrast against white')
        values.append(value.upper())
    return tuple(values)


def _stored_palette(row):
    if row is None:
        return None, None
    try:
        values = validate_palette(row.primary_color,row.secondary_color)
        if values != (row.primary_color,row.secondary_color):
            raise ValidationError('Noncanonical branding colors')
        return values
    except ValidationError as exc:
        raise ConflictError('Branding palette integrity unavailable') from exc


def _effective_palette(default, override=None):
    local = _stored_palette(override) if _allowed(default) else (None,None)
    if local[0] is not None:
        return (*local, 'clinic')
    inherited = _stored_palette(default)
    if inherited[0] is not None:
        return (*inherited, 'organization')
    return (*DEFAULT_PALETTE, 'default')


@transactional
def change_colors(*, user_id, organization_id, clinic_id=None, expected_version, primary_color, secondary_color):
    _enabled()
    org = _org(organization_id)
    user = _authorize(user_id, org, clinic_id)
    palette = validate_palette(primary_color, secondary_color)
    if clinic_id is not None and palette[0] is not None and not _allowed(_row(org)):
        _deny()
    row = _row(org, clinic_id)
    _check_version(row, expected_version)
    before = _metadata(row)
    if _stored_palette(row) == palette:
        return before
    if row is None:
        row = OrganizationBranding(organization_id=org.id,version=1) if clinic_id is None else ClinicBranding(organization_id=org.id,clinic_id=clinic_id,version=1)
        db.session.add(row)
    else:
        row.version += 1
    row.primary_color, row.secondary_color = palette
    db.session.flush()
    _audit(user, org, row, clinic_id, before, 'Branding colors updated')
    return _metadata(row)


def _metadata(row):
    return {'version': _version(row), 'logo_sha256': row.logo_sha256 if row else None,
            'has_logo': _bytes(row) is not None, 'primary_color': _stored_palette(row)[0], 'secondary_color': _stored_palette(row)[1]}


@transactional
def change_logo(*, user_id, organization_id, clinic_id=None, expected_version, raw=None, mime=None):
    _enabled()
    org = _org(organization_id)
    user = _authorize(user_id, org, clinic_id)
    if clinic_id is not None and raw is not None and not _allowed(_row(org)):
        _deny()
    row = _row(org, clinic_id)
    _check_version(row, expected_version)
    before = _metadata(row)
    logo = normalize_logo(raw, mime) if raw is not None else None
    if raw is None and (row is None or row.logo_png is None):
        return before
    if row is None:
        row = OrganizationBranding(organization_id=org.id, version=1) if clinic_id is None else ClinicBranding(organization_id=org.id, clinic_id=clinic_id, version=1)
        db.session.add(row)
    else:
        row.version += 1
    row.logo_png = logo
    row.logo_sha256 = sha256(logo).hexdigest() if logo is not None else None
    db.session.flush()
    _audit(user, org, row, clinic_id, before, 'Branding logo replaced' if logo is not None else 'Branding logo cleared')
    return _metadata(row)


@transactional
def read_override_entitlement(*, user_id, organization_id):
    _enabled()
    user = _actor(user_id)
    if user.role is not Role.SUPER_ADMIN or user.clinic_id is not None:
        _deny()
    row = _row(_org(organization_id))
    return {'version': _version(row), 'clinic_override_allowed': _allowed(row)}


@transactional
def set_override_entitlement(*, user_id, organization_id, expected_version, allowed):
    _enabled()
    user = _actor(user_id)
    if user.role is not Role.SUPER_ADMIN or user.clinic_id is not None or type(allowed) is not bool:
        _deny()
    org = _org(organization_id)
    row = _row(org)
    _check_version(row, expected_version)
    before = dict(_metadata(row), clinic_override_allowed=_allowed(row))
    if row is None:
        row = OrganizationBranding(organization_id=org.id, version=1)
        db.session.add(row)
    else:
        row.version += 1
    row.clinic_override_allowed = allowed
    db.session.flush()
    _audit(user, org, row, None, before, 'Platform staged branding entitlement updated')
    # No logo access for platform administration.
    return {'version': row.version, 'clinic_override_allowed': allowed}


@dataclass(frozen=True)
class BrandingSnapshot:
    metadata: Mapping
    logo_png: bytes | None
    branding_sha256: str


def _snapshot(org, clinic):
    default = _row(org)
    override = _row(org, clinic.id)
    # Revocation suppresses dormant overrides, never promotes them across tenants.
    logo = _bytes(override) if _allowed(default) else None
    source = 'clinic' if logo is not None else 'none'
    if logo is None:
        logo = _bytes(default)
        source = 'organization' if logo is not None else 'none'
    meta = {'organization_id': str(org.id), 'organization_name': org.name, 'clinic_id': clinic.id,
            'clinic_name': clinic.name, 'address': clinic.address, 'city': clinic.city, 'country': clinic.country,
            'phone': clinic.phone, 'email': clinic.email, 'organization_version': _version(default),
            'clinic_version': _version(override), 'clinic_override_allowed': _allowed(default),
            'logo_source': source, 'logo_sha256': sha256(logo).hexdigest() if logo else None}
    primary, secondary, palette_source = _effective_palette(default, override)
    meta.update(primary_color=primary, secondary_color=secondary, palette_source=palette_source, clinic_primary_color=_stored_palette(override)[0], clinic_secondary_color=_stored_palette(override)[1])
    digest = sha256(json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    return BrandingSnapshot(MappingProxyType(meta), logo, digest)


def resolve_branding(*, owning_clinic_id):
    """Internal only: caller must have already authorized the canonical document.
    Does not grant access to a record. Caller owns the surrounding transaction.
    """
    _enabled()
    if type(owning_clinic_id) is not int or owning_clinic_id <= 0:
        _deny()
    clinic = db.session.get(Clinic, owning_clinic_id)
    if clinic is None or clinic.status is not ClinicStatus.ACTIVE:
        _deny()
    org = _org(clinic.organization_id)
    return _snapshot(org, clinic)


@transactional
def read_branding(*, user_id, organization_id, clinic_id=None, logo=False):
    _enabled()
    org = _org(organization_id)
    _authorize(user_id, org, clinic_id)
    if clinic_id is None:
        row = _row(org)
        return _bytes(row) if logo else dict(_metadata(row), organization_id=str(org.id), organization_name=org.name, clinic_override_allowed=_allowed(row), effective_primary_color=_effective_palette(row)[0], effective_secondary_color=_effective_palette(row)[1], palette_source=_effective_palette(row)[2])
    snap = _snapshot(org, db.session.get(Clinic, clinic_id))
    return snap.logo_png if logo else dict(snap.metadata, branding_sha256=snap.branding_sha256)


# L05 Run 2D: read-only discovery. These responses confer no authorization.
_DISCOVERY_PAGE_SIZE = 50


def _platform_discovery_actor(user_id):
    _enabled()
    user = _actor(user_id)
    if user.role is not Role.SUPER_ADMIN or user.clinic_id is not None:
        _deny()


def _directory_query():
    # Select only the identity and entitlement columns. Never load logo bytes,
    # colours, membership records, clinic data or clinical records.
    return select(
        HospitalOrganization.id, HospitalOrganization.name,
        OrganizationBranding.version, OrganizationBranding.clinic_override_allowed,
    ).select_from(HospitalOrganization).outerjoin(OrganizationBranding,
                OrganizationBranding.organization_id == HospitalOrganization.id)


def _directory_item(row):
    oid, name, version, allowed = row
    return {'organization_id': str(oid), 'organization_name': name,
            'version': version if version is not None else 0,
            'clinic_override_allowed': allowed is True}


@transactional
def list_branding_organizations(*, user_id, after=None):
    _platform_discovery_actor(user_id)
    query = _directory_query()
    if after is not None:
        try:
            cursor = _parse_organization_id(after)
        except TenantAccessDenied as exc:
            raise ValidationError('Invalid organization cursor') from exc
        if not isinstance(after, str) or after != str(cursor):
            raise ValidationError('Invalid organization cursor')
        query = query.where(HospitalOrganization.id > cursor)
    rows = db.session.execute(query.order_by(HospitalOrganization.id).limit(
        _DISCOVERY_PAGE_SIZE + 1)).all()
    items = [_directory_item(row) for row in rows[:_DISCOVERY_PAGE_SIZE]]
    return {'items': items, 'next_cursor': items[-1]['organization_id']
            if len(rows) > _DISCOVERY_PAGE_SIZE else None}


@transactional
def read_branding_organization(*, user_id, organization_id):
    _platform_discovery_actor(user_id)
    try:
        oid = _parse_organization_id(organization_id)
    except TenantAccessDenied:
        _deny()
    row = db.session.execute(_directory_query().where(
        HospitalOrganization.id == oid)).one_or_none()
    if row is None:
        _deny()
    return _directory_item(row)


@transactional
def read_branding_capabilities(*, user_id, organization_id, clinic_id):
    _enabled()
    user = _actor(user_id)
    if user.role is Role.SUPER_ADMIN or organization_id is None:
        _deny()
    try:
        membership = require_tenant_membership(user_id=user.id,
                                               organization_id=organization_id)
    except TenantAccessDenied:
        _deny()
    org = _org(organization_id)

    def can_manage(target):
        # Reuse the exact mutation authorization, including the existing
        # home-organization audit boundary and active selected-clinic check.
        try:
            _authorize(user.id, org, target)
        except NotFoundError:
            return False
        return True

    organization_access = can_manage(None)
    clinic_access = clinic_id is not None and can_manage(clinic_id)
    # Revocation prevents new overrides but still permits a legitimate clinic
    # admin to clear a dormant override, exactly as in change_logo/colors.
    customize = clinic_access and db.session.execute(select(
        OrganizationBranding.clinic_override_allowed).where(
        OrganizationBranding.organization_id == org.id)).scalar_one_or_none() is True
    return {
        'organization_id': str(org.id), 'organization_name': org.name,
        'membership_role': membership.role, 'clinic_id': clinic_id,
        'can_manage_organization_branding': organization_access,
        'can_manage_clinic_branding': clinic_access,
        'can_customize_clinic_branding': customize,
        'can_reset_clinic_branding': clinic_access,
    }
