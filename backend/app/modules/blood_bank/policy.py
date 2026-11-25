"""Deterministic, fail-closed policy checks; no clinical protocol is shipped.

Production policy values must be independently approved, deployed server-side,
and versioned. Request bodies cannot supply the policy itself.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re


class PolicyError(ValueError):
    pass


BASE_MARKERS = frozenset({'hiv', 'hbv', 'hcv', 'syphilis'})
BLOOD_GROUPS = frozenset({'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'})
PRODUCTS = frozenset({'packed_red_blood_cells', 'whole_blood', 'platelets', 'fresh_frozen_plasma', 'cryoprecipitate'})
MEASUREMENTS = ('hemoglobin_g_dl', 'systolic_bp', 'diastolic_bp', 'pulse_bpm')


def text(value, name, max_length=128):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise PolicyError(f'{name} is required and must be at most {max_length} characters')
    return value.strip()


def positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise PolicyError(f'{name} must be a positive integer')
    return value


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PolicyError('Timestamp must include a timezone')
    return value.astimezone(timezone.utc)


def db_utc(value):
    # The existing database also uses naive UTC values for historical rows.
    if not isinstance(value, datetime):
        raise PolicyError('Missing stored timestamp')
    return utc(value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value)


def identity(value, name, max_length=128):
    result = text(value, name, max_length)
    # The canonical local identifier is ASCII and uppercase. Preserve original
    # supplier label in evidence; no assertion of ISBT 128 decoding/compliance.
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9._:/+\-=]*', result):
        raise PolicyError(f'{name} must be a canonical uppercase component identifier')
    return result


def policy_version(policy):
    if not isinstance(policy, dict) or policy.get('approved') is not True:
        raise PolicyError('Approved blood bank policy is not configured')
    return text(policy.get('version'), 'policy version', 80)


def require_actor(policy, authority, actor_id):
    policy_version(policy)
    values = policy.get(authority)
    if not isinstance(values, (list, tuple)) or not all(type(x) is int and x > 0 for x in values) or actor_id not in values:
        raise PolicyError('Staff member is not authorized for this blood bank operation')


def screening_panel(policy, results):
    policy_version(policy)
    panel = policy.get('required_tti_markers')
    if not isinstance(panel, (list, tuple)) or not all(isinstance(x, str) and x and x == x.strip().lower() for x in panel) or len(set(panel)) != len(panel) or not BASE_MARKERS.issubset(panel):
        raise PolicyError('Complete approved screening panel is not configured')
    if not isinstance(results, dict) or set(results) != set(panel):
        raise PolicyError('Screening evidence must match every approved marker')
    clean = {}
    for marker in panel:
        entry = results[marker]
        if not isinstance(entry, dict) or set(entry) != {'result', 'evidence_reference'}:
            raise PolicyError('Each screening marker requires a result and evidence reference')
        result = entry['result']
        if result not in ('non_reactive', 'reactive', 'indeterminate'):
            raise PolicyError('Unrecognized screening result')
        clean[marker] = {'result': result, 'evidence_reference': text(entry['evidence_reference'], 'screening evidence reference', 255)}
    return clean


def cleared_panel(policy, results):
    clean = screening_panel(policy, results)
    if any(x['result'] != 'non_reactive' for x in clean.values()):
        raise PolicyError('Reactive or indeterminate screening cannot authorize release')
    return clean


def intake(policy, *, actor_id, cooldown_days, passed, reference, measurements):
    require_actor(policy, 'intake_staff_ids', actor_id)
    if passed is not True:
        raise PolicyError('Explicit documented eligibility assessment is required')
    text(reference, 'eligibility assessment reference', 5000)
    minimum = positive_int(policy.get('minimum_cooldown_days'), 'approved recovery interval')
    if positive_int(cooldown_days, 'cooldown_days') < minimum:
        raise PolicyError('Recovery interval is below the approved minimum')
    limits = policy.get('intake_measurement_ranges')
    if not isinstance(limits, dict) or set(limits) != set(MEASUREMENTS):
        raise PolicyError('Approved donor measurement protocol is not configured')
    for key in MEASUREMENTS:
        bounds = limits[key]
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2 or isinstance(measurements.get(key), bool):
            raise PolicyError('Invalid or missing intake measurement')
        try:
            low, high, value = Decimal(str(bounds[0])), Decimal(str(bounds[1])), Decimal(str(measurements.get(key)))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise PolicyError('Invalid or missing intake measurement') from exc
        if not all(x.is_finite() for x in (low, high, value)) or low > high or not low <= value <= high:
            raise PolicyError(f'{key} does not satisfy the approved intake protocol')


def candidate_groups(policy, product, recipient, *, emergency=False):
    policy_version(policy)
    if product not in PRODUCTS:
        raise PolicyError('Unrecognized component type')
    if emergency:
        if product not in policy.get('emergency_product_types', ()):
            raise PolicyError('No approved emergency component policy')
        tables = policy.get('emergency_compatibility')
    else:
        tables = policy.get('component_compatibility')
        if recipient not in BLOOD_GROUPS:
            raise PolicyError('Verified recipient blood group required')
    if not isinstance(tables, dict) or not isinstance(tables.get(product), dict):
        raise PolicyError('Approved component-specific compatibility policy is missing')
    groups = tables[product].get(recipient)
    if not isinstance(groups, (list, tuple)) or not groups or any(g not in BLOOD_GROUPS for g in groups):
        raise PolicyError('No approved compatibility candidates for this component and recipient')
    return tuple(dict.fromkeys(groups))


def fresh_scan(policy, timestamp, now):
    policy_version(policy)
    max_age = positive_int(policy.get('barcode_max_age_seconds'), 'approved barcode freshness interval')
    # Engineering hard ceiling, not a clinical recommendation. No policy may
    # allow a digital authorization token older than five minutes.
    if max_age > 300:
        raise PolicyError('Barcode freshness exceeds engineering safety ceiling')
    delta = (utc(now) - db_utc(timestamp)).total_seconds()
    if delta < 0 or delta > max_age:
        raise PolicyError('Barcode verification is stale or future-dated')
