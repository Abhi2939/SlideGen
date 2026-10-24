"""Small HTTPS allowlist, DNS pinning, bounded streams, and safe image decoding."""

import io
import ipaddress
import socket
import time
from urllib.parse import urlsplit

import httpx
from lxml import etree
from PIL import Image, ImageOps
import resvg_py


FETCH_HOSTS = frozenset(
    {
        "api.iconify.design",
        "cdn.simpleicons.org",
        "api.pexels.com",
        "images.pexels.com",
        "api.unsplash.com",
        "images.unsplash.com",
    }
)
MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 8_000_000


def public_address(host: str) -> str:
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
        raise ValueError("asset destination is not public")
    return sorted(addresses)[0]


def download(url: str, *, headers=None, timeout=6.0, max_bytes=MAX_BYTES) -> tuple[bytes, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in FETCH_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("asset URL is not allowed")
    host = parsed.hostname
    address = public_address(host)
    # Connect to the checked IP, retaining the original Host and TLS SNI.
    # Redirects are rejected, closing both DNS-rebinding and redirect SSRF paths.
    target = httpx.URL(url).copy_with(host=address)
    request_headers = {"Host": host, **(headers or {})}
    end = time.monotonic() + timeout
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout) as client:
        with client.stream(
            "GET", target, headers=request_headers, extensions={"sni_hostname": host}
        ) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("asset redirects are not allowed")
            length = response.headers.get("content-length")
            if length and (not length.isdecimal() or int(length) > max_bytes):
                raise ValueError("asset response is too large")
            chunks, size = [], 0
            for chunk in response.iter_bytes(chunk_size=65536):
                size += len(chunk)
                if size > max_bytes or time.monotonic() > end:
                    raise ValueError("asset response exceeded its budget")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type", "").split(";")[0].lower()


def image_png(data: bytes, content_type: str) -> bytes:
    if content_type == "image/svg+xml":
        if len(data) > 128_000 or b"<!" in data:
            raise ValueError("unsafe SVG")
        root = etree.fromstring(
            data, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
        tags = {
            "svg",
            "g",
            "path",
            "circle",
            "rect",
            "line",
            "polyline",
            "polygon",
            "ellipse",
            "title",
        }
        if len(list(root.iter())) > 128:
            raise ValueError("SVG is too complex")
        for element in root.iter():
            if etree.QName(element).localname not in tags:
                raise ValueError("SVG contains unsupported resources")
            for name, value in element.attrib.items():
                if etree.QName(name).localname in {"href", "style"} or "url(" in value.lower():
                    raise ValueError("SVG contains external resources")
        data = resvg_py.svg_to_bytes(
            svg_string=etree.tostring(root).decode(), width=256, height=256, skip_system_fonts=True
        )
    elif content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("asset content type is not an image")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_PIXELS or image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("asset image is unsupported or too large")
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image.thumbnail((1600, 1200))
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()
