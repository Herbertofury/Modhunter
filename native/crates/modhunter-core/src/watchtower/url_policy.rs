//! Public page identity, separate from provider download authorization.
use std::net::{IpAddr, Ipv4Addr};
use url::{Host, Url};

pub fn is_public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => public_v4(ip),
        IpAddr::V6(ip) => {
            if let Some(v4) = ip.to_ipv4_mapped() {
                return public_v4(v4);
            }
            let words = ip.segments();
            !ip.is_loopback()
                && !ip.is_unspecified()
                && !ip.is_multicast()
                && (words[0] & 0xfe00) != 0xfc00
                && (words[0] & 0xffc0) != 0xfe80
                && (words[0] & 0xffc0) != 0xfec0
                && !(words[0] == 0x2001 && words[1] == 0xdb8)
                // Protocol/benchmark ranges, deprecated 6to4, and documentation
                // space are not ordinary public website destinations.
                && !(words[0] == 0x2001 && words[1] < 0x0200)
                && words[0] != 0x2002
                && !(words[0] == 0x3fff && words[1] < 0x1000)
                && words[0] >= 0x2000
                && words[0] <= 0x3fff
        }
    }
}

fn public_v4(ip: Ipv4Addr) -> bool {
    let [a, b, c, _] = ip.octets();
    !ip.is_private()
        && !ip.is_loopback()
        && !ip.is_link_local()
        && !ip.is_documentation()
        && !ip.is_broadcast()
        && !ip.is_unspecified()
        && a != 0
        && a < 224
        && !(a == 100 && (64..=127).contains(&b))
        && !(a == 192 && b == 0 && c == 0)
        && !(a == 192 && b == 88 && c == 99)
        && !(a == 198 && (b == 18 || b == 19))
}

pub fn canonical_page_url(raw: &str) -> Result<String, String> {
    let raw = raw.trim();
    if raw.is_empty() || raw.len() > 4096 || raw.chars().any(char::is_control) {
        return Err("Enter a complete public HTTPS page URL of at most 4,096 bytes.".into());
    }
    let mut url = Url::parse(raw).map_err(|_| "The page URL is invalid.".to_owned())?;
    if url.scheme() != "https" || !url.username().is_empty() || url.password().is_some() {
        return Err(
            "Page watches require HTTPS URLs without embedded usernames or passwords.".into(),
        );
    }
    if url.port() == Some(0) {
        return Err("The page URL has an invalid network port.".into());
    }
    match url.host() {
        Some(Host::Ipv4(ip)) if is_public_ip(IpAddr::V4(ip)) => {}
        Some(Host::Ipv6(ip)) if is_public_ip(IpAddr::V6(ip)) => {}
        Some(Host::Domain(host)) => {
            let host = host.trim_end_matches('.').to_ascii_lowercase();
            if !host.contains('.')
                || host.split('.').any(str::is_empty)
                || [
                    "localhost",
                    "local",
                    "internal",
                    "home",
                    "lan",
                    "test",
                    "invalid",
                    "example",
                    "onion",
                ]
                .iter()
                .any(|suffix| host == *suffix || host.ends_with(&format!(".{suffix}")))
                || matches!(
                    host.as_str(),
                    "tauri.localhost" | "ipc.localhost" | "metadata.google.internal"
                )
                || host == "home.arpa"
                || host.ends_with(".home.arpa")
            {
                return Err("Local, reserved, and application addresses cannot be tracked.".into());
            }
            url.set_host(Some(&host))
                .map_err(|_| "The page host is invalid.".to_owned())?;
        }
        _ => {
            return Err("Local, reserved, and private network addresses cannot be tracked.".into());
        }
    }
    // Anchor fragments do not change the network page. Path/query casing remains
    // untouched: arbitrary websites may distinguish /PackA from /packa.
    url.set_fragment(None);
    Ok(url.to_string())
}

pub fn page_origin(raw: &str) -> Result<String, String> {
    let url = Url::parse(&canonical_page_url(raw)?).map_err(|_| "Invalid page URL".to_owned())?;
    Ok(url.origin().ascii_serialization())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_identity_preserves_case_sensitive_paths_and_query() {
        assert_eq!(
            canonical_page_url("https://EXAMPLE.com:443/PackA?Release=V2#files").unwrap(),
            "https://example.com/PackA?Release=V2"
        );
        assert_ne!(
            canonical_page_url("https://example.com/PackA").unwrap(),
            canonical_page_url("https://example.com/packa").unwrap()
        );
        assert_eq!(
            page_origin("https://example.com:8443/page?q=1").unwrap(),
            "https://example.com:8443"
        );
    }
    #[test]
    fn local_and_encoded_addresses_never_become_watch_targets() {
        for raw in [
            "https://127.1/",
            "https://0x7f000001/",
            "https://2130706433/",
            "https://10.0.0.1/",
            "https://100.64.0.1/",
            "https://169.254.169.254/",
            "https://192.168.1.1/",
            "https://[::1]/",
            "https://[::ffff:127.0.0.1]/",
            "https://[fc00::1]/",
            "https://[fe80::1]/",
            "https://[2002:7f00:1::]/",
            "https://[2001:2::1]/",
            "https://[3fff::1]/",
            "https://localhost/",
            "https://machine.local/",
            "https://machine.home.arpa/",
            "https://tauri.localhost/",
            "https://example.invalid/",
            "file:///C:/private",
            "https://user:password@example.com/",
        ] {
            assert!(canonical_page_url(raw).is_err(), "accepted {raw}");
        }
    }
    #[test]
    fn public_ipv4_and_ipv6_are_validated_without_a_network_request() {
        assert!(canonical_page_url("http://example.com/").is_err());
        assert!(canonical_page_url("https://8.8.8.8/").is_ok());
        assert!(canonical_page_url("https://[2606:4700:4700::1111]/").is_ok());
        assert!(canonical_page_url("https://192.0.2.4/").is_err());
        assert!(canonical_page_url("https://[2001:db8::4]/").is_err());
    }
}
