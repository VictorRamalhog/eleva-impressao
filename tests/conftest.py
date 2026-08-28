"""Infra dos testes do Eleva Impressão.

Só uma fixture: a API local, com pasta de dados própria e uma impressora
fingida. As duas coisas são para o teste não encostar na máquina real — nem no
`%LOCALAPPDATA%` de quem roda, nem numa impressora de verdade.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def cliente_impressao(tmp_path, monkeypatch):
    """A API local de impressão, com pasta de dados própria e sem impressora."""
    from fastapi.testclient import TestClient

    from eleva_impressao import app as aplicacao
    from eleva_impressao import formatos, windows

    monkeypatch.setattr(formatos, "dir_dados", lambda: tmp_path)
    monkeypatch.setattr(
        windows,
        "listar",
        lambda: [windows.Impressora("LABEL", "USB001", "LABEL", padrao=True)],
    )
    return TestClient(aplicacao.criar_app())
