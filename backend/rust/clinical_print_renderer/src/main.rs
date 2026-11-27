//! Dedicated clinic document PDF renderer. No DB, network, credentials or filesystem output.
//! Input is a bounded, trusted server-side PrintDocument snapshot over stdin.
//! Output is PDF bytes on stdout ONLY on success; errors are generic.
use genpdf::{elements, fonts, Document, SimplePageDecorator};
use serde::Deserialize;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

const LEGACY_MAX_INPUT: usize = 65_536;
const V2_MAX_INPUT: usize = 512 * 1024;
const MAX_INPUT: usize = 1024 * 1024;
mod professional;
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
    #[serde(default)]
    branding: Option<Branding>,
    #[serde(default)]
    professional: Option<professional::Plan>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Branding {
    clinic_id: u64,
    lines: Vec<String>,
    branding_sha256: String,
    logo: Option<Logo>,
    #[serde(default="default_primary")]
    primary_color:String,
    #[serde(default="default_secondary")]
    secondary_color:String,
    #[serde(default)]
    verification_url:Option<String>,
    #[serde(default)]
    issued_at:Option<String>,
    #[serde(default)]
    verification_rows:Option<Vec<String>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Logo {
    width: u32,
    height: u32,
    rgb_hex: String,
}

fn palette_rgb(s: &str) -> Result<(u8,u8,u8), &'static str> {
    if s.len()!=7 || !s.starts_with('#') || !s.as_bytes()[1..].iter().all(|b| matches!(*b,b'0'..=b'9'|b'A'..=b'F')) { return Err("invalid palette"); }
    let r=u8::from_str_radix(&s[1..3],16).map_err(|_| "invalid palette")?;
    let g=u8::from_str_radix(&s[3..5],16).map_err(|_| "invalid palette")?;
    let b=u8::from_str_radix(&s[5..7],16).map_err(|_| "invalid palette")?;
    let f=|x:u8| { let n=x as f64/255.0; if n<=0.04045 { n/12.92 } else { ((n+0.055)/1.055).powf(2.4) } };
    let lum=0.2126*f(r)+0.7152*f(g)+0.0722*f(b);
    if 1.05/(lum+0.05)<4.5 { return Err("invalid palette contrast"); }
    Ok((r,g,b))
}
fn default_primary()->String { "#17354A".into() }
fn default_secondary()->String { "#475569".into() }
fn invoice_cells(value:&str)->Result<Vec<[String;4]>, &'static str> {
    let rows:Vec<[String;4]>=serde_json::from_str(value).map_err(|_| "invalid invoice rows")?;
    if rows.len()>100 { return Err("too many invoice rows"); }
    for row in &rows {
        check_str(&row[0],1020)?;
        if row[0].chars().count()>255 || row[0].chars().any(|c| c.is_control()) { return Err("invalid invoice description"); }
        if row[1].parse::<u32>().ok().filter(|n| *n>0).is_none() || row[1].len()>10 { return Err("invalid invoice quantity"); }
        for value in &row[2..] { if !money(value) { return Err("invalid invoice amount"); } }
    }
    Ok(rows)
}
fn money(s:&str)->bool {
    if let Some((a,b))=s.split_once('.') {
        !a.is_empty() && a.len()<=8 && a.bytes().all(|v| v.is_ascii_digit()) && b.len()==2 && b.bytes().all(|v| v.is_ascii_digit())
    } else { false }
}
const INVOICE_HEADERS:[&str;4]=["Description","Quantity","Unit price","Subtotal"];
fn shown_invoice(value:&str, headers:&[usize])->String {
    let rows=invoice_cells(value).unwrap_or_default();
    if rows.is_empty() { return "No itemized charges recorded".into(); }
    let mut text=String::new();
    for (i,r) in rows.iter().enumerate() {
        if headers.contains(&i) { text.push(' '); text.push_str(&INVOICE_HEADERS.join(" ")); }
        for v in r { text.push(' '); text.push_str(v); }
    }
    text
}
fn shown_value(label:&str,value:&str,headers:&[usize])->String {
    if label=="Invoice Items" { shown_invoice(value,headers) } else { value.to_string() }
}
fn valid_rgb_op(op:&lopdf::content::Operation)->bool {
    op.operands.len()==3 && op.operands.iter().all(|v| match v {
        lopdf::Object::Integer(n)=>*n==0||*n==1,
        lopdf::Object::Real(n)=>n.is_finite() && *n>=0.0 && *n<=1.0,
        _=>false,
    })
}
fn styled_text(text:String, size:u8, color:(u8,u8,u8))->elements::StyledElement<elements::Paragraph> {
    use genpdf::style::{Style,Color};
    // The outer style controls both baseline and reserved line height in genpdf.
    elements::StyledElement::new(elements::Paragraph::new(text),
        Style::new().with_font_size(size).with_color(Color::Rgb(color.0,color.1,color.2)))
}
fn push_styled(doc:&mut Document,font:&rusttype::Font<'_>,text:&str,size:u8,color:(u8,u8,u8))->Result<(), &'static str> {
    for line in wrap_lines(text, |c| font.glyph(c).scaled(rusttype::Scale::uniform((size as f32)*1.2+2.0)).h_metrics().advance_width.max(0.0)+1.5)? {
        doc.push(styled_text(line,size,color));
    }
    Ok(())
}
type InvoiceHeaders=std::rc::Rc<std::cell::RefCell<Vec<usize>>>;
fn row_fits(available:genpdf::Mm, row:genpdf::Mm, heading:genpdf::Mm)->bool {
    available >= row + heading
}
struct AtomicInvoiceTable {
    rows: Vec<[String;4]>,
    next: usize,
    headers: InvoiceHeaders,
    color: (u8,u8,u8),
}
impl genpdf::Element for AtomicInvoiceTable {
    fn render(&mut self, context:&genpdf::Context, mut area:genpdf::render::Area<'_>, style:genpdf::style::Style)
        ->Result<genpdf::RenderResult,genpdf::error::Error> {
        use genpdf::{Mm,Position,RenderResult};
        use genpdf::style::{Style,Color};
        use genpdf::error::{Error,ErrorKind};
        let fail=|| Error::new("invoice row cannot be laid out safely",ErrorKind::InvalidData);
        let cache=&context.font_cache;
        let body:Style=style.and(Style::new().with_font_size(10).with_color(Color::Rgb(24,37,47)));
        let head:Style=style.and(Style::new().with_font_size(10).with_color(Color::Rgb(self.color.0,self.color.1,self.color.2)));
        let width=area.size().width;
        let widths=[width*0.49,width*0.13,width*0.19,width*0.19];
        let xs=[Mm::from(0.0),widths[0],widths[0]+widths[1],widths[0]+widths[1]+widths[2]];
        let gap=Mm::from(3.0);
        let step=body.line_height(cache)*1.18;
        let header_h=head.line_height(cache)*1.55;
        let mut result=RenderResult::default();
        let mut first=true;
        while self.next<self.rows.len() {
            let row=&self.rows[self.next];
            let cap=widths[0]-gap;
            let lines=wrap_lines(&row[0],|c| ((f64::from(body.char_width(cache,c))+0.25)/f64::from(cap)*400.0) as f32).map_err(|_|fail())?;
            if lines.is_empty() || lines.iter().all(|v|v.trim().is_empty()) { return Err(fail()); }
            let row_h=step*(lines.len() as f64)+Mm::from(1.1);
            // Preflight all columns before writing any glyph for the row.
            if lines.iter().any(|v|body.str_width(cache,v)>cap) || (1..4).any(|i|body.str_width(cache,&row[i])>widths[i]-gap) {
                return Err(fail());
            }
            if !row_fits(area.size().height,row_h,if first{header_h}else{Mm::from(0.0)}) {break;}
            if first {
                for i in 0..4 {
                    let w=head.str_width(cache,INVOICE_HEADERS[i]);
                    if w>widths[i]-gap {return Err(fail());}
                    let x=if i==0 {xs[i]} else {xs[i]+widths[i]-gap-w};
                    if !area.print_str(cache,Position::new(x,0),head,INVOICE_HEADERS[i])? {return Err(fail());}
                }
                self.headers.borrow_mut().push(self.next);
                result.size.height+=header_h;
                area.add_offset(Position::new(0,header_h));
                first=false;
            }
            for (j,line) in lines.iter().enumerate() {
                if !area.print_str(cache,Position::new(0,step*(j as f64)),body,line)? {return Err(fail());}
            }
            // Numbers stay with their description on the same page and are right aligned.
            for i in 1..4 {
                let w=body.str_width(cache,&row[i]);
                let x=xs[i]+widths[i]-gap-w;
                if !area.print_str(cache,Position::new(x,0),body,&row[i])? {return Err(fail());}
            }
            result.size.height+=row_h;
            area.add_offset(Position::new(0,row_h));
            self.next+=1;
        }
        if result.size.height>Mm::from(0.0) {result.size.width=width;}
        result.has_more=self.next<self.rows.len();
        Ok(result)
    }
}
fn push_invoice_table(doc:&mut Document,font:&rusttype::Font<'_>,value:&str,color:(u8,u8,u8),headers:InvoiceHeaders)->Result<(), &'static str> {
    let rows=invoice_cells(value)?;
    if rows.is_empty() { return push_wrapped(doc,font,"No itemized charges recorded"); }
    doc.push(AtomicInvoiceTable{rows,next:0,headers,color});
    Ok(())
}

fn check_branding(d: &PrintRequest) -> Result<(), &'static str> {
    match (&d.branding, d.protocol) {
        (None, 1) => Ok(()),
        (Some(b), 2 | 3) => {
            palette_rgb(&b.primary_color)?; palette_rgb(&b.secondary_color)?;
            match (&b.verification_url,&b.issued_at,&b.verification_rows) {
                (None,None,None)=>(),
                (Some(url),Some(stamp),Some(rows)) if d.protocol==3 => {
                    let n=rows.len();
                    if !(29..=65).contains(&n)||rows.iter().any(|r|r.len()!=n||!r.bytes().all(|c|matches!(c,b'0'|b'1')))||rows.iter().enumerate().any(|(i,r)|r.bytes().enumerate().any(|(j,c)|(i<4||j<4||i>=n-4||j>=n-4)&&c==b'1')){return Err("invalid verification matrix");}
                    let (origin,token)=url.split_once("/api/v1/printing/verify/").ok_or("invalid verification URL")?;
                    let host=origin.strip_prefix("https://").ok_or("invalid verification origin")?;
                    if host.is_empty() || host.len()>100 || !host.bytes().all(|c|c.is_ascii_alphanumeric()||matches!(c,b'.'|b'-'|b':')) || token.len()!=32 || !token.bytes().all(|c|matches!(c,b'0'..=b'9'|b'a'..=b'f')) || stamp.is_empty() || stamp.len()>100 || stamp.chars().any(|c|c.is_control()) {return Err("invalid verification metadata");}
                },_=>return Err("invalid verification metadata"),
            }
            if b.clinic_id != d.clinic_id || b.lines.len() < 2 || b.lines.len() > 7
                || !b.lines[0].starts_with("Organisation: ") || !b.lines[1].starts_with("Clinic: ")
                || b.branding_sha256.len() != 64 || !b.branding_sha256.bytes().all(|c| c.is_ascii_hexdigit()) {
                return Err("invalid branding");
            }
            let labels = ["Organisation: ", "Clinic: ", "Address: ", "City: ", "Country: ", "Phone: ", "Email: "];
            let mut last = None;
            for line in &b.lines {
                check_str(line, 1040)?;
                if line.chars().any(|c| c.is_control()) { return Err("invalid branding identity"); }
                let i = labels.iter().position(|p| line.starts_with(*p)).ok_or("invalid branding identity")?;
                if last.map_or(false, |v| i <= v) || line[labels[i].len()..].trim().is_empty() { return Err("invalid branding identity"); }
                last = Some(i);
            }
            if let Some(logo) = &b.logo {
                if logo.width == 0 || logo.height == 0 || logo.width > 256 || logo.height > 256
                    || logo.rgb_hex.len() != (logo.width as usize)*(logo.height as usize)*6
                    || !logo.rgb_hex.bytes().all(|x| x.is_ascii_hexdigit()) { return Err("invalid logo"); }
            }
            Ok(())
        }
        _ => Err("invalid branding protocol"),
    }
}
fn heading_lines(d: &PrintRequest) -> Vec<String> {
    match &d.branding {
        None => vec!["CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY".into()],
        Some(b) => {
            let mut v = b.lines.clone();
            v.push("CONTROLLED DOCUMENT COPY".into());
            v.push(format!("BRANDING SHA-256: {}", b.branding_sha256));
            v
        }
    }
}
// Embed only bounded, already-normalized RGB pixels, after L02 text repair.
// Each logo stream is prepended as a balanced graphics-state block; the original
// corrected text stream remains byte-for-byte intact.
fn add_brand_logo(input: &[u8], logo: &Logo) -> Result<Vec<u8>, &'static str> {
    use lopdf::{Dictionary, Object, Stream};
    const BAD: &str = "invalid branded pdf";
    let mut pdf = lopdf::Document::load_mem(input).map_err(|_| BAD)?;
    let rgb: Vec<u8> = (0..logo.rgb_hex.len()).step_by(2)
        .map(|i| u8::from_str_radix(&logo.rgb_hex[i..i+2],16).map_err(|_| BAD))
        .collect::<Result<_,_>>()?;
    let mut dict = Dictionary::new();
    dict.set("Type", Object::Name(b"XObject".to_vec()));
    dict.set("Subtype", Object::Name(b"Image".to_vec()));
    dict.set("Width", Object::Integer(logo.width.into()));
    dict.set("Height", Object::Integer(logo.height.into()));
    dict.set("ColorSpace", Object::Name(b"DeviceRGB".to_vec()));
    dict.set("BitsPerComponent", Object::Integer(8));
    let image_id = pdf.add_object(Stream::new(dict, rgb));
    let pages = pdf.get_pages();
    if pages.is_empty() || pages.len()>100 { return Err(BAD); }
    let scale = (150.0 / logo.width as f64).min(50.0 / logo.height as f64);
    let w = logo.width as f64 * scale;
    let h = logo.height as f64 * scale;
    // A4 default page, top edge y=841.89. Top margin is 39 mm for logos.
    let content = format!("q\n{:.5} 0 0 {:.5} 45 {:.5} cm\n/CSPBrandLogo Do\nQ\n", w, h, 800.0-h).into_bytes();
    let overlay_id = pdf.add_object(Stream::new(Dictionary::new(), content));
    // Pinned generator emits a single content stream and indirect resources.
    // Do not replace the font dictionary or invoke a PDF text rewrite.
    for (_, page_id) in pages {
        let old = pdf.get_object(page_id).and_then(Object::as_dict).and_then(|p| p.get(b"Contents")).and_then(Object::as_reference).map_err(|_| BAD)?;
        let rid = pdf.get_object(page_id).and_then(Object::as_dict).and_then(|p| p.get(b"Resources")).and_then(Object::as_reference).map_err(|_| BAD)?;
        let resources = pdf.get_object_mut(rid).and_then(Object::as_dict_mut).map_err(|_| BAD)?;
        // genpdf has no image resources. Resources can be shared across pages.
        if resources.get(b"XObject").is_err() {
            resources.set("XObject", Object::Dictionary(Dictionary::new()));
        }
        let xs = resources.get_mut(b"XObject").and_then(Object::as_dict_mut).map_err(|_| BAD)?;
        if let Ok(existing) = xs.get(b"CSPBrandLogo") {
            if existing.as_reference().map_err(|_| BAD)? != image_id { return Err(BAD); }
        } else {
            xs.set("CSPBrandLogo", Object::Reference(image_id));
        }
        let page = pdf.get_object_mut(page_id).and_then(Object::as_dict_mut).map_err(|_| BAD)?;
        page.set("Contents", Object::Array(vec![Object::Reference(overlay_id),Object::Reference(old)]));
    }
    pdf.compress();
    let mut result = Vec::new();
    pdf.save_to(&mut result).map_err(|_| BAD)?;
    Ok(result)
}


fn add_brand_decoration(input: &[u8], d:&PrintRequest, brand:&Branding) -> Result<Vec<u8>, &'static str> {
    use lopdf::{Dictionary,Object,Stream};
    const BAD:&str="invalid decorated PDF";
    let (r,g,b)=palette_rgb(&brand.primary_color)?;
    let (r2,g2,b2)=palette_rgb(&brand.secondary_color)?;
    let rgb=|v:u8| v as f64/255.0;
    let mut pdf=lopdf::Document::load_mem(input).map_err(|_| BAD)?;
    let mut fd=Dictionary::new();
    fd.set("Type",Object::Name(b"Font".to_vec()));fd.set("Subtype",Object::Name(b"Type1".to_vec()));
    fd.set("BaseFont",Object::Name(b"Helvetica".to_vec()));fd.set("Encoding",Object::Name(b"WinAnsiEncoding".to_vec()));
    let fid=pdf.add_object(Object::Dictionary(fd));
    let pages=pdf.get_pages();let total=pages.len();
    if total==0 || total>100 {return Err(BAD);}
    for (index,(_,pid)) in pages.into_iter().enumerate() {
        let page=pdf.get_object(pid).and_then(Object::as_dict).map_err(|_| BAD)?;
        let old=page.get(b"Contents").map_err(|_| BAD)?.clone();
        let rid=page.get(b"Resources").and_then(Object::as_reference).map_err(|_| BAD)?;
        let fonts_id=pdf.get_object(rid).and_then(Object::as_dict).and_then(|v| v.get(b"Font")).and_then(Object::as_reference).map_err(|_| BAD)?;
        let fonts=pdf.get_object_mut(fonts_id).and_then(Object::as_dict_mut).map_err(|_| BAD)?;
        if let Ok(v)=fonts.get(b"CSPFooter") { if v.as_reference().map_err(|_| BAD)?!=fid {return Err(BAD);} }
        else {fonts.set("CSPFooter",Object::Reference(fid));}
        // Every page is A4 from the pinned generator. Footer and rules are
        // wholly outside the content frame (top 18/39 mm, bottom 24 mm).
        let foot=format!("Clinic reference: {} | Record reference: {} | Page {} of {}",d.clinic_id,d.record_id,index+1,total);
        let overlay=format!("q\n{:.5} {:.5} {:.5} rg\n45 815 505 4 re f\n{:.5} {:.5} {:.5} RG\n0.8 w\n45 57 m 550 57 l S\n0.52 0.12 0.12 rg\nBT /CSPFooter 8 Tf 1 0 0 1 45 40 Tm ({}) Tj ET\n0.12 0.15 0.18 rg\nBT /CSPFooter 8 Tf 1 0 0 1 45 24 Tm ({}) Tj ET\nQ\n",rgb(r),rgb(g),rgb(b),rgb(r2),rgb(g2),rgb(b2),d.watermark,foot);
        // Fixed warnings contain no PDF string metacharacters; numeric refs only.
        if d.watermark.contains('(')||d.watermark.contains(')')||d.watermark.contains('\\') {return Err(BAD);}
        let oid=pdf.add_object(Stream::new(Dictionary::new(),overlay.into_bytes()));
        let mut contents=match old {Object::Reference(id)=>vec![Object::Reference(id)],Object::Array(v)=>v,_=>return Err(BAD)};
        if contents.is_empty()||contents.len()>2||contents.iter().any(|v| v.as_reference().is_err()) {return Err(BAD);}
        contents.push(Object::Reference(oid));
        pdf.get_object_mut(pid).and_then(Object::as_dict_mut).map_err(|_| BAD)?.set("Contents",Object::Array(contents));
    }
    pdf.compress();let mut out=Vec::new();pdf.save_to(&mut out).map_err(|_| BAD)?;Ok(out)
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
    check_branding(d)?;
    if (d.protocol == 3) != d.professional.is_some() { return Err("invalid professional protocol"); }
    if d.clinic_id == 0 || d.record_id == 0 || d.is_signed || d.released_to_chart
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
    let extended=d.kind=="invoice" && matches!(d.protocol,2|3) && d.fields.len()==names.len()+2;
    let extra: &[&str] = match d.kind.as_str() {
        "invoice" => &["Patient Name", "Created Date"],
        "appointment" => &["Patient Name", "Assigned Staff Name"],
        "lab_order" => &["Patient Name", "Age At Order", "Recorded Gender", "Ordered By", "Ordered Tests"],
        "radiology_draft" => &["Patient Name", "Modality", "Body Part", "Report Author", "Signature Status"],
        _ => &[],
    };
    let enriched = d.protocol==3 && d.fields.len()==names.len()+extra.len()+(if d.kind=="invoice" {2} else {0});
    let extended = extended || (enriched && d.kind=="invoice");
    if d.fields.len()!=names.len() && !extended && !enriched { return Err("invalid source fields"); }
    if extended && d.fields.len()!=names.len()+2 && !enriched { return Err("invalid source fields"); }
    for ((label,text),expected) in d.fields.iter().zip(names) {
        if label!=expected { return Err("unexpected source fields"); }
        check_str(text,if label=="Report Text" {12_000} else {300})?;
    }
    if extended {
        let (k,v)=&d.fields[names.len()];
        if k!="Invoice Items" {return Err("invalid invoice items");}
        check_str(v,12_000)?;invoice_cells(v)?;
        let (k,v)=&d.fields[names.len()+1];
        if k!="Outstanding Balance" || !money(v) {return Err("invalid outstanding balance");}
    }
    if enriched {
        let offset=names.len()+(if extended {2} else {0});
        for ((label,value),expected) in d.fields[offset..].iter().zip(extra) {
            if label!=expected { return Err("invalid enriched source field"); }
            check_str(value,if label=="Ordered Tests" {12000} else {300})?;
            if label=="Ordered Tests" { ordered_tests(value)?; }
            if label=="Signature Status" && value!="Unsigned controlled copy" { return Err("invalid signature status"); }
        }
    }
    Ok(())
}

fn ordered_tests(value:&str)->Result<Vec<[String;3]>, &'static str> {
    let rows:Vec<[String;3]>=serde_json::from_str(value).map_err(|_|"invalid ordered tests")?;
    if rows.is_empty() || rows.len()>100 { return Err("invalid ordered tests"); }
    for row in &rows {
        if row[2]!="Not recorded" {return Err("unrecorded lab priority");}
        for cell in row {check_str(cell,600)?;if cell.trim().is_empty()||cell.chars().count()>150||cell.chars().any(|c|c.is_control()){return Err("invalid ordered test cell");}}
    }
    Ok(rows)
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

// Budget is intentionally below the printable A4 width (about 516 pt at
// 14 mm margins). Use larger-than-rendered metrics plus a spacing reserve
// for native PDF viewers/printer drivers. Validate actual rendered geometry
// separately; neither a character count nor metrics alone prove visibility.
const PRINT_LINE_BUDGET: f32 = 400.0;

fn wrap_lines<F>(text: &str, width: F) -> Result<Vec<String>, &'static str>
where
    F: Fn(char) -> f32,
{
    let mut result = Vec::new();
    for line in text.split('\n') {
        let normalized = line.replace('\t', "    ");
        let chars: Vec<char> = normalized.chars().collect();
        if chars.is_empty() {
            result.push(" ".into());
            continue;
        }
        let mut start = 0;
        while start < chars.len() {
            let mut end = start;
            let mut used = 0.0;
            let mut last_space = None;
            while end < chars.len() {
                let advance = width(chars[end]);
                if !advance.is_finite() || advance <= 0.0 || advance > PRINT_LINE_BUDGET {
                    return Err("invalid glyph width");
                }
                if used + advance > PRINT_LINE_BUDGET {
                    break;
                }
                used += advance;
                end += 1;
                if chars[end - 1].is_whitespace() {
                    last_space = Some(end);
                }
            }
            if end < chars.len() {
                if let Some(split) = last_space {
                    // Prefer whole words, without dropping any original characters.
                    end = split;
                }
            }
            if end == start {
                return Err("print line cannot advance");
            }
            result.push(chars[start..end].iter().collect());
            start = end;
        }
    }
    Ok(result)
}

fn push_wrapped(doc: &mut Document, font: &rusttype::Font<'_>, text: &str) -> Result<(), &'static str> {
    for line in wrap_lines(text, |c| {
        font.glyph(c)
            .scaled(rusttype::Scale::uniform(14.0))
            .h_metrics().advance_width.max(0.0) + 3.0
    })? {
        doc.push(elements::Paragraph::new(line));
    }
    Ok(())
}

// The locked genpdf/printpdf pair emits two-byte codes even for a simple
// WinAnsi Type1 font. Repair only the known, renderer-generated text operands
// before returning PDF bytes. Never touch embedded CID font content.
fn winansi_byte(c: char) -> Option<u8> {
    match c {
        ' '..='~' | '\u{00a0}'..='\u{00ff}' => Some(c as u8),
        _ => {
            const SPECIAL: [char; 32] = [
                '€','\0','‚','ƒ','„','…','†','‡','ˆ','‰','Š','‹','Œ','\0','Ž','\0',
                '\0','‘','’','“','”','•','–','—','˜','™','š','›','œ','\0','ž','Ÿ',
            ];
            if c == '\0' { return None; }
            SPECIAL.iter().position(|&x| x == c).map(|i| 0x80 + i as u8)
        }
    }
}

fn valid_winansi_code(b: u8) -> bool {
    matches!(b, 0x20..=0x7e | 0xa0..=0xff | 0x80 | 0x82..=0x8c |
        0x8e | 0x91..=0x9c | 0x9e..=0x9f)
}

fn simple_font_code(bytes: &[u8]) -> Result<Vec<u8>, &'static str> {
    if bytes.is_empty() || bytes.len() % 2 != 0 { return Err("invalid simple font code length"); }
    bytes.chunks_exact(2).map(|pair| {
        let code = u16::from_be_bytes([pair[0], pair[1]]);
        let b = if pair[0] == 0 { pair[1] } else {
            char::from_u32(u32::from(code)).and_then(winansi_byte).ok_or("invalid WinAnsi code")?
        };
        if !valid_winansi_code(b) { return Err("invalid WinAnsi byte"); }
        Ok(b)
    }).collect()
}

fn expected_winansi(d: &PrintRequest, headers:&[usize]) -> Result<Vec<u8>, &'static str> {
    let mut text = format!("{} {} {} Clinic reference: {} Record reference: {} Source version: {} ",
        heading_lines(d).join(" "), d.title, d.watermark, d.clinic_id, d.record_id, d.source_version);
    for (k, v) in &d.fields { text.push_str(k); text.push(':'); text.push_str(&shown_value(k,v,headers)); }
    text.push_str("SOURCE SHA-256:"); text.push_str(&d.source_digest);
    text.push_str("This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.");
    text.chars().filter(|c| !c.is_whitespace()).map(|c| winansi_byte(c).ok_or("unsupported expected WinAnsi text")).collect()
}

fn correct_builtin_encoding(input: &[u8], expected: &[u8]) -> Result<Vec<u8>, &'static str> {
    use lopdf::{content::Content, Object, Stream};
    const BAD: &str = "invalid generated simple font PDF";
    if input.len() > MAX_PDF { return Err(BAD); }
    let mut pdf = lopdf::Document::load_mem(input).map_err(|_| BAD)?;
    let mut observed = Vec::new();
    let pages = pdf.get_pages();
    if pages.is_empty() || pages.len() > 100 { return Err(BAD); }
    for (_, page_id) in pages {
        let page = pdf.get_object(page_id).and_then(Object::as_dict).map_err(|_| BAD)?;
        let resource_id = page.get(b"Resources").and_then(Object::as_reference).map_err(|_| BAD)?;
        let resources = pdf.get_object(resource_id).and_then(Object::as_dict).map_err(|_| BAD)?;
        let font_id = resources.get(b"Font").and_then(Object::as_reference).map_err(|_| BAD)?;
        let fonts = pdf.get_object(font_id).and_then(Object::as_dict).map_err(|_| BAD)?;
        // L01 emits exactly one page stream and these four Helvetica resources.
        let mut allowed_fonts = Vec::new();
        for name in [b"Helvetica".as_ref(), b"Helvetica-Bold".as_ref(), b"Helvetica-Oblique".as_ref(), b"Helvetica-BoldOblique".as_ref()] {
            let id = fonts.get(name).and_then(Object::as_reference).map_err(|_| BAD)?;
            let font = pdf.get_object(id).and_then(Object::as_dict).map_err(|_| BAD)?;
            if font.get(b"Subtype").and_then(Object::as_name).map_err(|_| BAD)? != b"Type1"
                || font.get(b"BaseFont").and_then(Object::as_name).map_err(|_| BAD)? != name
                || font.get(b"Encoding").and_then(Object::as_name).map_err(|_| BAD)? != b"WinAnsiEncoding"
                || font.get(b"ToUnicode").is_ok() { return Err(BAD); }
            allowed_fonts.push(name);
        }
        let stream_id = page.get(b"Contents").and_then(Object::as_reference).map_err(|_| BAD)?;
        let stream = pdf.get_object(stream_id).and_then(Object::as_stream).map_err(|_| BAD)?;
        // The pinned generator emits uncompressed page streams. Reject any new shape.
        if stream.dict.get(b"Filter").is_ok() || stream.content.len() > MAX_PDF { return Err(BAD); }
        let mut content = Content::decode(&stream.content).map_err(|_| BAD)?;
        let mut in_text = false;
        let mut font_ok = false;
        let mut count = 0usize;
        for op in &mut content.operations {
            match op.operator.as_str() {
                "BT" if !in_text => { in_text = true; font_ok = false; }
                "ET" if in_text => { in_text = false; font_ok = false; }
                "Tf" if in_text && op.operands.len() == 2 => {
                    let name = op.operands[0].as_name().map_err(|_| BAD)?;
                    if !allowed_fonts.contains(&name) { return Err(BAD); }
                    font_ok = true;
                }
                "TJ" if in_text && font_ok && op.operands.len() == 1 => {
                    let items = match &mut op.operands[0] { Object::Array(a) => a, _ => return Err(BAD) };
                    for item in items {
                        match item {
                            Object::String(bytes, _) => {
                                let converted = simple_font_code(bytes)?;
                                observed.extend(converted.iter().copied().filter(|b| !matches!(*b, 0x20 | 0xa0)));
                                *bytes = converted;
                                count += 1;
                            }
                            Object::Integer(_) | Object::Real(_) => (),
                            _ => return Err(BAD),
                        }
                    }
                }
                "TL" | "Td" if in_text => (),
                "rg" | "RG" if valid_rgb_op(op) => (),
                "BDC" | "EMC" | "q" | "Q" if !in_text => (),
                _ => return Err(BAD),
            }
        }
        if in_text || count == 0 { return Err(BAD); }
        let encoded = content.encode().map_err(|_| BAD)?;
        let updated = Stream::new(stream.dict.clone(), encoded);
        pdf.objects.insert(stream_id, Object::Stream(updated));
    }
    // A malformed or ambiguous code must never silently change clinical data.
    // Compare all non-whitespace source characters before returning anything.
    if observed != expected { return Err("generated simple font content mismatch"); }
    let mut output = Vec::new();
    pdf.save_to(&mut output).map_err(|_| BAD)?;
    Ok(output)
}

// Source-aware mapping for the pinned generator's regular embedded TrueType font.
// Distinct Unicode scalar values may share a glyph ID; allocate distinct CIDs.
// The original glyph, width and kerning operands are preserved.
#[derive(Default)]
struct UnicodeCodes {
    chars: std::collections::BTreeMap<char, u16>,
    entries: Vec<(char, u16)>,
}
impl UnicodeCodes {
    fn assign(&mut self, c: char, gid: u16) -> Result<u16, &'static str> {
        if gid == 0 { return Err("missing Unicode glyph"); }
        if let Some(&cid) = self.chars.get(&c) {
            if self.entries[usize::from(cid) - 1].1 != gid { return Err("glyph mapping drift"); }
            return Ok(cid);
        }
        let cid = u16::try_from(self.entries.len() + 1).map_err(|_| "too many Unicode codes")?;
        self.entries.push((c, gid));
        self.chars.insert(c, cid);
        Ok(cid)
    }
    fn cmap(&self) -> Vec<u8> {
        use std::fmt::Write as _;
        let mut out = String::from("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /CSPSourceUnicode def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n");
        for (block, entries) in self.entries.chunks(100).enumerate() {
            let _ = writeln!(out, "{} beginbfchar", entries.len());
            for (i, &(c, _)) in entries.iter().enumerate() {
                let mut units = [0u16; 2];
                let _ = write!(out, "<{:04X}> <", block * 100 + i + 1);
                for u in c.encode_utf16(&mut units) { let _ = write!(out, "{:04X}", u); }
                out.push_str(">\n");
            }
            out.push_str("endbfchar\n");
        }
        out.push_str("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n");
        out.into_bytes()
    }
}
fn source_unicode_chars(d: &PrintRequest, headers:&[usize]) -> Vec<char> {
    let mut text = format!("{} {} {} Clinic reference: {} Record reference: {} Source version: {} ",
        heading_lines(d).join(" "), d.title, d.watermark, d.clinic_id, d.record_id, d.source_version);
    for (k, v) in &d.fields { text.push_str(k); text.push(':'); text.push_str(&shown_value(k,v,headers)); }
    text.push_str("SOURCE SHA-256:"); text.push_str(&d.source_digest);
    text.push_str("This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.");
    text.chars().filter(|c| !c.is_whitespace()).collect()
}
fn consume_unicode_gid<I: Iterator<Item=char>, F: Fn(char)->u16>(gid: u16, space: u16, expected: &mut I, glyph: &F, codes: &mut UnicodeCodes) -> Result<u16, &'static str> {
    let c = if gid == space { ' ' } else { expected.next().ok_or("unexpected embedded glyph")? };
    if glyph(c) != gid { return Err("embedded glyph/source mismatch"); }
    codes.assign(c, gid)
}
fn embedded_widths(items: &[lopdf::Object]) -> Result<std::collections::BTreeMap<u16, lopdf::Object>, &'static str> {
    use lopdf::Object;
    const BAD: &str = "invalid embedded width table";
    let mut widths = std::collections::BTreeMap::new();
    let mut i = 0;
    while i < items.len() {
        let start = u16::try_from(items[i].as_i64().map_err(|_| BAD)?).map_err(|_| BAD)?;
        i += 1;
        match items.get(i).ok_or(BAD)? {
            Object::Array(a) => {
                for (j, w) in a.iter().enumerate() {
                    if !matches!(w, Object::Integer(_) | Object::Real(_)) { return Err(BAD); }
                    let gid = start.checked_add(u16::try_from(j).map_err(|_| BAD)?).ok_or(BAD)?;
                    if widths.insert(gid, w.clone()).is_some() { return Err(BAD); }
                }
                i += 1;
            }
            Object::Integer(end) => {
                let end = u16::try_from(*end).map_err(|_| BAD)?;
                let w = items.get(i+1).ok_or(BAD)?;
                if end < start || !matches!(w, Object::Integer(_) | Object::Real(_)) { return Err(BAD); }
                for gid in start..=end { if widths.insert(gid, w.clone()).is_some() { return Err(BAD); } }
                i += 2;
            }
            _ => return Err(BAD),
        }
    }
    Ok(widths)
}
fn correct_embedded_encoding(input: &[u8], d: &PrintRequest, font: &rusttype::Font<'_>, headers:&[usize]) -> Result<Vec<u8>, &'static str> {
    use lopdf::{content::Content, Dictionary, Object, Stream};
    const BAD: &str = "invalid generated embedded font PDF";
    // Bounded internal representation only. The public output cap stays 3 MiB.
    if input.len() > 8 * 1024 * 1024 { return Err(BAD); }
    let mut pdf = lopdf::Document::load_mem(input).map_err(|_| BAD)?;
    let source = source_unicode_chars(d,headers);
    let mut expected = source.into_iter();
    let glyph = |c: char| font.glyph(c).id().0;
    let space = glyph(' ');
    if space == 0 { return Err(BAD); }
    let mut codes = UnicodeCodes::default();
    let mut regular_id = None;
    let pages = pdf.get_pages();
    if pages.is_empty() || pages.len() > 100 { return Err(BAD); }
    for (_, page_id) in pages {
        let page = pdf.get_object(page_id).and_then(Object::as_dict).map_err(|_| BAD)?;
        let resource_id = page.get(b"Resources").and_then(Object::as_reference).map_err(|_| BAD)?;
        let resources = pdf.get_object(resource_id).and_then(Object::as_dict).map_err(|_| BAD)?;
        let font_id = resources.get(b"Font").and_then(Object::as_reference).map_err(|_| BAD)?;
        let fonts = pdf.get_object(font_id).and_then(Object::as_dict).map_err(|_| BAD)?;
        let id = fonts.get(b"F0").and_then(Object::as_reference).map_err(|_| BAD)?;
        if let Some(prior) = regular_id { if prior != id { return Err(BAD); } }
        regular_id = Some(id);
        let f = pdf.get_object(id).and_then(Object::as_dict).map_err(|_| BAD)?;
        if f.get(b"Subtype").and_then(Object::as_name).map_err(|_| BAD)? != b"Type0"
            || f.get(b"Encoding").and_then(Object::as_name).map_err(|_| BAD)? != b"Identity-H" { return Err(BAD); }
        let stream_id = page.get(b"Contents").and_then(Object::as_reference).map_err(|_| BAD)?;
        let stream = pdf.get_object(stream_id).and_then(Object::as_stream).map_err(|_| BAD)?;
        if stream.dict.get(b"Filter").is_ok() || stream.content.len() > MAX_PDF { return Err(BAD); }
        let mut content = Content::decode(&stream.content).map_err(|_| BAD)?;
        let mut in_text = false;
        let mut font_ok = false;
        let mut count = 0usize;
        for op in &mut content.operations {
            match op.operator.as_str() {
                "BT" if !in_text => { in_text = true; font_ok = false; }
                "ET" if in_text => { in_text = false; font_ok = false; }
                "Tf" if in_text && op.operands.len() == 2 => {
                    if op.operands[0].as_name().map_err(|_| BAD)? != b"F0" { return Err(BAD); }
                    font_ok = true;
                }
                "TJ" if in_text && font_ok && op.operands.len() == 1 => {
                    let items = match &mut op.operands[0] { Object::Array(a) => a, _ => return Err(BAD) };
                    for item in items {
                        match item {
                            Object::String(bytes, _) => {
                                if bytes.is_empty() || bytes.len() % 2 != 0 { return Err(BAD); }
                                for pair in bytes.chunks_exact_mut(2) {
                                    let gid = u16::from_be_bytes([pair[0], pair[1]]);
                                    let cid = consume_unicode_gid(gid, space, &mut expected, &glyph, &mut codes)?;
                                    pair.copy_from_slice(&cid.to_be_bytes());
                                    count += 1;
                                }
                            }
                            Object::Integer(_) | Object::Real(_) => (),
                            _ => return Err(BAD),
                        }
                    }
                }
                "TL" | "Td" if in_text => (),
                "rg" | "RG" if valid_rgb_op(op) => (),
                "BDC" | "EMC" | "q" | "Q" if !in_text => (),
                _ => return Err(BAD),
            }
        }
        if in_text || count == 0 { return Err(BAD); }
        let encoded = content.encode().map_err(|_| BAD)?;
        let updated = Stream::new(stream.dict.clone(), encoded);
        pdf.objects.insert(stream_id, Object::Stream(updated));
    }
    if expected.next().is_some() { return Err("missing embedded source characters"); }
    let font_id = regular_id.ok_or(BAD)?;
    let f = pdf.get_object(font_id).and_then(Object::as_dict).map_err(|_| BAD)?;
    let descendants = f.get(b"DescendantFonts").and_then(Object::as_array).map_err(|_| BAD)?;
    // printpdf may inline the descendant dictionary (the L02 artifact does).
    if descendants.len() != 1 { return Err(BAD); }
    let descendant_id = descendants[0].as_reference().ok();
    let mut descendant = match descendant_id {
        Some(id) => pdf.get_object(id).and_then(Object::as_dict).map_err(|_| BAD)?.clone(),
        None => descendants[0].as_dict().map_err(|_| BAD)?.clone(),
    };
    if descendant.get(b"Subtype").and_then(Object::as_name).map_err(|_| BAD)? != b"CIDFontType2" { return Err(BAD); }
    if let Ok(map) = descendant.get(b"CIDToGIDMap") {
        if map.as_name().map_err(|_| BAD)? != b"Identity" { return Err(BAD); }
    }
    let widths = embedded_widths(descendant.get(b"W").and_then(Object::as_array).map_err(|_| BAD)?)?;
    let default_width = descendant.get(b"DW").map_err(|_| BAD)?.clone();
    if !matches!(default_width, Object::Integer(_) | Object::Real(_)) { return Err(BAD); }
    let mut gidmap = vec![0, 0];
    let mut new_widths = Vec::new();
    for &(_, gid) in &codes.entries {
        gidmap.extend_from_slice(&gid.to_be_bytes());
        new_widths.push(widths.get(&gid).unwrap_or(&default_width).clone());
    }
    let cmap_id = pdf.add_object(Stream::new(Dictionary::new(), codes.cmap()));
    let gidmap_id = pdf.add_object(Stream::new(Dictionary::new(), gidmap));
    descendant.set("CIDToGIDMap", Object::Reference(gidmap_id));
    descendant.set("W", Object::Array(vec![Object::Integer(1), Object::Array(new_widths)]));
    let new_descendant = match descendant_id {
        Some(id) => { pdf.objects.insert(id, Object::Dictionary(descendant)); Object::Reference(id) }
        None => Object::Dictionary(descendant),
    };
    let f = pdf.get_object_mut(font_id).and_then(Object::as_dict_mut).map_err(|_| BAD)?;
    f.set("DescendantFonts", Object::Array(vec![new_descendant]));
    f.set("ToUnicode", Object::Reference(cmap_id));
    pdf.prune_objects();
    // Lossless compression gives the embedded fonts and longer reports room under the existing cap.
    pdf.compress();
    let mut output = Vec::new();
    pdf.save_to(&mut output).map_err(|_| BAD)?;
    Ok(output)
}

fn render(d: &PrintRequest) -> Result<Vec<u8>, &'static str> {
    if let Some(plan)=&d.professional {return professional::render(d,plan);}
    let headings = heading_lines(d);
    let invoice_headers:InvoiceHeaders=Default::default();
    let display:Vec<(String,String)>=d.fields.iter().map(|(k,v)|(k.clone(),shown_value(k,v,&[0]))).collect();
    let requested_texts: Vec<&str> = headings.iter().map(|v| v.as_str()).chain(std::iter::once(d.title.as_str()))
        .chain(std::iter::once(d.watermark.as_str()))
        .chain(display.iter().flat_map(|(k, v)| [k.as_str(), v.as_str()]))
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
    doc.set_title(if d.branding.is_some() { "CONTROLLED DOCUMENT COPY" } else { "CLINIC SYSTEM PRO - CONTROLLED DOCUMENT COPY" });
    doc.set_minimal_conformance();
    doc.set_font_size(10);
    let mut decorator = SimplePageDecorator::new();
    if let Some(b)=&d.branding {
        decorator.set_margins(genpdf::Margins::trbl(if b.logo.is_some(){39}else{18},14,24,14));
    } else { decorator.set_margins(14); }
    doc.set_page_decorator(decorator);
    if let Some(b)=&d.branding {
        let primary=palette_rgb(&b.primary_color)?;let secondary=palette_rgb(&b.secondary_color)?;
        for (i,line) in headings.iter().enumerate() {
            push_styled(&mut doc,&check_font,line,if i==0{15}else if i==1{12}else{9},if i==0{primary}else{secondary})?;
        }
        doc.push(elements::Break::new(0.35));
        push_styled(&mut doc,&check_font,d.title.as_str(),20,primary)?;
        push_styled(&mut doc,&check_font,d.watermark.as_str(),9,(132,32,32))?;
    } else {
        for line in &headings { push_wrapped(&mut doc,&check_font,line)?; }
        push_wrapped(&mut doc,&check_font,d.title.as_str())?;
        push_wrapped(&mut doc,&check_font,d.watermark.as_str())?;
    }
    push_wrapped(&mut doc, &check_font, &format!(
        "Clinic reference: {}   Record reference: {}", d.clinic_id, d.record_id
    ))?;
    push_wrapped(&mut doc, &check_font, &format!("Source version: {}", d.source_version))?;
    for (label,value) in &d.fields {
        if let Some(b)=&d.branding {
            let secondary=palette_rgb(&b.secondary_color)?;
            push_styled(&mut doc,&check_font,&format!("{}:",label),9,secondary)?;
            if label=="Invoice Items" { push_invoice_table(&mut doc,&check_font,value,secondary,invoice_headers.clone())?; }
            else if label=="Outstanding Balance" {push_styled(&mut doc,&check_font,value,16,palette_rgb(&b.primary_color)?)?;}
            else {push_styled(&mut doc,&check_font,value,10,(24,37,47))?;}
        } else {
            push_wrapped(&mut doc,&check_font,&format!("{}:",label))?;
            push_wrapped(&mut doc,&check_font,value)?;
        }
    }
    // Preserve the complete digest; validate() requires exactly 64 ASCII hex bytes.
    push_wrapped(&mut doc, &check_font, "SOURCE SHA-256:")?;
    for piece in d.source_digest.as_bytes().chunks(32) {
        push_wrapped(&mut doc, &check_font, std::str::from_utf8(piece).map_err(|_| "invalid digest")?)?;
    }
    push_wrapped(&mut doc, &check_font, "This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.")?;
    let mut result = Vec::new();
    doc.render(&mut result).map_err(|_| "pdf render failed")?;
    let headers=invoice_headers.borrow();
    if let Some((_,value))=d.fields.iter().find(|(k,_)|k=="Invoice Items") {
        let count=invoice_cells(value)?.len();
        if count>0 && (headers.first()!=Some(&0) || headers.windows(2).any(|p|p[0]>=p[1]) || headers.last().map_or(true,|v|*v>=count)) {return Err("invalid invoice page inventory");}
    }
    if use_builtin {
        result = correct_builtin_encoding(&result, &expected_winansi(d,&headers)?)?;
    } else {
        result = correct_embedded_encoding(&result, d, &check_font,&headers)?;
    }
    if let Some(logo) = d.branding.as_ref().and_then(|b| b.logo.as_ref()) {
        result = add_brand_logo(&result, logo)?;
    }
    if let Some(b)=&d.branding { result=add_brand_decoration(&result,d,b)?; }
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
    if (d.protocol == 1 && bytes.len() > LEGACY_MAX_INPUT) || (d.protocol == 2 && bytes.len() > V2_MAX_INPUT) { return Err("input too large"); }
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
    fn invoice_row_and_heading_must_fit_together() {
        use genpdf::Mm;
        assert!(!row_fits(Mm::from(20.0),Mm::from(16.0),Mm::from(5.0)));
        assert!(row_fits(Mm::from(21.0),Mm::from(16.0),Mm::from(5.0)));
        assert!(!row_fits(Mm::from(15.0),Mm::from(16.0),Mm::from(0.0)));
    }
    #[test]
    fn invoice_encoding_expectation_includes_each_repeated_heading() {
        let rows=r#"[["First","1","1.00","1.00"],["Second","2","2.00","4.00"]]"#;
        let got=shown_invoice(rows,&[0,1]);
        assert_eq!(got.split_whitespace().collect::<Vec<_>>().join(" "),"Description Quantity Unit price Subtotal First 1 1.00 1.00 Description Quantity Unit price Subtotal Second 2 2.00 4.00");
    }
    #[test]
    fn rejects_nonclinical_kind_and_signed() {
        let mut d = PrintRequest {
            branding: None,
            professional: None,
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
    fn layout_wrap_preserves_words_and_long_unbroken_runs() {
        let text = format!("Controlled billing copy {} and final disclaimer.", "W".repeat(300));
        let lines = wrap_lines(&text, |c| if c == 'W' { 18.0 } else { 9.0 }).unwrap();
        assert!(lines.len() > 5);
        assert_eq!(lines.concat(), text);
        assert!(lines.iter().all(|s| s.chars().map(|c| if c == 'W' { 18.0 } else { 9.0 }).sum::<f32>() <= PRINT_LINE_BUDGET));
        assert!(lines[0].ends_with(' '));
    }
    #[test]
    fn layout_wrap_preserves_unicode_and_explicit_lines() {
        let text = "é".repeat(128);
        let lines = wrap_lines(&text, |_| 12.0).unwrap();
        assert_eq!(lines.concat(), text);
        assert!(lines.iter().all(|s| s.chars().count() <= 33));
        assert_eq!(wrap_lines("A\tB\n\nC", |_| 10.0).unwrap(), vec!["A    B", " ", "C"]);
        assert!(wrap_lines("W", |_| 401.0).is_err());
        assert!(wrap_lines("W", |_| f32::NAN).is_err());
    }
    #[test]
    fn simple_font_encoding_preserves_ascii_and_winansi() {
        assert_eq!(simple_font_code(&[0, 0x43, 0, 0xe9, 0, 0x80, 0x20, 0x13]).unwrap(), vec![0x43, 0xe9, 0x80, 0x96]);
        assert_eq!(winansi_byte('€'), Some(0x80));
        assert_eq!(winansi_byte('µ'), Some(0xb5));
        assert_eq!(winansi_byte('μ'), None);
    }
    #[test]
    fn simple_font_encoding_rejects_ambiguous_or_invalid_codes() {
        for invalid in [&[][..], &[0x43][..], &[0, 0][..], &[0, 0x81][..], &[0x03, 0xbc][..]] {
            assert!(simple_font_code(invalid).is_err());
        }
    }
    #[test]
    fn unicode_codes_distinguish_shared_glyphs_and_utf16() {
        let mut c = UnicodeCodes::default();
        assert_eq!(c.assign('-', 16).unwrap(), 1);
        assert_eq!(c.assign('\u{2010}', 16).unwrap(), 2);
        assert_eq!(c.assign('-', 16).unwrap(), 1);
        assert_eq!(c.assign('\u{2212}', 120).unwrap(), 3);
        assert_eq!(c.assign('\u{1f600}', 500).unwrap(), 4);
        assert!(c.assign('-', 17).is_err());
        assert!(c.assign('x', 0).is_err());
        let map = String::from_utf8(c.cmap()).unwrap();
        assert!(map.contains("<0001> <002D>\n<0002> <2010>\n<0003> <2212>\n<0004> <D83DDE00>"));
    }
    #[test]
    fn unicode_source_alignment_rejects_wrong_or_extra_glyph() {
        let glyph = |c| match c { '-' | '\u{2010}' => 16, ' ' => 3, 'A' => 36, _ => 0 };
        let mut expected = "-\u{2010}A".chars();
        let mut codes = UnicodeCodes::default();
        assert_eq!(consume_unicode_gid(16, 3, &mut expected, &glyph, &mut codes).unwrap(), 1);
        assert_eq!(consume_unicode_gid(3, 3, &mut expected, &glyph, &mut codes).unwrap(), 2);
        assert_eq!(consume_unicode_gid(16, 3, &mut expected, &glyph, &mut codes).unwrap(), 3);
        assert_eq!(consume_unicode_gid(36, 3, &mut expected, &glyph, &mut codes).unwrap(), 4);
        assert!(consume_unicode_gid(36, 3, &mut expected, &glyph, &mut codes).is_err());
        assert!(consume_unicode_gid(16, 3, &mut "A".chars(), &glyph, &mut codes).is_err());
    }
    #[test]
    fn unicode_width_mapping_keeps_original_metrics() {
        use lopdf::Object;
        let widths = embedded_widths(&[Object::Integer(16), Object::Array(vec![Object::Integer(389)]), Object::Integer(20), Object::Integer(22), Object::Integer(500)]).unwrap();
        assert_eq!(widths[&16].as_i64().unwrap(), 389);
        assert_eq!(widths[&22].as_i64().unwrap(), 500);
        assert!(embedded_widths(&[Object::Integer(16)]).is_err());
    }
    #[test]
    fn bounded_string_checks() {
        assert!(check_str("A\u{202e}B", 300).is_err());
        assert!(check_str("A\0B", 300).is_err());
        assert!(check_str(&"A".repeat(301), 300).is_err());
    }
}
