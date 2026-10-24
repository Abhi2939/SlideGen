"""Resolve optional assets before rendering, with a bounded disk cache."""

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from app.assets.fetch import download, image_png
from app.assets.models import DeckAsset
from app.assets.photos import curated_photo


@dataclass(frozen=True)
class AssetSettings:
    enabled: bool = True
    cache_dir: Path = Path("data/assets")
    pexels_key: str | None = None
    budget_seconds: float = 20
    cache_max_bytes: int = 128 * 1024 * 1024
    curated_photos: bool = True
    unsplash_key: str | None = None

    @classmethod
    def from_env(cls):
        return cls(
            enabled=os.getenv("SLIDEGEN_ASSETS_ENABLED", "true").lower() in {"1", "true", "yes"},
            cache_dir=Path(
                os.getenv("SLIDEGEN_ASSET_CACHE_DIR")
                or Path(os.getenv("SLIDEGEN_DATA_DIR", "data")) / "assets"
            ),
            pexels_key=os.getenv("PEXELS_API_KEY") or None,
            unsplash_key=os.getenv("UNSPLASH_ACCESS_KEY") or None,
            curated_photos=os.getenv("SLIDEGEN_CURATED_PHOTOS_ENABLED", "true").lower()
            in {"1", "true", "yes"},
        )


def topic_query(title):
    if title in {
        "cosmetics makeup",
        "financial technology",
        "education learning",
        "renewable energy",
        "technology office",
        "dogs",
    }:
        return title
    for pattern, query in (
        (r"blush|beauty|cosmetic|lipstick|skincare|makeup", "cosmetics makeup"),
        (r"dog|puppy|canine", "dogs"),
        (r"edtech|education|learning|student|school", "education learning"),
        (r"fintech|payment|bank|finance|acme", "financial technology"),
        (r"solar|renewable|energy", "renewable energy"),
        (r"invoice|saas|software|technology", "technology office"),
    ):
        if re.search(pattern, title, re.I):
            return query
    return "business workspace"


def icon_name(text):
    for pattern, name in (
        (r"ugc|creator|demo", "video"),
        (r"subscription|repeat", "repeat"),
        (r"pop.up|store", "store"),
        (r"festive|festival", "party-popper"),
        (r"launch", "rocket"),
        (r"start|begin", "play"),
        (r"review|choice|options", "list-checks"),
        (r"fingerprint|biometric", "fingerprint"),
        (r"kyc|verify|verification|security", "shield-check"),
        (r"invoice|document|receipt", "file-text"),
        (r"pay|transaction|transfer", "arrow-left-right"),
        (r"fund|budget|allocation", "hand-coins"),
        (r"sign|customer|profile|onboard", "user-round"),
        (r"quiz|choice|preferences", "list-checks"),
        (r"growth|roadmap|scale", "trending-up"),
        (r"beauty|shade|product|cosmetic", "sparkles"),
        (r"complete|confirm", "check"),
    ):
        if re.search(pattern, text, re.I):
            return name
    return None


def _write(path: Path, data: bytes):
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class AssetResolver:
    def __init__(self, settings=None):
        self.settings = settings or AssetSettings.from_env()

    def _photo_candidates(self, query, variant):
        """Search configured APIs, then use source-attributed open downloads."""
        if self.settings.pexels_key:
            try:
                params = urlencode({"query": query, "per_page": 1, "page": variant + 1})
                data, kind = download(
                    f"https://api.pexels.com/v1/search?{params}",
                    headers={"Authorization": self.settings.pexels_key},
                    max_bytes=256_000,
                )
                if kind == "application/json":
                    for photo in json.loads(data).get("photos", []):
                        yield (
                            photo["src"]["large"],
                            photo["url"],
                            photo["photographer"],
                            "Pexels License; API attribution required; https://www.pexels.com/license/",
                        )
            except Exception:
                pass
        if self.settings.unsplash_key:
            try:
                params = urlencode({"query": query, "per_page": 1, "page": variant + 1})
                data, kind = download(
                    f"https://api.unsplash.com/search/photos?{params}",
                    headers={"Authorization": "Client-ID " + self.settings.unsplash_key},
                    max_bytes=256_000,
                )
                if kind == "application/json":
                    for photo in json.loads(data).get("results", []):
                        # Record the API-required download event without following its redirect.
                        location = photo.get("links", {}).get("download_location")
                        if location:
                            download(
                                location,
                                headers={
                                    "Authorization": "Client-ID " + self.settings.unsplash_key
                                },
                                max_bytes=256_000,
                            )
                        yield (
                            photo["urls"]["regular"],
                            photo["links"]["html"],
                            photo["user"]["name"],
                            "Unsplash API attribution required; https://unsplash.com/license",
                        )
            except Exception:
                pass
        if self.settings.curated_photos:
            from app.assets.photos import PHOTO_CATALOG

            category = query if query in PHOTO_CATALOG else topic_query(query)
            choices = PHOTO_CATALOG.get(category, ())
            if choices:
                photo = curated_photo(category, variant % len(choices))
                yield (
                    photo["src"]["large"],
                    photo["url"],
                    photo["photographer"],
                    "Pexels License; https://www.pexels.com/license/",
                )

    def _resolve(self, role, query, variant):
        settings = self.settings
        if (
            role == "photo"
            and not settings.pexels_key
            and not settings.unsplash_key
            and not settings.curated_photos
        ):
            return None
        identity = f"v1:{role}:{query}:{variant}"
        if role == "photo":
            identity += f":photo-v3:{bool(settings.pexels_key)}:{bool(settings.unsplash_key)}"
        digest = hashlib.sha256(identity.encode()).hexdigest()
        meta = settings.cache_dir / f"{digest}.json"
        image_path = settings.cache_dir / f"{digest}.png"
        try:
            cached = json.loads(meta.read_text(encoding="utf-8"))
            content = image_path.read_bytes()
            if hashlib.sha256(content).hexdigest() == cached["sha256"]:
                image_png(content, "image/png")
                return {**cached, "path": str(image_path.resolve())}
        except (OSError, ValueError, KeyError):
            pass
        photographer = None
        if role == "photo":
            candidates = self._photo_candidates(query, variant)
            png = None
            for url, source, photographer, licence in candidates:
                if urlsplit(source).scheme != "https" or urlsplit(source).hostname not in {
                    "www.pexels.com",
                    "unsplash.com",
                }:
                    continue
                if any(not c.isprintable() for c in source):
                    continue
                try:
                    data, content_type = download(url)
                    png = image_png(data, content_type)
                    break
                except Exception:
                    continue
            if png is None:
                return None
            photographer = "".join(c for c in str(photographer) if c.isprintable())[:120]
            licence += "; illustrative stock photo, no endorsement"
        elif role == "icon":
            url = f"https://api.iconify.design/lucide/{query}.svg?color=%23{variant}"
            source, licence = url, "Lucide ISC / Feather-derived MIT; https://lucide.dev/license"
        else:
            url = f"https://cdn.simpleicons.org/{query}"
            source = url
            licence = "Simple Icons CC0 collection; individual brand terms/trademarks apply; https://simpleicons.org/"
        if role != "photo":
            data, content_type = download(url)
            png = image_png(data, content_type)
        record = {
            "role": role,
            "query": query,
            "sha256": hashlib.sha256(png).hexdigest(),
            "source_url": source,
            "licence": licence,
            "photographer": photographer,
        }
        _write(image_path, png)
        _write(meta, json.dumps(record, ensure_ascii=False).encode())
        return {**record, "path": str(image_path.resolve())}

    def resolve(self, deck, source_text: str, *, deadline=None):
        if not self.settings.enabled:
            return deck.model_copy(update={"assets": []})
        try:
            self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
            self._prune()
            budget = self.settings.budget_seconds
            if deadline is not None:
                budget = min(budget, max(0, deadline - time.monotonic() - 2))
            if budget <= 0:
                return deck
            plans = []
            query = topic_query(" ".join([deck.title, *(slide.title for slide in deck.slides)]))
            photo_number = 0
            for index, slide in enumerate(deck.slides):
                color = "ffffff" if slide.layout in {"title", "closing", "section"} else "1b365d"
                if slide.layout == "editorial":
                    from app.render.theme import get_theme

                    color = str(get_theme(deck.theme).primary).lower()
                    if slide.archetype in {
                        "cover",
                        "split_image",
                        "big_stat",
                        "quote",
                        "closing_cta",
                        "three_cards",
                        "four_cards",
                        "grid_2x2",
                        "comparison",
                        "process_steps",
                    }:
                        asset_query = slide.image_query or query
                        if not (self.settings.pexels_key or self.settings.unsplash_key):
                            from app.assets.photos import PHOTO_CATALOG

                            asset_query = topic_query(asset_query)
                            if asset_query not in PHOTO_CATALOG:
                                asset_query = query
                        plans.append(
                            (
                                index,
                                "hero",
                                "photo",
                                asset_query,
                                photo_number
                                + (slide.layout_variant if slide.archetype != "cover" else 0),
                            )
                        )
                        photo_number += 1
                    for number, item in enumerate(slide.items):
                        name = icon_name(item.title)
                        if number < len(slide.icon_names):
                            requested = slide.icon_names[number]
                            if (
                                re.fullmatch(r"[a-z][a-z0-9-]{1,45}", requested)
                                and requested != "circle"
                            ):
                                name = requested
                        if name:
                            plans.append((index, f"item:{number}", "icon", name, color))
                if slide.layout == "title":
                    plans.append((index, "hero", "photo", query, 0))
                if slide.layout == "ask_allocation":
                    plans.extend(
                        [
                            (index, "hero", "photo", query, 1),
                            (index, "edge", "photo", query, 2),
                            (index, "centre", "icon", "hand-coins", "1b365d"),
                        ]
                    )
                if slide.layout in {"flow_steps", "process"}:
                    for number, step in enumerate(slide.steps):
                        name = icon_name(step.title)
                        if name:
                            plans.append((index, f"step:{number}", "icon", name, "ffffff"))
                if slide.layout == "quadrants":
                    for quadrant in slide.quadrants:
                        for name in quadrant.names:
                            # Names must be verbatim evidence, never invented to fill a grid.
                            if not name.startswith("[") and re.search(
                                r"(?<!\w)" + re.escape(name) + r"(?!\w)", source_text, re.I
                            ):
                                slug = re.sub(r"[^a-z0-9]", "", name.lower())
                                if slug:
                                    plans.append((index, name, "logo", slug, 0))
                if slide.layout == "market_snapshot":
                    for number, row in enumerate(slide.table_rows):
                        plans.append(
                            (index, f"driver:{number}", "icon", icon_name(row[0]), "1b365d")
                        )
            unique = {(p[2], p[3], p[4]) for p in plans}
            executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="slidegen-assets")
            futures = {executor.submit(self._resolve, *item): item for item in unique}
            try:
                done, pending = wait(futures, timeout=budget)
                results = {}
                for future in done:
                    try:
                        results[futures[future]] = future.result()
                    except Exception:
                        # Asset failures are expected fallbacks. No raw URLs/errors/keys in logs.
                        pass
                for future in pending:
                    future.cancel()
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            assets, used_photos = [], set()
            for index, key, role, query, variant in plans:
                record = results.get((role, query, variant))
                if not record or (
                    role == "photo"
                    and record["source_url"] in used_photos
                    and deck.design_version != "gamma-v1"
                ):
                    continue
                if role == "photo":
                    used_photos.add(record["source_url"])
                assets.append(DeckAsset(slide_index=index, key=key, **record))
            manifest_id = hashlib.sha256(
                deck.model_dump_json(exclude={"assets"}).encode()
            ).hexdigest()
            manifests = self.settings.cache_dir / "manifests"
            manifests.mkdir(exist_ok=True)
            _write(
                manifests / f"{manifest_id}.json",
                json.dumps([a.model_dump() for a in assets], indent=2).encode(),
            )
            return deck.model_copy(update={"assets": assets})
        except Exception:
            return deck.model_copy(update={"assets": []})

    def _prune(self):
        files = sorted(
            (
                p
                for p in self.settings.cache_dir.rglob("*")
                if p.is_file() and p.suffix in {".png", ".json"}
            ),
            key=lambda p: p.stat().st_mtime,
        )
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= self.settings.cache_max_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size


def prepare_assets(deck, source_text, *, deadline=None):
    return AssetResolver().resolve(deck, source_text, deadline=deadline)
