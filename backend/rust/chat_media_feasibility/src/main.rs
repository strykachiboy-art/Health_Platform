fn main() {
    let rtc = str0m::Rtc::builder()
        .set_crypto_provider(std::sync::Arc::new(str0m::crypto::from_feature_flags()))
        .build(std::time::Instant::now());
    assert!(rtc.is_alive());
    assert!(!rtc.is_connected());
    println!("CHAT_R_ENGINE_INITIALIZED_NOT_CONNECTED=PASS");
}
