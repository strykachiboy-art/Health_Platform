// Synthetic same-host test only: no service listener, no real patient or device data.
use std::{io::ErrorKind, net::{SocketAddr, UdpSocket}, sync::Arc, thread, time::{Duration, Instant}};
use str0m::{Candidate, Event, Input, Output, Rtc};
use str0m::format::Codec;
use str0m::media::{Direction, MediaKind, MediaTime, Mid};
use str0m::net::{Protocol, Receive};
const AUDIO: &[u8] = &[0xf8, 0xff, 0xfe]; // Opus silence, not recorded speech.
const VIDEO: &[u8] = &[0xd0,0x2,0x0,0x9d,0x1,0x2a,0x20,0x0,0x20,0x0,0x0,0x47,0x8,0x85,0x85,0x88,0x85,0x84,0x88,0x2,0x2,0x2,0x75,0xaa,0x3,0xf8,0x2,0x8,0x21,0x29,0x9e,0x70,0xfe,0xfb,0x68,0x97,0xff,0x3d,0x33,0x5e,0x60,0xff,0x27,0x3f,0xfd,0xb9,0x1f,0xed,0xc8,0xff,0x6e,0x47,0xff,0xb6,0xc2,0x0]; // single synthetic VP8 keyframe from FFmpeg/libvpx
struct Peer {
    rtc: Rtc, sock: UdpSocket, addr: SocketAddr, other: SocketAddr,
    deadline: Instant, connected: bool, audio: usize, video: usize, tx: usize, rx: usize,
}
impl Peer {
    fn new(sock: UdpSocket, other: SocketAddr) -> Self {
        let addr = sock.local_addr().unwrap();
        assert!(addr.ip().is_loopback() && other.ip().is_loopback());
        sock.set_nonblocking(true).unwrap();
        let now = Instant::now();
        let rtc = Rtc::builder()
            .set_crypto_provider(Arc::new(str0m::crypto::from_feature_flags())).build(now);
        let mut p = Self { rtc, sock, addr, other, deadline: now, connected: false, audio: 0, video: 0, tx: 0, rx: 0 };
        p.rtc.add_local_candidate(Candidate::host(addr, "udp").unwrap());
        p.drain();
        p
    }
    fn drain(&mut self) {
        // Bound work per mutation. Exhausting the budget is a failure, not a pass.
        for _ in 0..4096 {
            match self.rtc.poll_output().unwrap() {
                Output::Timeout(t) => { self.deadline = t; return; }
                Output::Transmit(t) => {
                    assert_eq!(t.destination, self.other, "non-loopback or unexpected peer destination");
                    self.sock.send_to(&t.contents, t.destination).unwrap();
                    self.tx += 1;
                }
                Output::Event(Event::Connected) => self.connected = true,
                Output::Event(Event::MediaData(d)) => {
                    match d.params.spec().codec {
                        Codec::Opus => { assert_eq!(d.data.as_ref(), AUDIO); self.audio += 1; }
                        Codec::Vp8 => { assert_eq!(d.data.as_ref(), VIDEO); self.video += 1; }
                        _ => panic!("Unexpected negotiated codec for test media"),
                    }
                }
                Output::Event(_) => {}
            }
        }
        panic!("drain budget exceeded");
    }
    fn step(&mut self) {
        let mut buf = [0u8; 65536];
        for _ in 0..64 {
            match self.sock.recv_from(&mut buf) {
                Ok((n, source)) => {
                    assert_eq!(source, self.other);
                    self.rx += 1;
                    self.rtc.handle_input(Input::Receive(Instant::now(), Receive {
                        proto: Protocol::Udp, source, destination: self.addr,
                        contents: buf[..n].try_into().unwrap(),
                    })).unwrap();
                    self.drain();
                }
                Err(e) if e.kind() == ErrorKind::WouldBlock => break,
                Err(e) => panic!("UDP receive failed: {e}"),
            }
        }
        let now = Instant::now();
        if now >= self.deadline {
            self.rtc.handle_input(Input::Timeout(now)).unwrap();
            self.drain();
        }
    }
    fn send(&mut self, mid: Mid, codec: Codec, ms: u64, bytes: &'static [u8]) {
        assert!(self.connected && self.rtc.is_connected());
        let writer = self.rtc.writer(mid).expect("negotiated media writer");
        let pt = writer.payload_params().find(|p| p.spec().codec == codec).expect("codec negotiated").pt();
        writer.write(pt, Instant::now(), MediaTime::from_millis(ms), bytes).unwrap();
        self.drain();
    }
}
fn drive(a: &mut Peer,b: &mut Peer, dur: Duration) {
    let end = Instant::now() + dur;
    while Instant::now() < end { a.step(); b.step(); thread::sleep(Duration::from_millis(1)); }
}
fn main() {
    let sa = UdpSocket::bind("127.0.0.1:0").unwrap();
    let sb = UdpSocket::bind("127.0.0.1:0").unwrap();
    let aa = sa.local_addr().unwrap(); let ab = sb.local_addr().unwrap();
    let mut a = Peer::new(sa, ab); let mut b = Peer::new(sb, aa);
    let start = Instant::now();
    let mut change = a.rtc.sdp_api();
    let am = change.add_media(MediaKind::Audio, Direction::SendRecv, None, None, None);
    let vm = change.add_media(MediaKind::Video, Direction::SendRecv, None, None, None);
    let (offer, pending) = change.apply().unwrap();
    a.drain();
    let answer = b.rtc.sdp_api().accept_offer(offer).unwrap();
    b.drain();
    a.rtc.sdp_api().accept_answer(pending, answer).unwrap();
    a.drain();
    while !(a.connected && b.connected && a.rtc.is_connected() && b.rtc.is_connected()) {
        assert!(start.elapsed() < Duration::from_secs(20), "ICE/DTLS timeout");
        drive(&mut a, &mut b, Duration::from_millis(5));
    }
    println!("CHAT_R_R02_ICE_DTLS_BOTH=PASS;MS={}", start.elapsed().as_millis());
    // 40 audio frames and 8 VP8 frames in each direction. No capture or codec implementation.
    for i in 0..40u64 {
        a.send(am, Codec::Opus, i*20, AUDIO);
        b.send(am, Codec::Opus, i*20, AUDIO);
        if i%5 == 0 {
            a.send(vm, Codec::Vp8, i*20, VIDEO);
            b.send(vm, Codec::Vp8, i*20, VIDEO);
        }
        drive(&mut a, &mut b, Duration::from_millis(20));
    }
    drive(&mut a,&mut b,Duration::from_secs(2));
    println!("CHAT_R_R02_PAYLOAD_COUNTS=A_AUDIO:{}:A_VIDEO:{}:B_AUDIO:{}:B_VIDEO:{}",a.audio,a.video,b.audio,b.video);
    println!("CHAT_R_R02_UDP_DATAGRAMS=A_TX:{}:A_RX:{}:B_TX:{}:B_RX:{}",a.tx,a.rx,b.tx,b.rx);
    assert!(a.audio>=30 && b.audio>=30 && a.video>=5 && b.video>=5, "insufficient exact payload receipts");
    assert!(a.tx>0 && a.rx>0 && b.tx>0 && b.rx>0);
    assert!(a.rtc.is_connected() && b.rtc.is_connected());
    println!("CHAT_R_R02_BIDIRECTIONAL_OPUS_VP8_LOOPBACK=PASS");
}
