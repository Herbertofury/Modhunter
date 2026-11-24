//! Convert local RSS reader exports into the ordinary reviewed Watchtower import format.
use super::url_policy::anonymous_source_url;
use roxmltree::Document;
use serde_json::{Value, json};

pub fn subscription_document(source: &str) -> Result<Value, String> {
    if source.len() > 8 * 1024 * 1024 {
        return Err("Subscription import exceeds 8 MiB.".into());
    }
    let xml = Document::parse(source.trim_start_matches('\u{feff}').trim_start())
        .map_err(|_| "Expected valid OPML without a document type declaration.")?;
    let root = xml.root_element();
    let body = root
        .children()
        .find(|n| n.has_tag_name("body"))
        .filter(|_| root.has_tag_name("opml"))
        .ok_or("Expected an OPML subscription list with a body.")?;
    let mut watches = Vec::new();
    let label = |n: roxmltree::Node<'_, '_>| {
        n.attribute("title")
            .or_else(|| n.attribute("text"))
            .unwrap_or("")
            .trim()
            .to_owned()
    };
    for node in body.descendants().filter(|n| n.has_tag_name("outline")) {
        let feed = match node.attribute("xmlUrl") {
            Some(raw) => anonymous_source_url(raw.trim()).map(|url| url.to_string()),
            None if node.attribute("url").is_some()
                || node.attribute("type").is_some_and(|t| {
                    ["rss", "atom", "include", "link"]
                        .iter()
                        .any(|known| t.eq_ignore_ascii_case(known))
                }) =>
            {
                Err("This linked outline needs an inline feed URL.".into())
            }
            None => continue,
        };
        let url = match feed {
            Ok(url) => url,
            Err(reason) => {
                // Private subscription addresses must not appear in a preview or saved export.
                watches.push(json!({"title":format!("Subscription {}", watches.len() + 1),"importError":reason}));
                continue;
            }
        };
        let mut folders: Vec<_> = node
            .ancestors()
            .skip(1)
            .filter(|n| n.has_tag_name("outline"))
            .map(label)
            .filter(|s| !s.is_empty())
            .collect();
        folders.reverse();
        let tags: Vec<_> = node
            .attribute("category")
            .unwrap_or("")
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect();
        watches.push(json!({"url":url,"title":label(node),"selector":"feed:","folder":folders.join(" / "),"tags":tags}));
    }
    if watches.is_empty() {
        return Err("No inline feed subscriptions were present in this OPML.".into());
    }
    Ok(json!({"kind":"modhunter-watchtower","schemaVersion":1,"watches":watches}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_nested_folders_tags_and_all_feed_urls_in_order() {
        let doc = subscription_document(r#"<opml version="2.0"><body><outline text="Games"><outline text="Skyrim"><outline text="Unofficial" title="Author &amp; patches" xmlUrl="https://example.com/first?view=issues" category="updates, Skyrim"/></outline><outline text="Other" xmlUrl="https://example.com/second"/></outline></body></opml>"#).unwrap();
        assert_eq!(doc["watches"][0]["title"], "Author & patches");
        assert_eq!(doc["watches"][0]["folder"], "Games / Skyrim");
        assert_eq!(doc["watches"][0]["tags"], json!(["updates", "Skyrim"]));
        assert_eq!(doc["watches"][1]["url"], "https://example.com/second");
        assert_eq!(doc["watches"][1]["selector"], "feed:");
    }
    #[test]
    fn rejects_partial_or_private_subscription_lists() {
        for entry in [
            r#"<outline type="include" url="https://example.com/list"/>"#,
            r#"<outline xmlUrl="https://example.com/rss?access_token=secret"/>"#,
            r#"<outline xmlUrl="http://127.0.0.1/feed"/>"#,
        ] {
            let source = format!(
                "<opml><body><outline xmlUrl=\"https://example.com/ok\"/>{entry}</body></opml>"
            );
            let doc = subscription_document(&source).unwrap();
            assert_eq!(doc["watches"].as_array().unwrap().len(), 2);
            assert!(doc["watches"][1]["importError"].is_string());
            assert!(!doc.to_string().contains("access_token"));
        }
    }
}
