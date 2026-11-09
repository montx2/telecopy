"""O entrypoint interativo deve converter SIGTERM/SIGBREAK em interrupção limpa."""
from __future__ import annotations

import signal

import pytest

import main


def test_main_instala_e_restaura_sinais_de_interrupcao() -> None:
    originais = {sig: signal.getsignal(sig) for sig in main._sinais_interrupcao()}
    antigos = main._instalar_sinais_interrupcao()
    try:
        for sig in main._sinais_interrupcao():
            handler = signal.getsignal(sig)
            assert callable(handler)
            with pytest.raises(KeyboardInterrupt):
                handler(sig, None)
    finally:
        main._restaurar_sinais(antigos)
    for sig, old in originais.items():
        assert signal.getsignal(sig) is old or signal.getsignal(sig) == old


def test_main_restauracao_tolera_sinal_invalido() -> None:
    main._restaurar_sinais({})
    main._restaurar_sinais({-1: signal.SIG_DFL})
