from __future__ import annotations

from pathlib import Path

from kalshi_python_sync.auth import KalshiAuth as _SdkKalshiAuth


class KalshiAuth(_SdkKalshiAuth):
    """Thin SDK-backed auth wrapper with project-compatible constructor."""

    @classmethod
    def from_pem_file(cls, *, api_key_id: str, private_key_path: Path) -> "KalshiAuth":
        return cls(key_id=api_key_id, private_key_pem=private_key_path.read_text())

