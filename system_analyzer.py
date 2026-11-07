"""
system_analyzer.py — Detecta recursos do sistema e recomenda configuracoes otimizadas.
"""

import psutil
import os
import platform
from pathlib import Path
from dataclasses import dataclass
from typing import Tuple, Optional

@dataclass
class SystemProfile:
    """Perfil completo do sistema do usuario."""
    ram_total_gb: float
    ram_available_gb: float
    ram_percent_used: float
    disk_total_gb: float
    disk_free_gb: float
    disk_usage_percent: float
    disk_type: str
    cpu_count_physical: int
    cpu_count_logical: int
    cpu_freq_mhz: float
    recommended_workers: int
    max_safe_workers: int
    min_workers: int
    estimated_buffer_gb: float
    can_handle_large_files: bool
    is_low_end_pc: bool
    is_high_end_pc: bool


class SystemAnalyzer:
    """Analisa recursos do sistema e sugere configuracoes otimizadas."""
    
    RAM_PER_WORKER_GB = 0.5
    DISK_PER_WORKER_GB = 2.0
    MIN_WORKERS = 1
    MAX_WORKERS_HARD = 16
    
    def __init__(self):
        self.profile = self._analyze()
    
    def _analyze(self) -> SystemProfile:
        """Coleta todas as metricas do sistema."""
        mem = psutil.virtual_memory()
        ram_total = mem.total / (1024**3)
        ram_available = mem.available / (1024**3)
        
        app_path = Path(__file__).parent
        try:
            disk = psutil.disk_usage(app_path)
        except:
            disk = psutil.disk_usage(os.getcwd())
        
        disk_total = disk.total / (1024**3)
        disk_free = disk.free / (1024**3)
        
        disk_type = self._detect_disk_type()
        
        cpu_count_phys = psutil.cpu_count(logical=False) or 2
        cpu_count_log = psutil.cpu_count(logical=True) or 4
        cpu_freq = psutil.cpu_freq().current if psutil.cpu_freq() else 2500
        
        rec_workers, max_workers, min_workers = self._calculate_workers(
            ram_available, disk_free, cpu_count_log
        )
        
        estimated_buffer = min(ram_available * 0.6, disk_free * 0.2)
        
        is_low_end = (ram_total < 4 or cpu_count_log < 4 or disk_free < 10)
        is_high_end = (ram_total >= 16 and cpu_count_log >= 8 and disk_free >= 50)
        
        return SystemProfile(
            ram_total_gb=round(ram_total, 2),
            ram_available_gb=round(ram_available, 2),
            ram_percent_used=mem.percent,
            disk_total_gb=round(disk_total, 2),
            disk_free_gb=round(disk_free, 2),
            disk_usage_percent=disk.percent,
            disk_type=disk_type,
            cpu_count_physical=cpu_count_phys,
            cpu_count_logical=cpu_count_log,
            cpu_freq_mhz=cpu_freq,
            recommended_workers=rec_workers,
            max_safe_workers=max_workers,
            min_workers=min_workers,
            estimated_buffer_gb=round(estimated_buffer, 2),
            can_handle_large_files=disk_free > 20 and ram_available > 4,
            is_low_end_pc=is_low_end,
            is_high_end_pc=is_high_end
        )
    
    def _detect_disk_type(self) -> str:
        """Detecta se e SSD ou HDD."""
        try:
            if os.name == 'nt':
                return "SSD"
            else:
                return "SSD" if self._quick_io_test() else "HDD"
        except:
            return "Desconhecido"
    
    def _quick_io_test(self) -> bool:
        """Teste rapido de I/O."""
        try:
            import tempfile
            import time
            
            test_file = Path(tempfile.gettempdir()) / "tc_io_test.tmp"
            data = b"0" * (1024 * 1024)
            
            start = time.time()
            with open(test_file, 'wb') as f:
                f.write(data)
            write_time = time.time() - start
            
            try:
                test_file.unlink()
            except:
                pass
            
            return write_time < 0.1
        except:
            return True
    
    def _calculate_workers(self, ram_gb: float, disk_gb: float, cpu_cores: int) -> Tuple[int, int, int]:
        """Calcula workers recomendados."""
        by_ram = int(ram_gb / self.RAM_PER_WORKER_GB)
        by_disk = int(disk_gb / self.DISK_PER_WORKER_GB)
        by_cpu = min(cpu_cores * 2, 8)
        
        max_safe = min(by_ram, by_disk, by_cpu, self.MAX_WORKERS_HARD)
        max_safe = max(max_safe, self.MIN_WORKERS)
        
        recommended = max(int(max_safe * 0.6), self.MIN_WORKERS)
        
        return recommended, max_safe, self.MIN_WORKERS
    
    def estimate_for_channel(self, size_gb: Optional[float] = None) -> dict:
        """Estima capacidade para um canal especifico."""
        p = self.profile
        
        if not size_gb:
            return {
                "can_process": True,
                "recommended": p.recommended_workers,
                "warning": None
            }
        
        warnings = []
        
        if size_gb > p.disk_free_gb * 0.8:
            warnings.append(f"Canal grande ({size_gb}GB). Disco com {p.disk_free_gb}GB livres.")
        
        needed_ram = size_gb * 0.1
        if needed_ram > p.ram_available_gb:
            warnings.append(f"Requer ~{needed_ram:.1f}GB RAM. Disponivel: {p.ram_available_gb:.1f}GB.")
            safe_workers = max(1, p.recommended_workers // 2)
        else:
            safe_workers = p.recommended_workers
        
        if p.is_low_end_pc and safe_workers > 2:
            safe_workers = 2
            warnings.append("Modo economico ativado (PC com recursos limitados).")
        
        return {
            "can_process": len(warnings) == 0 or p.disk_free_gb > size_gb,
            "recommended": safe_workers,
            "max": p.max_safe_workers,
            "warning": " | ".join(warnings) if warnings else None
        }
        
analyzer = SystemAnalyzer()