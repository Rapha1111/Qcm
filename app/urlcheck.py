"""Validation des URL Moodle fournies par les utilisateurs (limite les risques de SSRF)."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def normalize_moodle_url(raw: str) -> str:
    """Renvoie l'URL de base (https, sans slash final) ou lève ValueError."""
    raw = raw.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise ValueError("L'URL doit commencer par https://")
    if not parts.hostname or parts.username or parts.password:
        raise ValueError("URL invalide")
    return f"https://{parts.netloc}{parts.path.rstrip('/')}"


def ensure_public_host(url: str) -> None:
    """Refuse les adresses locales/privées (bloquant : à lancer dans un thread)."""
    host = urlsplit(url).hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("Nom de domaine introuvable")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Cette adresse n'est pas autorisée")
