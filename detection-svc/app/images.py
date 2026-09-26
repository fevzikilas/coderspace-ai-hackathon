"""Görüntü çözme / indirme / önbellek yardımcıları."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ipaddress
import socket
import threading
from collections import OrderedDict
from urllib.parse import urlparse

import httpx
from PIL import Image

from .config import Settings


class ImageError(ValueError):
    """İstemci kaynaklı görüntü hatası (HTTP 400)."""


def decode_b64(data: str, max_bytes: int) -> bytes:
    if data.startswith("data:"):
        _, _, data = data.partition(",")
    try:
        raw = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageError(f"image_b64 çözülemedi: {exc}") from exc
    if not raw:
        raise ImageError("image_b64 boş")
    if len(raw) > max_bytes:
        raise ImageError(f"Görüntü çok büyük (>{max_bytes} bayt)")
    return raw


def _check_url(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageError("image_url yalnızca http(s) olabilir")
    host = parsed.hostname
    if settings.image_url_allowlist and host not in settings.image_url_allowlist:
        raise ImageError(f"image_url host'u izinli değil: {host}")
    if settings.allow_private_image_urls:
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ImageError(f"image_url çözümlenemedi: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ImageError("image_url özel/loopback adrese işaret ediyor (ALLOW_PRIVATE_IMAGE_URLS=true ile açılır)")


def fetch_url(url: str, settings: Settings) -> bytes:
    _check_url(url, settings)
    try:
        with httpx.Client(timeout=settings.fetch_timeout_s, follow_redirects=False) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ImageError(f"image_url {resp.status_code} döndürdü")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > settings.max_image_bytes:
                        raise ImageError(f"Görüntü çok büyük (>{settings.max_image_bytes} bayt)")
    except httpx.HTTPError as exc:
        raise ImageError(f"image_url indirilemedi: {exc}") from exc
    return bytes(buf)


_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def open_image(raw: bytes) -> tuple[Image.Image, str]:
    """(RGB görüntü, içerik türü). Format bilgisi RGB'ye çevirmeden önce okunur."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # Pillow çok çeşitli hata fırlatır
        raise ImageError(f"Geçerli bir görüntü değil: {exc}") from exc
    content_type = _CONTENT_TYPES.get((img.format or "").upper(), "application/octet-stream")
    return img.convert("RGB"), content_type


def make_image_id(raw: bytes | None, drone_id: str, timestamp: str) -> str:
    h = hashlib.sha1()
    if raw is not None:
        h.update(raw)
    else:
        h.update(f"{drone_id}|{timestamp}".encode())
    return "img-" + h.hexdigest()[:12]


class ImageCache:
    """Son N görüntüyü bellekte tutan basit LRU (gateway/UI `GET /images/{id}` için)."""

    def __init__(self, size: int) -> None:
        self._size = max(1, size)
        self._items: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, image_id: str, raw: bytes, content_type: str) -> None:
        with self._lock:
            self._items[image_id] = (raw, content_type)
            self._items.move_to_end(image_id)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def get(self, image_id: str) -> tuple[bytes, str] | None:
        with self._lock:
            item = self._items.get(image_id)
            if item is not None:
                self._items.move_to_end(image_id)
            return item
