"""Limitador de pedidos por IP — classe RateLimiter (extraída de server.py)."""
from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    """Janela deslizante de 60s por chave (IP). A limpeza de IPs inativos corre no
    máximo uma vez por `sweep_every` segundos (não a cada pedido) e o número de
    IPs seguidos é limitado — um cliente a rodar IPs falsos não degrada o lock."""

    def __init__(self, per_minute: int, max_keys: int = 10_000, sweep_every: float = 60.0):
        self.per_minute = per_minute  # 0 = desligado
        self.max_keys = max_keys
        self.sweep_every = sweep_every
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def allow(self, ip: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            if now - self._last_sweep >= self.sweep_every:
                self._sweep(now)
            dq = self._hits.get(ip)
            if dq is None:
                if len(self._hits) >= self.max_keys:
                    self._sweep(now)
                    if len(self._hits) >= self.max_keys:
                        # Tabela cheia de IPs ativos: esquece o mais antigo.
                        self._hits.pop(next(iter(self._hits)))
                dq = self._hits[ip] = deque()
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) >= self.per_minute:
                return False
            dq.append(now)
            return True

    def _sweep(self, now):
        """Esquecer IPs sem atividade há mais de 60s (a janela já expirou)."""
        self._last_sweep = now
        for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
            del self._hits[k]


class ConcurrencyLimiter:
    """Máximo de pedidos EM CURSO por chave (IP). acquire() nunca bloqueia:
    devolve False quando a chave já está no limite. A tabela só guarda chaves
    com pedidos em curso."""

    def __init__(self, per_key: int):
        self.per_key = per_key
        self._active: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> bool:
        with self._lock:
            n = self._active.get(key, 0)
            if n >= self.per_key:
                return False
            self._active[key] = n + 1
            return True

    def release(self, key: str):
        with self._lock:
            n = self._active.get(key, 0) - 1
            if n > 0:
                self._active[key] = n
            else:
                self._active.pop(key, None)
