"""Project-owned display references only; no scraping, fetching or bundled artwork."""
from urllib.parse import urlsplit

HOSTS = {"staticdelivery.nexusmods.com", "cdn.modrinth.com", "thunderstore.io", "cdn.thunderstore.io", "gcdn.thunderstore.io"}

def enemy_art(item):
    result = []
    seen = set()
    for row in (item, item.get("update"), item.get("sourceUpdate"), item.get("source")):
        if not isinstance(row, dict):
            continue
        author = row.get("author")
        candidates = [("mod", row.get(k)) for k in ("thumbnailUrl", "imageUrl", "previewUrl", "iconUrl")]
        candidates += [("author", row.get("authorAvatarUrl")), ("author", author.get("avatarUrl") if isinstance(author, dict) else None)]
        for kind, value in candidates:
            if not isinstance(value, str) or len(value) > 8192:
                continue
            try:
                url = urlsplit(value)
                if url.scheme != "https" or url.hostname not in HOSTS or url.username or url.password or url.port not in (None, 443):
                    continue
            except ValueError:
                continue
            if (kind, value) not in seen:
                seen.add((kind, value))
                result.append({"kind": kind, "url": value})
    return result
