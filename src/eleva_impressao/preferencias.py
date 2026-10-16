"""O que o programa lembra entre uma sessão e outra.

Existe por um motivo concreto: descobrir que a impressora precisa do modo TSPL
custou **duas tiras de etiqueta**. Se a escolha se perdesse a cada vez que o
programa abre, o custo se repetiria toda semana — e sempre no dia corrido, que é
quando ninguém confere o seletor antes de clicar em Imprimir.

O modo é guardado **por impressora**, não global: a térmica de etiqueta quer
TSPL, mas uma impressora comum de escritório na mesma máquina só entende o
caminho do driver. Guardar um valor só faria a segunda parar de funcionar.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# Importa o módulo, e não a função: assim ``dir_dados`` é resolvido na hora da
# chamada. Importado por valor, o teste que troca a pasta de dados não teria
# efeito aqui e as preferências de teste iriam parar na pasta de verdade.
from eleva_impressao import formatos

ARQUIVO = "preferencias.json"


@dataclass(slots=True)
class Impressora:
    modo: str = "imagem"
    gap_mm: float = 2.0


@dataclass(slots=True)
class Preferencias:
    formato_id: str = ""
    # O formato usado com PDF fica à parte: abrir um PDF não pode trocar o
    # formato do ZPL de amanhã, e vice-versa.
    formato_pdf_id: str = ""
    impressora: str = ""
    impressoras: dict[str, Impressora] = field(default_factory=dict)

    def da_impressora(self, nome: str) -> Impressora:
        return self.impressoras.get(nome, Impressora())

    def dicionario(self) -> dict:
        dados = asdict(self)
        dados["impressoras"] = {n: asdict(i) for n, i in self.impressoras.items()}
        return dados


def caminho():
    return formatos.dir_dados() / ARQUIVO


def carregar() -> Preferencias:
    """Arquivo ausente ou corrompido devolve o padrão — nunca impede de abrir.

    Lê como ``utf-8-sig`` de propósito: no Windows, quase tudo que grava texto
    (o Bloco de Notas, o PowerShell) põe um BOM na frente. Lido como ``utf-8``
    puro, esse byte invisível quebra o JSON e a preferência voltava ao padrão
    sem dizer nada — o tipo de falha que só aparece como "ué, mudou sozinho".
    """
    arquivo = caminho()
    if not arquivo.exists():
        return Preferencias()
    try:
        bruto = json.loads(arquivo.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, ValueError):
        return Preferencias()

    impressoras = {}
    for nome, valores in (bruto.get("impressoras") or {}).items():
        if isinstance(valores, dict):
            impressoras[str(nome)] = Impressora(
                modo=str(valores.get("modo", "imagem")),
                gap_mm=float(valores.get("gap_mm", 2.0)),
            )
    return Preferencias(
        formato_id=str(bruto.get("formato_id", "")),
        formato_pdf_id=str(bruto.get("formato_pdf_id", "")),
        impressora=str(bruto.get("impressora", "")),
        impressoras=impressoras,
    )


def gravar(preferencias: Preferencias) -> Preferencias:
    caminho().write_text(
        json.dumps(preferencias.dicionario(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return preferencias


def lembrar(
    *,
    impressora: str = "",
    modo: str = "",
    gap_mm: float | None = None,
    formato_id: str = "",
    formato_pdf_id: str = "",
) -> Preferencias:
    """Guarda o que veio; o que não veio fica como estava."""
    atual = carregar()
    if formato_id:
        atual.formato_id = formato_id
    if formato_pdf_id:
        atual.formato_pdf_id = formato_pdf_id
    if impressora:
        atual.impressora = impressora
        anterior = atual.da_impressora(impressora)
        atual.impressoras[impressora] = Impressora(
            modo=modo or anterior.modo,
            gap_mm=anterior.gap_mm if gap_mm is None else float(gap_mm),
        )
    return gravar(atual)


__all__ = ["Impressora", "Preferencias", "caminho", "carregar", "gravar", "lembrar"]
