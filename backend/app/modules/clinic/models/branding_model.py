"""Bounded transactional branding assets; no public storage keys or clinical grants."""
from sqlalchemy import Uuid, false
from app.extensions import db


class OrganizationBranding(db.Model):
    __tablename__ = 'organization_branding'
    __table_args__ = (
        db.CheckConstraint('version >= 1', name='ck_org_brand_version'),
        db.CheckConstraint("(primary_color IS NULL AND secondary_color IS NULL) OR (primary_color IS NOT NULL AND secondary_color IS NOT NULL AND length(primary_color)=7 AND length(secondary_color)=7 AND substr(primary_color,1,1)='#' AND substr(secondary_color,1,1)='#')", name='ck_org_brand_colors'),
        db.CheckConstraint('(logo_png IS NULL AND logo_sha256 IS NULL) OR (logo_png IS NOT NULL AND logo_sha256 IS NOT NULL AND length(logo_png) BETWEEN 1 AND 2097152 AND length(logo_sha256) = 64)', name='ck_org_brand_logo'),
    )
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(Uuid, db.ForeignKey('hospital_organizations.id'), nullable=False, unique=True)
    version = db.Column(db.Integer, nullable=False)
    logo_png = db.Column(db.LargeBinary, nullable=True)
    logo_sha256 = db.Column(db.String(64), nullable=True)
    primary_color = db.Column(db.String(7), nullable=True)
    secondary_color = db.Column(db.String(7), nullable=True)
    studio_palette = db.Column(db.JSON(none_as_null=True), nullable=True)
    # Not a paid subscription receipt. Platform-controlled staged capability.
    clinic_override_allowed = db.Column(db.Boolean, nullable=False, default=False, server_default=false())


class ClinicBranding(db.Model):
    __tablename__ = 'clinic_branding'
    __table_args__ = (
        db.ForeignKeyConstraint(['clinic_id', 'organization_id'], ['clinics.id', 'clinics.organization_id'], name='fk_clinic_brand_owner'),
        db.CheckConstraint('version >= 1', name='ck_clinic_brand_version'),
        db.CheckConstraint("(primary_color IS NULL AND secondary_color IS NULL) OR (primary_color IS NOT NULL AND secondary_color IS NOT NULL AND length(primary_color)=7 AND length(secondary_color)=7 AND substr(primary_color,1,1)='#' AND substr(secondary_color,1,1)='#')", name='ck_clinic_brand_colors'),
        db.CheckConstraint('(logo_png IS NULL AND logo_sha256 IS NULL) OR (logo_png IS NOT NULL AND logo_sha256 IS NOT NULL AND length(logo_png) BETWEEN 1 AND 2097152 AND length(logo_sha256) = 64)', name='ck_clinic_brand_logo'),
    )
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, nullable=False, unique=True)
    organization_id = db.Column(Uuid, nullable=False)
    version = db.Column(db.Integer, nullable=False)
    logo_png = db.Column(db.LargeBinary, nullable=True)
    logo_sha256 = db.Column(db.String(64), nullable=True)
    primary_color = db.Column(db.String(7), nullable=True)
    secondary_color = db.Column(db.String(7), nullable=True)
    studio_palette = db.Column(db.JSON(none_as_null=True), nullable=True)
