//! RAD4 isolated, one-shot DICOM-rs validation of SYNTHETIC uncompressed SC files.
//! Orthanc is the image archive. Flask alone controls all clinical decisions.
//! This executable never contacts a database, service, queue, or remote endpoint.

use dicom_object::open_file;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

const MAX_BYTES: usize = 4 * 1024 * 1024;
const SOP_CLASS: &str = "1.2.840.10008.5.1.4.1.1.7";
const TRANSFER_SYNTAX: &str = "1.2.840.10008.1.2.1";

fn reject(code: &'static str) -> String { code.to_owned() }
fn digest(data: &[u8]) -> String { format!("{:x}", Sha256::digest(data)) }

fn uid(s: &str) -> bool {
    s.len() <= 64 && s.len() > 2 && s.is_ascii()
        && s.split('.').count() >= 2
        && s.split('.').all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
}

fn expected_subject(s: &str) -> bool {
    (12..=64).contains(&s.len()) && s.starts_with("RAD2_SYNTH_")
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

fn accession(s: &str) -> bool {
    (1..=16).contains(&s.len()) && s.is_ascii()
        && s.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
}

fn checked_wire(src: &[u8]) -> Result<(), String> {
    // Deliberately restricted explicit VR little-endian Part 10.  No encapsulated
    // or compressed data, indefinite lengths, nested SQ, or duplicates admitted.
    if !(160..=MAX_BYTES).contains(&src.len()) || &src[128..132] != b"DICM" {
        return Err(reject("BAD_PART10_OR_SIZE"));
    }
    let mut i = 132usize;
    let mut last: Option<(u16, u16)> = None;
    let mut seen = HashSet::new();
    let long_vr = [b"OB", b"OD", b"OF", b"OL", b"OV", b"OW", b"SQ", b"UC", b"UN", b"UR", b"UT", b"UV", b"SV"];
    let mut pixels = false;
    while i < src.len() {
        if src.len() - i < 8 { return Err(reject("TRUNCATED_DICOM_HEADER")); }
        let tag = (u16::from_le_bytes([src[i], src[i + 1]]),
                   u16::from_le_bytes([src[i + 2], src[i + 3]]));
        if !seen.insert(tag) { return Err(reject("DUPLICATE_TAG")); }
        if let Some(previous) = last {
            if tag <= previous { return Err(reject("UNSORTED_TAGS")); }
        }
        last = Some(tag);
        let vr = &src[i + 4..i + 6];
        if !vr.iter().all(|b| b.is_ascii_uppercase()) { return Err(reject("INVALID_VR")); }
        let is_long = long_vr.iter().any(|v| vr == v.as_slice());
        let (head, len) = if is_long {
            if src.len() - i < 12 || src[i + 6] != 0 || src[i + 7] != 0 {
                return Err(reject("INVALID_LONG_VR"));
            }
            (12usize, u32::from_le_bytes([src[i + 8], src[i + 9], src[i + 10], src[i + 11]]) as usize)
        } else {
            (8usize, u16::from_le_bytes([src[i + 6], src[i + 7]]) as usize)
        };
        if len == 0xFFFF_FFFF || len % 2 != 0 { return Err(reject("INVALID_DICOM_LENGTH")); }
        i = i.checked_add(head).and_then(|x| x.checked_add(len))
            .ok_or_else(|| reject("DICOM_LENGTH_OVERFLOW"))?;
        if i > src.len() { return Err(reject("TRUNCATED_DICOM_VALUE")); }
        if tag == (0x7FE0, 0x0010) { pixels = true; }
    }
    if !pixels { return Err(reject("PIXEL_DATA_ABSENT")); }
    Ok(())
}

fn get_text(obj: &dicom_object::DefaultDicomObject, key: &str) -> Result<String, String> {
    let element = obj.element_by_name(key).map_err(|_| reject("REQUIRED_TAG_MISSING"))?;
    let value = element.to_str().map_err(|_| reject("TAG_NOT_STRING"))?;
    Ok(value.trim_matches(|c: char| c == '\0' || c.is_whitespace()).to_owned())
}

fn get_u16(obj: &dicom_object::DefaultDicomObject, key: &str) -> Result<u16, String> {
    obj.element_by_name(key).map_err(|_| reject("IMAGE_TAG_MISSING"))?
        .to_int::<u16>().map_err(|_| reject("IMAGE_TAG_INVALID"))
}

struct Evidence {
    file_sha256: String,
    pixel_sha256: String,
    width: u16,
    height: u16,
    pixels: Vec<u8>,
}

fn inspect(src: &Path, expected: &[String]) -> Result<Evidence, String> {
    if expected.len() != 7 || !expected_subject(&expected[0]) || !accession(&expected[1])
        || !uid(&expected[2]) || !uid(&expected[3]) || !uid(&expected[4])
        || expected[5].len() != 64 || expected[6].len() != 64
        || ![&expected[5], &expected[6]].iter().all(|s| s.bytes().all(|c| c.is_ascii_hexdigit())) {
        return Err(reject("UNTRUSTED_EXPECTED_IDENTITY"));
    }
    let md = fs::symlink_metadata(src).map_err(|_| reject("INPUT_NOT_FOUND"))?;
    if !md.is_file() || md.file_type().is_symlink() || md.len() > MAX_BYTES as u64 {
        return Err(reject("UNSAFE_INPUT_TYPE_OR_SIZE"));
    }
    let bytes = fs::read(src).map_err(|_| reject("INPUT_READ_FAILED"))?;
    checked_wire(&bytes)?;
    let file_sha = digest(&bytes);
    if file_sha != expected[5] { return Err(reject("FILE_DIGEST_MISMATCH")); }
    // dicom-rs provides a second, independent structured parse of the bounded file.
    let obj = open_file(src).map_err(|_| reject("DICOM_RS_PARSE_FAILED"))?;
    if obj.meta().transfer_syntax() != TRANSFER_SYNTAX
        || obj.meta().media_storage_sop_class_uid() != SOP_CLASS
        || obj.meta().media_storage_sop_instance_uid() != expected[4] {
        return Err(reject("DICOM_FILE_META_MISMATCH"));
    }
    let pairs = [
        ("PatientID", &expected[0]), ("AccessionNumber", &expected[1]),
        ("StudyInstanceUID", &expected[2]), ("SeriesInstanceUID", &expected[3]),
        ("SOPInstanceUID", &expected[4]),
    ];
    for (key, trusted) in pairs {
        if get_text(&obj, key)? != *trusted { return Err(reject("IDENTITY_MISMATCH")); }
    }
    if get_text(&obj, "SOPClassUID")? != SOP_CLASS || get_text(&obj, "Modality")? != "OT" {
        return Err(reject("UNSUPPORTED_SOP_CLASS_OR_MODALITY"));
    }
    let width = get_u16(&obj, "Columns")?;
    let height = get_u16(&obj, "Rows")?;
    if !(1..=512).contains(&width) || !(1..=512).contains(&height)
        || get_u16(&obj, "BitsAllocated")? != 8
        || get_u16(&obj, "BitsStored")? != 8
        || get_u16(&obj, "HighBit")? != 7
        || get_u16(&obj, "SamplesPerPixel")? != 1
        || get_u16(&obj, "PixelRepresentation")? != 0
        || get_text(&obj, "PhotometricInterpretation")? != "MONOCHROME2" {
        return Err(reject("UNSUPPORTED_PREVIEW_FORMAT"));
    }
    let pixels = obj.element_by_name("PixelData")
        .map_err(|_| reject("PIXEL_DATA_MISSING"))?
        .to_bytes().map_err(|_| reject("ENCAPSULATED_PIXEL_DATA_DENIED"))?;
    if pixels.len() != usize::from(width) * usize::from(height) {
        return Err(reject("PIXEL_LENGTH_INVALID"));
    }
    let pixel_sha = digest(pixels.as_ref());
    if pixel_sha != expected[6] { return Err(reject("PIXEL_DIGEST_MISMATCH")); }
    Ok(Evidence { file_sha256: file_sha, pixel_sha256: pixel_sha,
                  width, height, pixels: pixels.into_owned() })
}

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().collect();
    // One file, seven independently supplied expected identity/digest values,
    // and one output path: no globbing, network, queue, or override of files.
    if !(args.len() == 10 || args.len() == 11) || args[1] != "inspect" {
        return Err(reject("BAD_INVOCATION"));
    }
    let observed = inspect(Path::new(&args[2]), &args[3..10])?;
    // Optional bounded 8-bit PGM derivative; never an interpretable diagnostic
    // rendering or an image-archive record. Exclusive create prevents overwrite.
    let mut preview_generated = false;
    if args.len() == 11 {
        let destination = Path::new(&args[10]);
        let filename = destination.file_name().and_then(|v| v.to_str()).unwrap_or("");
        if destination.parent() != Some(Path::new("/output"))
            || !filename.starts_with("rad4_preview_") || !filename.ends_with(".pgm")
            || filename.len() > 48 || !filename.bytes().all(|b| b.is_ascii_alphanumeric()
                || b == b'_' || b == b'.') {
            return Err(reject("PREVIEW_DESTINATION_NOT_ALLOWED"));
        }
        let mut output = OpenOptions::new().write(true).create_new(true)
            .open(destination).map_err(|_| reject("PREVIEW_CREATE_DENIED"))?;
        let header = format!("P5\n# SYNTHETIC NON-DIAGNOSTIC PREVIEW - NOT FOR CLINICAL USE\n{} {}\n255\n",
                             observed.width, observed.height);
        output.write_all(header.as_bytes()).and_then(|_| output.write_all(&observed.pixels))
            .map_err(|_| reject("PREVIEW_WRITE_FAILED"))?;
        output.flush().map_err(|_| reject("PREVIEW_FLUSH_FAILED"))?;
        preview_generated = true;
    }
    let result = serde_json::json!({
        "status": "validated_synthetic_metadata_only",
        "file_sha256": observed.file_sha256,
        "pixel_sha256": observed.pixel_sha256,
        "width": observed.width,
        "height": observed.height,
        "pixel_bytes": observed.pixels.len(),
        "archive_verified": false,
        "clinical_association": false,
        "diagnostic_report_signed": false,
        "chart_released": false,
        "preview_generated": preview_generated,
        "dicom_rs_parser_used": true,
    });
    println!("{}", result);
    Ok(())
}

fn main() {
    if let Err(err) = run() {
        eprintln!("RAD4_DENIED={}", err);
        std::process::exit(4);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn uid_accepts_dicom_decimal_components() { assert!(uid("2.25.12345")); }
    #[test] fn uid_rejects_alpha_and_empty_components() { assert!(!uid("2..4")); assert!(!uid("2.abc")); }
    #[test] fn accession_bounded_by_dicom_short_string() { assert!(accession("R123456789012345")); assert!(!accession("R1234567890123456")); }
    #[test] fn synthetic_subject_only() { assert!(expected_subject("RAD2_SYNTH_RUNABCDE_A")); assert!(!expected_subject("REAL_PATIENT_1")); }
    #[test] fn wire_rejects_no_part10() { assert!(checked_wire(&[0u8; 160]).is_err()); }
    #[test] fn sha256_known_vector() { assert_eq!(digest(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"); }
}
