//! Anonymous structured observations. No entry link, attachment or external entity is fetched.
use super::{MAX_CAPTURE_BYTES, PageObservation, canonical_page_url, is_public_ip};
use reqwest::{
    blocking::Client,
    dns::{Addrs, Name, Resolve, Resolving},
    header,
};
use roxmltree::{Document, Node};
use std::{
    collections::VecDeque,
    io::Read,
    sync::{Arc, Mutex},
    time::Duration,
};
use url::Url;

const ATOM: &str = "http://www.w3.org/2005/Atom";
const DOCUMENT_BYTES: usize = 2 * 1024 * 1024;

struct PublicDns;
impl Resolve for PublicDns {
    fn resolve(&self, name: Name) -> Resolving {
        Box::pin(async move {
            let addresses: Vec<_> = tokio::net::lookup_host((name.as_str(), 0)).await?.collect();
            if addresses.is_empty() || addresses.iter().any(|address| !is_public_ip(address.ip())) {
                return Err("Source does not resolve exclusively to public addresses".into());
            }
            Ok(Box::new(addresses.into_iter()) as Addrs)
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PublicFormat {
    Feed,
    Json,
}

impl PublicFormat {
    pub fn from_selector(selector: &str) -> Option<Self> {
        match selector {
            "feed:" => Some(Self::Feed),
            "json:" => Some(Self::Json),
            _ => None,
        }
    }
}

#[derive(Clone)]
struct CachedDocument {
    format: PublicFormat,
    url: String,
    etag: Option<header::HeaderValue>,
    modified: Option<header::HeaderValue>,
    title: String,
    text: String,
}

pub struct PublicDocumentReader {
    client: Client,
    cache: Mutex<VecDeque<Arc<CachedDocument>>>,
}

impl PublicDocumentReader {
    pub fn new() -> Result<Self, String> {
        let client = Client::builder()
            .user_agent(concat!("Modhunter/", env!("CARGO_PKG_VERSION")))
            .https_only(true)
            .no_proxy()
            .dns_resolver(Arc::new(PublicDns))
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(20))
            .build()
            .map_err(|_| "Unable to initialize public document transport")?;
        Ok(Self {
            client,
            cache: Mutex::new(VecDeque::new()),
        })
    }

    pub fn observe(
        &self,
        url: &Url,
        format: PublicFormat,
        filtered_resources: bool,
    ) -> Result<PageObservation, String> {
        let url = canonical_page_url(url.as_str())?;
        let previous = self
            .cache
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .find(|item| item.url == url && item.format == format)
            .cloned();
        let mut request = self
            .client
            .get(&url)
            .header(
                header::ACCEPT,
                match format {
                    PublicFormat::Feed => {
                        "application/atom+xml, application/rss+xml, application/xml, text/xml"
                    }
                    PublicFormat::Json => "application/json, application/ld+json, */*;q=0.1",
                },
            )
            .header(header::ACCEPT_ENCODING, "identity");
        if let Some(item) = &previous {
            if let Some(tag) = &item.etag {
                request = request.header(header::IF_NONE_MATCH, tag);
            }
            if let Some(date) = &item.modified {
                request = request.header(header::IF_MODIFIED_SINCE, date);
            }
        }
        let response = request.send().map_err(
            |_| "Public document connection failed. The last successful observation is retained.",
        )?;
        let status = response.status().as_u16();
        let mut observation = PageObservation {
            final_url: url.clone(),
            filtered_resources,
            http_status: Some(status),
            ..Default::default()
        };
        let headers = response.headers();
        let store_response = !headers.get_all(header::CACHE_CONTROL).iter().any(|v| {
            v.to_str().is_err()
                || v.to_str().unwrap_or("").split(',').any(|directive| {
                    let key = directive.trim().split('=').next().unwrap_or("");
                    key.eq_ignore_ascii_case("no-store") || key.eq_ignore_ascii_case("private")
                })
        });
        match status {
            304 => {
                let item = previous
                    .ok_or("Server returned 304 without a matching saved representation.")?;
                observation.title = item.title.clone();
                observation.text = item.text.clone();
                if !store_response {
                    self.cache
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .retain(|i| i.url != url || i.format != format);
                }
            }
            200 => {
                let etag = headers.get(header::ETAG).cloned();
                let modified = headers.get(header::LAST_MODIFIED).cloned();
                let mut bytes = Vec::new();
                response
                    .take((DOCUMENT_BYTES + 1) as u64)
                    .read_to_end(&mut bytes)
                    .map_err(|_| "The complete document body could not be read.")?;
                if bytes.len() > DOCUMENT_BYTES {
                    return Err(
                        "Structured document exceeds the reader limit. No partial observation was saved."
                            .into(),
                    );
                }
                let source =
                    std::str::from_utf8(&bytes).map_err(|_| "Structured document is not UTF-8.")?;
                let (title, text) = match format {
                    PublicFormat::Feed => entries(source)?,
                    PublicFormat::Json => ("JSON".to_owned(), json_text(source)?),
                };
                let mut cache = self.cache.lock().unwrap_or_else(|e| e.into_inner());
                cache.retain(|i| i.url != url || i.format != format);
                if store_response && (etag.is_some() || modified.is_some()) {
                    // Eviction only changes the next GET from conditional to unconditional.
                    if cache.len() >= 32 {
                        cache.pop_front();
                    }
                    cache.push_back(Arc::new(CachedDocument {
                        format,
                        url,
                        etag,
                        modified,
                        title: title.clone(),
                        text: text.clone(),
                    }));
                }
                observation.title = title;
                observation.text = text;
            }
            _ => {
                observation.status = if status == 401 {
                    "signInRequired"
                } else {
                    "unavailable"
                }
                .into();
                observation.message = format!(
                    "Public document returned HTTP {status}. Check its exact address and access requirements."
                );
            }
        }
        Ok(observation)
    }
}

fn json_text(source: &str) -> Result<String, String> {
    use serde::Deserialize;

    // Validate the entire document without converting provider IDs to floating point.
    let mut decoder = serde_json::Deserializer::from_str(source);
    serde::de::IgnoredAny::deserialize(&mut decoder)
        .and_then(|_| decoder.end())
        .map_err(|_| "The source did not return a complete JSON document.")?;
    let mut text = String::with_capacity(source.len().min(MAX_CAPTURE_BYTES));
    let mut quoted = false;
    let mut escaped = false;
    for c in source.chars() {
        if !quoted && matches!(c, ' ' | '\t' | '\n' | '\r') {
            continue;
        }
        text.push(c);
        if escaped {
            escaped = false;
        } else if quoted && c == '\\' {
            escaped = true;
        } else if c == '"' {
            quoted = !quoted;
        } else if !quoted && c == ',' {
            text.push('\n');
        }
        if text.len() > MAX_CAPTURE_BYTES {
            return Err("Complete JSON exceeds the observation limit.".into());
        }
    }
    Ok(text)
}

fn entries(xml: &str) -> Result<(String, String), String> {
    let document =
        Document::parse(xml).map_err(|_| "Feed is malformed or uses unsupported XML entities.")?;
    let root = document.root_element();
    let (container, entry_name) = if root.has_tag_name((ATOM, "feed")) {
        (root, "entry")
    } else if root.has_tag_name("rss") && root.tag_name().namespace().is_none() {
        let channel = root
            .children()
            .find(|c| c.has_tag_name("channel") && c.tag_name().namespace().is_none())
            .ok_or("RSS feed has no channel.")?;
        (channel, "item")
    } else {
        return Err("Expected an RSS channel or Atom feed. Clear the feed selector to watch this as a rendered page.".into());
    };
    let ns = container.tag_name().namespace();
    let title = container
        .children()
        .find(|n| {
            n.is_element() && n.tag_name().namespace() == ns && n.tag_name().name() == "title"
        })
        .and_then(|n| n.text())
        .unwrap_or("")
        .trim()
        .to_owned();
    if title.is_empty() || title.len() > MAX_CAPTURE_BYTES {
        return Err(
            "Feed is missing a readable channel title or exceeds the observation limit.".into(),
        );
    }
    let mut output = format!("Feed {title}");
    for ancestor in container.ancestors().filter(Node::is_element) {
        if let Some(base) = ancestor.attribute((roxmltree::NS_XML_URI, "base")) {
            output.push_str("\nBase URL ");
            output.push_str(base);
        }
    }
    for entry in container.children().filter(|n| {
        n.is_element() && n.tag_name().namespace() == ns && n.tag_name().name() == entry_name
    }) {
        output.push_str("\nEntry");
        for node in entry.descendants() {
            if node.is_element() {
                let tag = node.tag_name();
                output.push('\n');
                if tag.namespace() != ns {
                    output.push_str(tag.namespace().unwrap_or(""));
                    output.push(' ');
                }
                output.push_str(tag.name());
                let mut attrs: Vec<_> = node.attributes().collect();
                attrs.sort_unstable_by_key(|a| (a.namespace(), a.name()));
                for attr in attrs {
                    output.push(' ');
                    if let Some(namespace) = attr.namespace() {
                        output.push_str(namespace);
                        output.push(' ');
                    }
                    output.push_str(attr.name());
                    output.push('=');
                    output.push_str(attr.value());
                }
            } else if let Some(text) = node.text().filter(|_| node.is_text()) {
                let text = text.trim();
                if !text.is_empty() {
                    output.push(' ');
                    output.push_str(text);
                }
            }
            if output.len() > MAX_CAPTURE_BYTES {
                return Err("Feed entries exceed the complete observation size. No partial entry list was saved.".into());
            }
        }
    }
    Ok((title, output))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn json_preserves_large_identifiers_string_spaces_and_complete_structure() {
        let source = r#"{ "id": 90071992547409932164, "value": "a  \"b\" \\" }"#;
        let normalized = json_text(source).unwrap();
        assert!(normalized.contains("90071992547409932164"));
        assert!(normalized.contains(r#"a  \"b\" \\"#));
        assert_eq!(normalized, json_text(&source.replace("{ ", "{\n")).unwrap());
        assert_ne!(normalized, json_text(&source.replace("a  ", "a ")).unwrap());
        for invalid in ["", "null false", "{\"x\":}", "[] garbage", "01"] {
            assert!(json_text(invalid).is_err());
        }
    }
    #[test]
    fn atom_and_rss_cover_all_entries_links_and_edits() {
        let rss = r#"<rss><channel><title>Files</title><lastBuildDate>now</lastBuildDate><item><guid>1</guid><title>A</title></item><item><title>B</title><enclosure url="old"/></item></channel></rss>"#;
        let text = entries(rss).unwrap().1;
        assert_eq!(text.matches("\nEntry\n").count(), 2);
        assert_eq!(text, entries(&rss.replace("now", "later")).unwrap().1);
        assert_ne!(text, entries(&rss.replace("old", "new")).unwrap().1);
        let atom = r#"<feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title><entry><id>42</id><link href="a"/><content>Notes</content></entry></feed>"#;
        assert!(entries(atom).unwrap().1.contains("link href=a"));
        for invalid in [
            "<html/>",
            "<rss/>",
            "<rss><channel></rss>",
            "<!DOCTYPE x [<!ENTITY v 'test'>]><rss><channel/></rss>",
        ] {
            assert!(entries(invalid).is_err());
        }
    }
}
