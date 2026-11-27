//! Native painter for the bounded server-generated professional presentation plan.
//! The legacy protocol and L02 correction routines are unchanged. This path
//! allocates distinct source-Unicode CIDs from the outset for both font faces.
use super::{PrintRequest, UnicodeCodes, check_str, palette_rgb, invoice_cells, MAX_PDF};
use lopdf::{Dictionary, Document, Object, ObjectId, Stream};
use serde::Deserialize;
use std::fmt::Write as _;
use std::path::PathBuf;

const W:f64=595.2756;
const H:f64=841.8898;
const BAD:&str="invalid professional presentation";
const DISCLAIMER:&str="This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.";
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Plan {family:String,pages:Vec<Page>}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Page {text:Vec<Text>,rects:Vec<Rect>,logo:Option<[f64;4]>}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Text {text:String,x:f64,y:f64,size:u8,bold:bool,color:String,tag:String}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Rect {x:f64,y:f64,w:f64,h:f64,color:String}
fn norm(s:&str)->String {s.chars().filter(|c|!c.is_whitespace()).collect()}
fn name(s:&str)->Object {Object::Name(s.as_bytes().to_vec())}
fn array(v:Vec<Object>)->Object {Object::Array(v)}
fn dict(entries:&[(&str,Object)])->Dictionary {let mut d=Dictionary::new();for (k,v) in entries {d.set(*k,v.clone());}d}
fn real(v:f64)->Object {Object::Real(v)}
fn num(v:i64)->Object {Object::Integer(v)}
fn reference(v:ObjectId)->Object {Object::Reference(v)}
fn rgb(s:&str)->Result<[f64;3],&'static str> {
    if s.len()!=7 || !s.starts_with('#') || !s.as_bytes()[1..].iter().all(|c|matches!(*c,b'0'..=b'9'|b'A'..=b'F')) {return Err(BAD)}
    let mut result=[0.;3];for (i,n) in result.iter_mut().enumerate(){*n=u8::from_str_radix(&s[1+i*2..3+i*2],16).map_err(|_|BAD)? as f64/255.;}Ok(result)
}
fn color_ok(s:&str,p:&str,q:&str)->bool {s==p||s==q||matches!(s,"#142A3A"|"#536575"|"#D6DFE6"|"#F1F5F7"|"#842020"|"#FCF2F1"|"#FFFFFF")}
fn box_ok(x:f64,y:f64,w:f64,h:f64)->bool {x.is_finite()&&y.is_finite()&&w.is_finite()&&h.is_finite()&&x>=44.8&&y>=21.8&&w>0.&&h>0.&&x+w<=W-44.8&&y+h<=H-17.8}
fn fields_expected(d:&PrintRequest)->Result<String,&'static str>{
    let mut body=String::new();
    for(k,v) in &d.fields {if !matches!(k.as_str(),"Invoice Items"|"Outstanding Balance"|"Report Text"){body.push_str(k);body.push_str(v);}}
    let _=write!(body,"Clinic reference{}Record reference{}Source version{}",d.clinic_id,d.record_id,d.source_version);
    if let Some((_,items))=d.fields.iter().find(|(k,_)|k=="Invoice Items") {
        body.push_str("Invoice Items");let rows=invoice_cells(items)?;
        if rows.is_empty(){body.push_str("No itemized charges recorded");}
        for row in rows {for cell in row {body.push_str(&cell);}}
        let amount=d.fields.iter().find(|(k,_)|k=="Outstanding Balance").ok_or(BAD)?;
        let currency=d.fields.iter().find(|(k,_)|k=="Currency").ok_or(BAD)?;
        body.push_str("Outstanding Balance");body.push_str(&currency.1);body.push_str(&amount.1);
    }
    if let Some((_,report))=d.fields.iter().find(|(k,_)|k=="Report Text") {body.push_str("Report Text");body.push_str(report);}
    Ok(norm(&body))
}
fn validate_plan(d:&PrintRequest,plan:&Plan)->Result<(),&'static str>{
    let b=d.branding.as_ref().ok_or(BAD)?;palette_rgb(&b.primary_color)?;palette_rgb(&b.secondary_color)?;
    if b.lines.len()<2||plan.pages.is_empty()||plan.pages.len()>100{return Err(BAD)}
    let org=b.lines[0].strip_prefix("Organisation: ").ok_or(BAD)?;
    let clinic=b.lines[1].strip_prefix("Clinic: ").ok_or(BAD)?;
    let header=norm(&format!("{}{}{}{}{}CONTROLLED DOCUMENT COPY",org,clinic,b.lines[2..].join(" | "),d.title,d.watermark));
    let mut body=String::new();let mut n=0;
    let has_items=match d.fields.iter().find(|(k,_)|k=="Invoice Items") {Some((_,v))=>!invoice_cells(v)?.is_empty(),None=>false};
    let mut table_seen=false;
    for (i,page) in plan.pages.iter().enumerate(){
        if page.text.is_empty()||page.text.len()>1500||page.rects.len()>1000{return Err(BAD)}
        n+=page.text.len();if n>10000{return Err(BAD)}
        match (&b.logo,&page.logo){
            (None,None)=>(),(Some(logo),Some([x,y,w,h]))=>{
                let scale=(85./f64::from(logo.width)).min(48./f64::from(logo.height));
                if !box_ok(*x,*y,*w,*h)||(*x-45.).abs()>0.001||(*y-58.).abs()>0.001||(*w-f64::from(logo.width)*scale).abs()>0.001||(*h-f64::from(logo.height)*scale).abs()>0.001 {return Err(BAD)}
            },_=>return Err(BAD),
        }
        let mut head=String::new();let mut foot=String::new();let mut tab=String::new();let mut warning=String::new();let mut disclaimer=String::new();
        let mut phase=0;
        for t in &page.text {
            check_str(&t.text,12000)?;
            if t.text.chars().any(|c|c.is_control())||!(7..=25).contains(&t.size)||!t.x.is_finite()||!t.y.is_finite()||t.x<44.8||t.x>W-44.8||t.y<22.||t.y>H-18.||!color_ok(&t.color,&b.primary_color,&b.secondary_color){return Err(BAD)}
            match t.tag.as_str(){
                "header" if phase==0&&t.color!="#FFFFFF"&&t.y<H-137.=>head.push_str(&t.text),
                "warning" if phase==0&&t.color=="#842020"&&t.y<H-137.=>{head.push_str(&t.text);warning.push_str(&t.text);},
                "body" if phase<=1&&t.color!="#FFFFFF"&&t.y<H-137.=>{phase=1;body.push_str(&t.text);},
                "table_header" if phase<=1&&has_items&&t.color=="#FFFFFF"&&t.y<H-137.=>{phase=1;tab.push_str(&t.text);},
                "footer" if t.color!="#FFFFFF"&&t.y>=H-132.=>{phase=2;foot.push_str(&t.text);},
                "disclaimer" if t.color=="#842020"&&t.y>=H-132.=>{phase=2;foot.push_str(&t.text);disclaimer.push_str(&t.text);},_=>return Err(BAD),
            }
        }
        if !tab.is_empty(){if norm(&tab)!="DescriptionQuantityUnitpriceSubtotal" {return Err(BAD)}table_seen=true;}
        let expected_footer=norm(&format!("SOURCE SHA-256: {} BRANDING SHA-256: {} {} Page {} of {}",d.source_digest,b.branding_sha256,DISCLAIMER,i+1,plan.pages.len()));
        if norm(&head)!=header||norm(&foot)!=expected_footer||norm(&warning)!=norm(&d.watermark)||norm(&disclaimer)!=norm(DISCLAIMER){return Err(BAD)}
        for r in &page.rects {if !box_ok(r.x,r.y,r.w,r.h)||!color_ok(&r.color,&b.primary_color,&b.secondary_color){return Err(BAD)}}
    }
    if table_seen!=has_items||norm(&body)!=fields_expected(d)?{return Err(BAD)}
    Ok(())
}
struct Face {raw:Vec<u8>,font:rusttype::Font<'static>,codes:UnicodeCodes,ps:&'static str,bold:bool}
impl Face {
    fn load(path:PathBuf,ps:&'static str,bold:bool)->Result<Self,&'static str>{
        let raw=std::fs::read(path).map_err(|_|BAD)?;if raw.len()>4*1024*1024{return Err(BAD)}
        let font=rusttype::Font::try_from_vec(raw.clone()).ok_or(BAD)?;
        Ok(Self{raw,font,codes:UnicodeCodes::default(),ps,bold})
    }
    fn advance(&self,ch:char)->Result<f64,&'static str>{
        let g=self.font.glyph(ch);if g.id().0==0{return Err(BAD)}
        let m=self.font.v_metrics_unscaled();let s=(m.ascent-m.descent)*1000./f32::from(self.font.units_per_em());
        let width=g.scaled(rusttype::Scale::uniform(s)).h_metrics().advance_width as f64;
        if !width.is_finite()||width<0.||width>4000.{return Err(BAD)}Ok(width)
    }
    fn encode(&mut self,t:&Text)->Result<String,&'static str>{
        let mut result=String::new();let mut width=0.;
        for c in t.text.chars(){let gid=self.font.glyph(c).id().0;let cid=self.codes.assign(c,gid)?;let _=write!(result,"{:04X}",cid);width+=self.advance(c)?*f64::from(t.size)/1000.;}
        if t.x+width>W-44.7{return Err(BAD)}Ok(result)
    }
    fn embed(self,pdf:&mut Document)->Result<ObjectId,&'static str>{
        let m=self.font.v_metrics_unscaled();let em=f64::from(self.font.units_per_em());
        let ascent=f64::from(m.ascent)*1000./em;let descent=f64::from(m.descent)*1000./em;
        // Actual TrueType metrics and source-CID mapping. No synthesized bold.
        let bbox=font_bbox(&self.raw,em)?;
        let mut widths=Vec::new();let mut gidmap=vec![0,0];
        for &(ch,gid) in &self.codes.entries {widths.push(real(self.advance(ch)?));gidmap.extend_from_slice(&gid.to_be_bytes());}
        let cmap=pdf.add_object(Stream::new(Dictionary::new(),self.codes.cmap()));
        let gid=pdf.add_object(Stream::new(Dictionary::new(),gidmap));
        let file=pdf.add_object(Stream::new(dict(&[("Length1",num(self.raw.len() as i64))]),self.raw));
        let fd=pdf.add_object(Object::Dictionary(dict(&[("Type",name("FontDescriptor")),("FontName",name(self.ps)),("Flags",num(32)),("FontBBox",array(bbox.into_iter().map(real).collect())),("ItalicAngle",num(0)),("Ascent",real(ascent)),("Descent",real(descent)),("CapHeight",real(ascent)),("StemV",num(if self.bold{120}else{80})),("FontFile2",reference(file))])));
        let sys=dict(&[("Registry",Object::string_literal("Adobe")),("Ordering",Object::string_literal("Identity")),("Supplement",num(0))]);
        let cid=pdf.add_object(Object::Dictionary(dict(&[("Type",name("Font")),("Subtype",name("CIDFontType2")),("BaseFont",name(self.ps)),("CIDSystemInfo",Object::Dictionary(sys)),("FontDescriptor",reference(fd)),("DW",num(1000)),("W",array(vec![num(1),array(widths)])),("CIDToGIDMap",reference(gid))])));
        Ok(pdf.add_object(Object::Dictionary(dict(&[("Type",name("Font")),("Subtype",name("Type0")),("BaseFont",name(self.ps)),("Encoding",name("Identity-H")),("DescendantFonts",array(vec![reference(cid)])),("ToUnicode",reference(cmap))]))))
    }
}
fn font_bbox(raw:&[u8],em:f64)->Result<[f64;4],&'static str>{
    let u16at=|i:usize|->Result<u16,&'static str>{let b=raw.get(i..i+2).ok_or(BAD)?;Ok(u16::from_be_bytes([b[0],b[1]]))};
    let u32at=|i:usize|->Result<u32,&'static str>{let b=raw.get(i..i+4).ok_or(BAD)?;Ok(u32::from_be_bytes([b[0],b[1],b[2],b[3]]))};
    if !raw.starts_with(&[0,1,0,0])||em<=0.{return Err(BAD)}
    let mut head=None;let mut fs=None;
    for i in 0..usize::from(u16at(4)?){let p=12+16*i;let tag=raw.get(p..p+4).ok_or(BAD)?;let off=u32at(p+8)? as usize;if tag==b"head"{head=Some(off);}if tag==b"OS/2"{fs=Some(u16at(off+8)?);}}
    // Do not embed restricted-license or bitmap-only font programs.
    if fs.ok_or(BAD)?&0x0202!=0{return Err(BAD)}
    let p=head.ok_or(BAD)?+36;
    let mut result=[0.;4];for (i,v) in result.iter_mut().enumerate(){*v=f64::from(u16at(p+2*i)? as i16)*1000./em;}Ok(result)
}
fn faces(family:&str)->Result<[Face;2],&'static str>{
    let (root,files,names)=if cfg!(windows){
        let root=PathBuf::from(std::env::var_os("WINDIR").unwrap_or_else(||"C:\\Windows".into())).join("Fonts");
        match family {"DejaVu"=>(root,["DejaVuSans.ttf","DejaVuSans-Bold.ttf"],["DejaVuSans","DejaVuSans-Bold"]),"Arial"=>(root,["arial.ttf","arialbd.ttf"],["ArialMT","Arial-BoldMT"]),"Segoe"=>(root,["segoeui.ttf","segoeuib.ttf"],["SegoeUI","SegoeUI-Bold"]),_=>return Err(BAD)}
    }else{
        if family!="DejaVu"{return Err(BAD)}
        (PathBuf::from("/usr/share/fonts/truetype/dejavu"),["DejaVuSans.ttf","DejaVuSans-Bold.ttf"],["DejaVuSans","DejaVuSans-Bold"])
    };
    Ok([Face::load(root.join(files[0]),names[0],false)?,Face::load(root.join(files[1]),names[1],true)?])
}
pub(super) fn render(d:&PrintRequest,plan:&Plan)->Result<Vec<u8>,&'static str>{
    validate_plan(d,plan)?;let mut faces=faces(&plan.family)?;
    let b=d.branding.as_ref().ok_or(BAD)?;let mut pdf=Document::with_version("1.4");
    let image_id=if let Some(logo)=&b.logo {
        let raw=(0..logo.rgb_hex.len()).step_by(2).map(|i|u8::from_str_radix(&logo.rgb_hex[i..i+2],16).map_err(|_|BAD)).collect::<Result<Vec<_>,_>>()?;
        Some(pdf.add_object(Stream::new(dict(&[("Type",name("XObject")),("Subtype",name("Image")),("Width",num(i64::from(logo.width))),("Height",num(i64::from(logo.height))),("ColorSpace",name("DeviceRGB")),("BitsPerComponent",num(8))]),raw)))
    }else{None};
    let pages_id=pdf.new_object_id();let mut kids=Vec::new();let mut streams=Vec::new();
    for p in &plan.pages{
        let mut content=String::new();
        for r in &p.rects{let [red,g,bl]=rgb(&r.color)?;let _=writeln!(content,"q {:.6} {:.6} {:.6} rg {:.4} {:.4} {:.4} {:.4} re f Q",red,g,bl,r.x,H-r.y-r.h,r.w,r.h);}
        if let Some([x,y,w,h])=p.logo{let _=writeln!(content,"q {:.4} 0 0 {:.4} {:.4} {:.4} cm /Logo Do Q",w,h,x,H-y-h);}
        for t in &p.text{let [red,g,bl]=rgb(&t.color)?;let i=if t.bold{1}else{0};let hex=faces[i].encode(t)?;let _=writeln!(content,"q {:.6} {:.6} {:.6} rg BT /F{} {} Tf 1 0 0 1 {:.4} {:.4} Tm <{}> Tj ET Q",red,g,bl,i,t.size,t.x,H-t.y,hex);}
        streams.push(pdf.add_object(Stream::new(Dictionary::new(),content.into_bytes())));
    }
    let [regular,bold]=faces;let regular=regular.embed(&mut pdf)?;let bold=bold.embed(&mut pdf)?;
    let mut resources=dict(&[("Font",Object::Dictionary(dict(&[("F0",reference(regular)),("F1",reference(bold))]))) ]);
    if let Some(id)=image_id{resources.set("XObject",Object::Dictionary(dict(&[("Logo",reference(id))])));}
    let rid=pdf.add_object(Object::Dictionary(resources));
    for sid in streams {let id=pdf.add_object(Object::Dictionary(dict(&[("Type",name("Page")),("Parent",reference(pages_id)),("MediaBox",array(vec![num(0),num(0),real(W),real(H)])),("Resources",reference(rid)),("Contents",reference(sid))])));kids.push(reference(id));}
    pdf.objects.insert(pages_id,Object::Dictionary(dict(&[("Type",name("Pages")),("Count",num(kids.len() as i64)),("Kids",array(kids))])));
    let root=pdf.add_object(Object::Dictionary(dict(&[("Type",name("Catalog")),("Pages",reference(pages_id))])));pdf.trailer.set("Root",reference(root));
    pdf.compress();let mut result=Vec::new();pdf.save_to(&mut result).map_err(|_|BAD)?;
    if result.len()>MAX_PDF||!result.starts_with(b"%PDF-"){return Err(BAD)}Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn professional_geometry_has_bounded_a4_frame() {
        assert!(box_ok(45.,45.,505.,4.));
        assert!(!box_ok(0.,45.,505.,4.));
        assert!(!box_ok(45.,45.,600.,4.));
        assert!(!box_ok(45.,f64::NAN,10.,10.));
    }
    #[test]
    fn professional_graphics_palette_is_literal_and_bounded() {
        assert!(rgb("#176B65").is_ok());
        assert!(rgb("#ffffff").is_err());
        assert!(rgb("url(x)").is_err());
        assert!(color_ok("#842020","#176B65","#283548"));
        assert!(!color_ok("#ABABAB","#176B65","#283548"));
    }
}
