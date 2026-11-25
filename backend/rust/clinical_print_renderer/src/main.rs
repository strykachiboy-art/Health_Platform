//! Dedicated clinic document PDF renderer. No DB, network, credentials or filesystem output.
//! Input is a bounded, trusted server-side PrintDocument snapshot over stdin.
//! Output is PDF bytes on stdout ONLY on success; errors are generic.
use genpdf::{elements, fonts, Document, SimplePageDecorator};
use serde::Deserialize;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

const MAX_INPUT: usize = 65_536;
const MAX_PDF: usize = 3 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PrintRequest {
    protocol: u8,
    kind: String,
    clinic_id: u64,
    record_id: u64,
    source_version: u64,
    title: String,
    watermark: String,
    source_digest: String,
    is_signed: bool,
    released_to_chart: bool,
    fields: Vec<(String, String)>,
}

fn check_str(s: &str, max: usize) -> Result<(), &'static str> {
    if s.is_empty()
        || s.len() > max
        || s.chars().any(|c| {
            (c.is_control() && c != '\n' && c != '\t')
                || matches!(c, '\u{202e}' | '\u{202d}' | '\u{202c}')
        })
    {
        return Err("invalid text");
    }
    Ok(())
}

fn validate(d: &PrintRequest) -> Result<(), &'static str> {
    if d.protocol != 1 || d.clinic_id == 0 || d.record_id == 0 || d.is_signed || d.released_to_chart
    {
        return Err("unauthorized print snapshot");
    }
    let (names, watermark): (&[&str], &str) = match d.kind.as_str() {
        "invoice" => (
            &[
                "Invoice Number",
                "Patient Id",
                "Status",
                "Currency",
                "Total Amount",
                "Amount Paid",
            ],
            "BILLING DOCUMENT COPY - NOT A PAYMENT RECEIPT",
        ),
        "appointment" => (
            &[
                "Patient Id",
                "Assigned Staff Id",
                "Status",
                "Scheduled Start",
                "Scheduled End",
            ],
            "APPOINTMENT INFORMATION - NOT PROOF OF ATTENDANCE",
        ),
        "lab_order" => (
            &["Patient Id", "Status", "Created At", "Sample Collected At"],
            "SPECIMEN / LAB ORDER ONLY - NO VERIFIED RESULT",
        ),
        "radiology_draft" => (
            &[
                "Imaging Order Id",
                "Accession Number",
                "Report Revision",
                "Report Text",
            ],
            "UNSIGNED WORKING NOTE - NOT FOR DIAGNOSIS OR CHART RELEASE",
        ),
        _ => return Err("unsupported document type"),
    };
    if d.kind == "radiology_draft" && d.source_version == 0 {
        return Err("unversioned draft");
    }
    if d.watermark != watermark
        || d.title
            != d.kind
                .replace('_', " ")
                .split_whitespace()
                .map(|p| {
                    let mut c = p.chars();
                    match c.next() {
                        None => String::new(),
                        Some(first) => first.to_uppercase().collect::<String>() + c.as_str(),
                    }
                })
                .collect::<Vec<_>>()
                .join(" ")
    {
        return Err("document label mismatch");
    }
    if d.source_digest.len() != 64 || !d.source_digest.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("invalid digest");
    }
    if d.fields.len() != names.len() {
        return Err("invalid source fields");
    }
    for ((label, text), expected) in d.fields.iter().zip(names) {
        if label != expected {
            return Err("unexpected source fields");
        }
        check_str(text, if label == "Report Text" { 12_000 } else { 300 })?;
    }
    Ok(())
}

fn font_paths() -> Result<[PathBuf; 4], &'static str> {
    // Never accept font paths from request payloads. Operator-controlled deployment only.
    if cfg!(windows) {
        let windir = std::env::var_os("WINDIR").unwrap_or_else(|| "C:\\Windows".into());
        let dir = PathBuf::from(windir).join("Fonts");
        let arial = ["arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"];
        let segoe = [
            "segoeui.ttf",
            "segoeuib.ttf",
            "segoeuii.ttf",
            "segoeuiz.ttf",
        ];
        for family in [segoe, arial] {
            let paths = family.map(|f| dir.join(f));
            if paths.iter().all(|p| p.is_file()) {
                return Ok(paths);
            }
        }
    } else {
        let dir = Path::new("/usr/share/fonts/truetype/dejavu");
        let names = [
            "DejaVuSans.ttf",
            "DejaVuSans-Bold.ttf",
            "DejaVuSans-Oblique.ttf",
            "DejaVuSans-BoldOblique.ttf",
        ];
        let paths = names.map(|f| dir.join(f));
        if paths.iter().all(|p| p.is_file()) {
            return Ok(paths);
        }
    }
    Err("approved font family missing")
}

// genpdf 0.2 embeds every glyph of all loaded font variants. Use the
// PDF-standard Helvetica family only with metric-compatible installed fonts
// and text representable in Windows-1252. The font files supply glyph metrics
// to genpdf; they are NOT embedded in PDFs on this branch.
// Non-Windows-1252 text retains the existing embedded-font behavior and bounds.
fn helvetica_text_compatible(c: char) -> bool {
    c == '\n'
        || c == '\t'
        || (' '..='~').contains(&c)
        || ('\u{00A0}'..='\u{00FF}').contains(&c)
        || matches!(
            c,
            '\u{20AC}'
                | '\u{201A}'
                | '\u{0192}'
                | '\u{201E}'
                | '\u{2026}'
                | '\u{2020}'
                | '\u{2021}'
                | '\u{02C6}'
                | '\u{2030}'
                | '\u{0160}'
                | '\u{2039}'
                | '\u{0152}'
                | '\u{017D}'
                | '\u{2018}'
                | '\u{2019}'
                | '\u{201C}'
                | '\u{201D}'
                | '\u{2022}'
                | '\u{2013}'
                | '\u{2014}'
                | '\u{02DC}'
                | '\u{2122}'
                | '\u{0161}'
                | '\u{203A}'
                | '\u{0153}'
                | '\u{017E}'
                | '\u{0178}'
        )
}

fn helvetica_metric_paths() -> Option<[PathBuf; 4]> {
    #[cfg(windows)]
    {
        // Arial is metric-compatible with PDF-standard Helvetica.
        let windows = std::env::var_os("WINDIR").unwrap_or_else(|| "C:\\Windows".into());
        let dir = PathBuf::from(windows).join("Fonts");
        let paths =
            ["arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"].map(|name| dir.join(name));
        if paths.iter().all(|p| p.is_file()) {
            return Some(paths);
        }
    }
    #[cfg(not(windows))]
    {
        // Explicitly installed by the Dockerfile, not a runtime download.
        let dir = Path::new("/usr/share/fonts/truetype/liberation");
        let paths = [
            "LiberationSans-Regular.ttf",
            "LiberationSans-Bold.ttf",
            "LiberationSans-Italic.ttf",
            "LiberationSans-BoldItalic.ttf",
        ]
        .map(|name| dir.join(name));
        if paths.iter().all(|p| p.is_file()) {
            return Some(paths);
        }
    }
    None
}

fn safe_chunks(text: &str, cap: usize) -> Vec<String> {
    // Bound single glyph runs so whitespace-free input cannot blow page width.
    let mut result = Vec::new();
    for line in text.split('\n') {
        let normalized = line.replace('\t', "    ");
        let chars: Vec<char> = normalized.chars().collect();
        if chars.is_empty() {
            result.push(" ".into());
            continue;
        }
        for piece in chars.chunks(cap) {
            result.push(piece.iter().collect());
        }
    }
    result
}

fn render(d: &PrintRequest) -> Result<Vec<u8>, &'static str> {
    let requested_texts: Vec<&str> = std::iter::once(d.title.as_str())
        .chain(std::iter::once(d.watermark.as_str()))
        .chain(d.fields.iter().flat_map(|(k, v)| [k.as_str(), v.as_str()]))
        .collect();
    let metric_fonts = if requested_texts
        .iter()
        .all(|text| text.chars().all(helvetica_text_compatible))
    {
        helvetica_metric_paths()
    } else {
        None
    };
    let use_builtin = metric_fonts.is_some();
    let paths = match metric_fonts {
        Some(paths) => paths,
        None => font_paths()?,
    };
    // Fail closed when configured fonts cannot represent a requested character.
    let raw = std::fs::read(&paths[0]).map_err(|_| "font unavailable")?;
    let check_font = rusttype::Font::try_from_vec(raw).ok_or("invalid font")?;
    for s in requested_texts {
        for c in s.chars() {
            if c != '\n' && c != '\t' && check_font.glyph(c).id().0 == 0 {
                return Err("font cannot render character");
            }
        }
    }
    // genpdf FontData::load uses individual printpdf builtin font variants.
    let regular_builtin = use_builtin.then_some(printpdf::BuiltinFont::Helvetica);
    let bold_builtin = use_builtin.then_some(printpdf::BuiltinFont::HelveticaBold);
    let italic_builtin = use_builtin.then_some(printpdf::BuiltinFont::HelveticaOblique);
    let bold_italic_builtin = use_builtin.then_some(printpdf::BuiltinFont::HelveticaBoldOblique);
    let family = fonts::FontFamily {
        regular: fonts::FontData::load(&paths[0], regular_builtin).map_err(|_| "invalid font")?,
        bold: fonts::FontData::load(&paths[1], bold_builtin).map_err(|_| "invalid font")?,
        italic: fonts::FontData::load(&paths[2], italic_builtin).map_err(|_| "invalid font")?,
        bold_italic: fonts::FontData::load(&paths[3], bold_italic_builtin)
            .map_err(|_| "invalid font")?,
    };
    let mut doc = Document::new(family);
    doc.set_title("CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY");
    doc.set_minimal_conformance();
    doc.set_font_size(10);
    let mut decorator = SimplePageDecorator::new();
    decorator.set_margins(14);
    doc.set_page_decorator(decorator);
    doc.push(elements::Paragraph::new(
        "CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY",
    ));
    doc.push(elements::Paragraph::new(d.title.as_str()));
    doc.push(elements::Paragraph::new(d.watermark.as_str()));
    doc.push(elements::Paragraph::new(format!(
        "Clinic reference: {}   Record reference: {}",
        d.clinic_id, d.record_id
    )));
    doc.push(elements::Paragraph::new(format!(
        "Source version: {}",
        d.source_version
    )));
    for (label, value) in &d.fields {
        doc.push(elements::Paragraph::new(format!("{}:", label)));
        for chunk in safe_chunks(value, 56) {
            doc.push(elements::Paragraph::new(chunk));
        }
    }
    doc.push(elements::Paragraph::new(format!(
        "SOURCE SHA-256: {}",
        d.source_digest
    )));
    doc.push(elements::Paragraph::new("This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release."));
    let mut result = Vec::new();
    doc.render(&mut result).map_err(|_| "pdf render failed")?;
    if !result.starts_with(b"%PDF-") || result.len() > MAX_PDF {
        return Err("invalid pdf output");
    }
    Ok(result)
}

fn run() -> Result<(), &'static str> {
    let mut stdin = io::stdin().take((MAX_INPUT + 1) as u64);
    let mut bytes = Vec::new();
    stdin
        .read_to_end(&mut bytes)
        .map_err(|_| "input unavailable")?;
    if bytes.is_empty() || bytes.len() > MAX_INPUT {
        return Err("input too large");
    }
    let d: PrintRequest = serde_json::from_slice(&bytes).map_err(|_| "invalid print protocol")?;
    validate(&d)?;
    let pdf = render(&d)?;
    io::stdout().write_all(&pdf).map_err(|_| "write failed")?;
    Ok(())
}

fn main() {
    if run().is_err() {
        // No error details, PHI, data fragments, or stack traces to stderr.
        eprintln!("clinical_print_renderer: denied");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_nonclinical_kind_and_signed() {
        let mut d = PrintRequest {
            protocol: 1,
            kind: "invoice".into(),
            clinic_id: 1,
            record_id: 2,
            source_version: 0,
            title: "Invoice".into(),
            watermark: "BILLING DOCUMENT COPY - NOT A PAYMENT RECEIPT".into(),
            source_digest: "a".repeat(64),
            is_signed: false,
            released_to_chart: false,
            fields: vec![
                ("Invoice Number".into(), "A".into()),
                ("Patient Id".into(), "1".into()),
                ("Status".into(), "ISSUED".into()),
                ("Currency".into(), "NGN".into()),
                ("Total Amount".into(), "100".into()),
                ("Amount Paid".into(), "0".into()),
            ],
        };
        assert!(validate(&d).is_ok());
        d.is_signed = true;
        assert!(validate(&d).is_err());
        d.is_signed = false;
        d.kind = "discharge_final".into();
        assert!(validate(&d).is_err());
    }
    #[test]
    fn bounded_string_checks() {
        assert!(check_str("A\u{202e}B", 300).is_err());
        assert!(check_str("A\0B", 300).is_err());
        assert!(check_str(&"A".repeat(301), 300).is_err());
    }
}
