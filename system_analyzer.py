"""
system_analyzer.py — Perfil de hardware e recomendação de configuração.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from config import SystemProfile, detect_system, recommended_workers, turbo_settings


@dataclass
class ChannelEstimate:
    size_gb: Optional[float]
    recommended_workers: int
    max_workers: int
    warning: Optional[str]
    can_process: bool = True


class SystemAnalyzer:
    """Calcula parâmetros seguros de execução a partir do hardware."""

    RAM_PER_WORKER_GB = 0.5
    DISK_PER_WORKER_GB = 2.0
    MAX_WORKERS_HARD = 8

    def __init__(self) -> None:
        self._profile: Optional[SystemProfile] = None

    @property
    def profile(self) -> SystemProfile:
        self.refresh()
        return self._profile  # type: ignore[return-value]

    def refresh(self) -> SystemProfile:
        self._profile = detect_system()
        return self._profile

    # -- workers ------------------------------------------------------------

    def worker_bounds(self) -> tuple[int, int]:
        """(mínimo, máximo) de workers: o máximo é o do perfil Turbo do config."""
        return 1, max(1, int(turbo_settings()["workers"]))

    def recommended(self, memory_profile: float = 0.6) -> int:
        minimum, maximum = self.worker_bounds()
        value = recommended_workers(self.profile.ram_available_gb, memory_profile)
        if self.profile.is_low_end:
            value = min(value, 2)
        return max(minimum, min(value, maximum))

    def estimate_for_channel(self, size_gb: Optional[float] = None) -> ChannelEstimate:
        p = self.profile
        _, maximum = self.worker_bounds()
        workers = self.recommended()
        warnings: list[str] = []

        if size_gb:
            if size_gb > p.disk_free_gb * 0.8:
                warnings.append(
                    f"Canal grande (~{size_gb:.1f} GB) e apenas {p.disk_free_gb:.1f} GB livres."
                )
            if p.is_low_end and workers > 2:
                workers = 2
                warnings.append("Modo econômico ativado (PC com poucos recursos).")

        return ChannelEstimate(
            size_gb=size_gb,
            recommended_workers=workers,
            max_workers=maximum,
            warning=" | ".join(warnings) if warnings else None,
            can_process=not (size_gb and size_gb > p.disk_free_gb),
        )

    # -- relatório ----------------------------------------------------------

    def summary(self) -> Dict[str, str]:
        p = self.profile
        return {
            "ram": f"{p.ram_available_gb:.1f} / {p.ram_total_gb:.1f} GB",
            "disk": f"{p.disk_free_gb:.1f} GB livres de {p.disk_total_gb:.1f} GB",
            "cpu": f"{p.cpu_logical} lógicos / {p.cpu_physical} físicos",
            "class": p.label,
            "workers": str(self.recommended()),
        }


analyzer = SystemAnalyzer()


def quick_report() -> Dict[str, str]:
    return analyzer.summary()


__all__ = ["SystemAnalyzer", "ChannelEstimate", "analyzer", "quick_report"]
