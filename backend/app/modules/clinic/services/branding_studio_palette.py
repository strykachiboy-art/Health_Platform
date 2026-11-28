"""Pure staged palette contract shared by storage and PDF planning."""
import colorsys
import re

DEFAULTS = dict(primary_color='#17354A', secondary_color='#475569',
                text_color='#142A3A', warning_color='#842020',
                background_color='#FFFFFF', output_mode='full')
COLOR_KEYS = tuple(k for k in DEFAULTS if k != 'output_mode')
MODES = ('full', 'ink_saving', 'monochrome')


def rgb(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9A-F]{6}', value):
        raise ValueError('Canonical six-digit hex required')
    return tuple(int(value[i:i+2], 16) for i in (1, 3, 5))


def luminance(value):
    def linear(x):
        x /= 255
        return x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4
    return sum(w * linear(x) for w, x in zip((.2126, .7152, .0722), rgb(value)))


def contrast(a, b):
    a, b = sorted((luminance(a), luminance(b)))
    return (b + .05) / (a + .05)


def normalize_local(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('Unknown studio palette field')
    result = {}
    for key, value in values.items():
        if value is None:
            continue
        if key == 'output_mode':
            if value not in MODES:
                raise ValueError('Unsupported output mode')
        else:
            if not isinstance(value, str):
                raise ValueError('Hex colour required')
            value = value.upper()
            rgb(value)
        result[key] = value
    return result


def validate_effective(p):
    if not isinstance(p, dict) or set(p) != set(DEFAULTS) or normalize_local(p) != p:
        raise ValueError('Incomplete or noncanonical studio palette')
    paper = p['background_color']
    if luminance(paper) < .80:
        raise ValueError('Paper background must remain light')
    for key in ('primary_color', 'secondary_color'):
        if min(contrast(p[key], c) for c in (paper, '#FFFFFF')) < 4.5:
            raise ValueError('Accent contrast below 4.5:1')
    if min(contrast(p['text_color'], c) for c in (paper, '#FFFFFF', '#F1F5F7')) < 7:
        raise ValueError('Document text contrast below 7:1')
    warning = p['warning_color']
    h, s, _ = colorsys.rgb_to_hsv(*(x / 255 for x in rgb(warning)))
    if not (h * 360 <= 50 or h * 360 >= 330) or s < .35:
        raise ValueError('Warning colour must be recognizable as caution')
    if min(contrast(warning, c) for c in (paper, '#FFFFFF', '#FCF2F1')) < 4.5:
        raise ValueError('Warning contrast below 4.5:1')
    return p


def resolve(parent=None, local=None):
    return validate_effective({**DEFAULTS, **(parent or {}), **(local or {})})


def paper_color(p):
    return p['background_color'] if p['output_mode'] == 'full' else '#FFFFFF'


def paint_color(color, mode):
    if mode != 'monochrome':
        return color
    # Preserve visual hierarchy with high contrast; not merely a colour tint.
    r, g, b = rgb(color)
    level = (299*r + 587*g + 114*b + 500)//1000
    level = 0 if level < 128 else level
    return '#'+f'{level:02X}'*3
