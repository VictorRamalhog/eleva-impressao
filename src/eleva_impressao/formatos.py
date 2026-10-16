"""Tamanhos de etiqueta — os embutidos e os que a operadora criar.

Duas decisões que valem explicação:

**Formato é dado, não código.** O tamanho da etiqueta muda quando o fornecedor
do papel muda; se estivesse hardcodado, cada rolo novo viraria um commit. Fica
num JSON que a própria tela edita.

**Coluna é propriedade do formato, não da impressão.** "Uma coluna" e "duas
colunas" não são dois botões na hora de imprimir: são papéis diferentes, com
largura diferente. Amarrar a coluna ao formato é o que impede mandar um
100 × 150 mm de uma coluna para um rolo de duas.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ARQUIVO = "formatos-etiqueta.json"
DPMM_VALIDOS = (6, 8, 12, 24)
LARGURA_MAXIMA_MM = 110.0  # limite do cabeçote da KP-IM604


class FormatoInvalido(ValueError):
    """Medida que a impressora não aceita (largura acima do cabeçote, etc.)."""


@dataclass(slots=True)
class Formato:
    id: str
    nome: str
    largura_mm: float
    altura_mm: float
    dpmm: int = 8
    colunas: int = 1
    espaco_colunas_mm: float = 2.0
    margem_mm: float = 0.0
    # Quem monta o par de colunas: nós ou o próprio ZPL?
    #
    # ``False`` (o normal): cada ``^XA`` é **uma** etiqueta de ``largura_mm`` e o
    # programa encosta duas para formar a folha.
    # ``True``: o ``^XA`` já vem com as duas colunas dentro dele, desenhadas em
    # ``^FO`` distantes — é assim que o Mercado Livre entrega a etiqueta de duas
    # vias. Aí a tela de desenho tem de ser a folha inteira, e não há o que
    # compor: uma etiqueta do arquivo já é uma folha.
    #
    # Desenhar um ZPL do segundo tipo com a régua do primeiro corta a coluna da
    # direita fora — e o programa avisa quando reconhece o sintoma.
    colunas_no_zpl: bool = False
    # Borda que o PDF não ocupa ao ser encaixado na etiqueta. Só vale para PDF:
    # o ZPL já diz onde cada coisa vai. Existe porque o desenho do Mercado Livre
    # vai quase até a beira, e a térmica falha nos primeiros e últimos décimos
    # de milímetro quando o rolo avança um pouco torto.
    margem_seguranca_mm: float = 0.0
    embutido: bool = False  # embutido não se apaga; pode ser duplicado e editado

    # -- medidas derivadas ------------------------------------------------- #

    @property
    def largura_total_mm(self) -> float:
        """Largura do papel: as colunas mais o espaço entre elas."""
        return (
            self.largura_mm * self.colunas
            + self.espaco_colunas_mm * (self.colunas - 1)
            + self.margem_mm * 2
        )

    @property
    def largura_pontos(self) -> int:
        return int(round(self.largura_mm * self.dpmm))

    @property
    def altura_pontos(self) -> int:
        return int(round(self.altura_mm * self.dpmm))

    @property
    def espaco_pontos(self) -> int:
        return int(round(self.espaco_colunas_mm * self.dpmm))

    @property
    def margem_pontos(self) -> int:
        return int(round(self.margem_mm * self.dpmm))

    @property
    def polegadas(self) -> tuple[float, float]:
        """Como o Labelary pede o tamanho — em polegadas."""
        return (round(self.largura_mm / 25.4, 4), round(self.altura_mm / 25.4, 4))

    @property
    def largura_desenho_mm(self) -> float:
        """A largura da tela onde o ZPL é desenhado.

        É aqui que os dois casos se separam: quando o ZPL já traz as colunas, a
        tela é a folha inteira; quando não traz, é uma etiqueta só.
        """
        return self.largura_total_mm if self.colunas_no_zpl else self.largura_mm

    @property
    def descricao(self) -> str:
        coluna = "1 coluna" if self.colunas == 1 else f"{self.colunas} colunas"
        if self.colunas_no_zpl:
            coluna += " no próprio ZPL"
        descricao = f"{self.largura_mm:g} × {self.altura_mm:g} mm · {coluna}"
        if self.margem_seguranca_mm:
            descricao += f" · margem de {self.margem_seguranca_mm:g} mm no PDF"
        return descricao

    def validar(self) -> None:
        if not self.nome.strip():
            raise FormatoInvalido("o formato precisa de um nome")
        if self.largura_mm <= 0 or self.altura_mm <= 0:
            raise FormatoInvalido("largura e altura têm de ser maiores que zero")
        if self.dpmm not in DPMM_VALIDOS:
            raise FormatoInvalido(
                f"densidade {self.dpmm} dpmm não existe; use uma de {DPMM_VALIDOS}"
            )
        if self.colunas < 1 or self.colunas > 2:
            raise FormatoInvalido("por enquanto só 1 ou 2 colunas")
        if self.espaco_colunas_mm < 0 or self.margem_mm < 0 or self.margem_seguranca_mm < 0:
            raise FormatoInvalido("espaço e margem não podem ser negativos")
        if self.margem_seguranca_mm > min(self.largura_mm, self.altura_mm) / 4:
            raise FormatoInvalido(
                f"margem de segurança de {self.margem_seguranca_mm:g} mm come mais da "
                "metade da etiqueta; use no máximo "
                f"{min(self.largura_mm, self.altura_mm) / 4:g} mm"
            )
        if self.colunas_no_zpl and self.colunas < 2:
            raise FormatoInvalido(
                "'o ZPL já traz as colunas' só faz sentido com mais de uma coluna"
            )
        if self.largura_total_mm > LARGURA_MAXIMA_MM + 0.01:
            raise FormatoInvalido(
                f"o papel ficaria com {self.largura_total_mm:.1f} mm de largura e a "
                f"impressora só alcança {LARGURA_MAXIMA_MM:g} mm"
            )

    def dicionario(self) -> dict:
        dados = asdict(self)
        dados["largura_total_mm"] = round(self.largura_total_mm, 2)
        dados["largura_desenho_mm"] = round(self.largura_desenho_mm, 2)
        dados["descricao"] = self.descricao
        return dados


# Os que já vêm prontos. O primeiro é o padrão da operação: a etiqueta do
# Mercado Livre é 100 × 150 mm, uma coluna.
EMBUTIDOS: tuple[Formato, ...] = (
    Formato("ml-100x150", "Mercado Livre 100 × 150 mm", 100, 150, embutido=True),
    # O mesmo rolo, para a etiqueta que o Mercado Livre agora entrega em PDF: a
    # etiqueta vem desenhada com ≈ 90 × 149 mm dentro de uma A4 e é encaixada
    # nos 100 × 150 sem distorcer. A margem afasta o desenho da beira — sem ela
    # a moldura encosta na borda de cima e de baixo do papel.
    Formato("ml-pdf-100x150", "Mercado Livre PDF · 100 × 150 mm", 100, 150,
            margem_seguranca_mm=1.5, embutido=True),
    # Os dois formatos abaixo têm a mesma medida de propósito: o rolo é o mesmo
    # (40 × 25 mm, duas colunas). O que muda é **quem monta o par** — e é só
    # isso que os nomes precisam dizer.
    #
    # O ZPL de duas vias do Mercado Livre já vem com as duas colunas dentro de
    # um ^XA (a segunda a partir de ^FO360), então a tela de desenho tem de ser
    # a folha inteira: 82 mm.
    Formato("ml-2col", "Mercado Livre · par já no ZPL · 40 × 25 mm", 40, 25,
            colunas=2, espaco_colunas_mm=2, colunas_no_zpl=True, embutido=True),
    Formato("duas-40x25", "Par montado aqui · 40 × 25 mm em 2 colunas", 40, 25,
            colunas=2, espaco_colunas_mm=2, embutido=True),
    Formato("etq-100x50", "100 × 50 mm", 100, 50, embutido=True),
    Formato("etq-60x40", "60 × 40 mm", 60, 40, embutido=True),
    Formato("etq-40x25", "40 × 25 mm (uma coluna)", 40, 25, embutido=True),
)
PADRAO = EMBUTIDOS[0].id
PADRAO_PDF = EMBUTIDOS[1].id


def dir_dados() -> Path:
    """Onde guardar o que é do usuário.

    Empacotado em ``.exe``, a pasta do programa é temporária e some — por isso o
    JSON vai para ``%LOCALAPPDATA%``. Rodando do repositório, fica em ``data/``,
    junto do resto.
    """
    if getattr(sys, "frozen", False):
        raiz = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Eleva Impressao"
    else:
        # parents[2] = raiz do repositório: este arquivo está em
        # src/eleva_impressao/. Era parents[3] quando o pacote ainda morava sob
        # src/eleva/impressao/ — subir um nível a mais agora escreveria fora do
        # projeto, na pasta que contém o repositório.
        raiz = Path(__file__).resolve().parents[2] / "data"
    raiz.mkdir(parents=True, exist_ok=True)
    return raiz


def caminho() -> Path:
    return dir_dados() / ARQUIVO


# --------------------------------------------------------------------------- #
# Leitura e escrita
# --------------------------------------------------------------------------- #


def _do_dicionario(dados: dict) -> Formato:
    return Formato(
        id=str(dados["id"]),
        nome=str(dados.get("nome", dados["id"])),
        largura_mm=float(dados.get("largura_mm", 100)),
        altura_mm=float(dados.get("altura_mm", 150)),
        dpmm=int(dados.get("dpmm", 8)),
        colunas=int(dados.get("colunas", 1)),
        espaco_colunas_mm=float(dados.get("espaco_colunas_mm", 2.0)),
        margem_mm=float(dados.get("margem_mm", 0.0)),
        colunas_no_zpl=bool(dados.get("colunas_no_zpl", False)),
        margem_seguranca_mm=float(dados.get("margem_seguranca_mm", 0.0)),
        embutido=bool(dados.get("embutido", False)),
    )


def carregar() -> list[Formato]:
    """Embutidos primeiro, depois os criados aqui. Arquivo corrompido não derruba."""
    arquivo = caminho()
    proprios: list[Formato] = []
    if arquivo.exists():
        try:
            bruto = json.loads(arquivo.read_text(encoding="utf-8-sig"))
            proprios = [_do_dicionario(d) for d in bruto.get("formatos", [])]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            # Preferimos abrir com os embutidos a não abrir.
            proprios = []

    conhecidos = {f.id: f for f in EMBUTIDOS}
    for formato in proprios:
        # Um embutido editado sobrescreve o de fábrica, mas continua embutido.
        formato.embutido = formato.id in conhecidos
        conhecidos[formato.id] = formato
    ordem = [f.id for f in EMBUTIDOS] + [f.id for f in proprios if f.id not in
                                         {e.id for e in EMBUTIDOS}]
    return [conhecidos[i] for i in dict.fromkeys(ordem)]


def gravar(formatos: list[Formato]) -> None:
    embutidos_de_fabrica = {f.id: f for f in EMBUTIDOS}

    def mudou(f: Formato) -> bool:
        original = embutidos_de_fabrica.get(f.id)
        return original is None or asdict(original) != asdict(f)

    # Só vai para o disco o que não é idêntico ao de fábrica: assim um embutido
    # melhorado numa versão futura chega para quem nunca o editou.
    guardar = [asdict(f) for f in formatos if mudou(f)]
    caminho().write_text(
        json.dumps({"formatos": guardar}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def buscar(formato_id: str) -> Formato:
    for formato in carregar():
        if formato.id == formato_id:
            return formato
    raise KeyError(f"formato {formato_id!r} não existe")


def formato_padrao() -> Formato:
    try:
        return buscar(PADRAO)
    except KeyError:  # pragma: no cover - só se apagarem o embutido à mão
        return EMBUTIDOS[0]


def _id_livre(nome: str, existentes: set[str]) -> str:
    base = "".join(c if c.isalnum() else "-" for c in nome.lower()).strip("-") or "formato"
    base = base[:40]
    candidato, n = base, 2
    while candidato in existentes:
        candidato, n = f"{base}-{n}", n + 1
    return candidato


def salvar(formato: Formato) -> Formato:
    """Cria ou atualiza. Sem ``id``, gera um a partir do nome."""
    formato.validar()
    formatos = carregar()
    existentes = {f.id for f in formatos}
    if not formato.id:
        formato = replace(formato, id=_id_livre(formato.nome, existentes))

    for i, atual in enumerate(formatos):
        if atual.id == formato.id:
            formatos[i] = replace(formato, embutido=atual.embutido)
            break
    else:
        formatos.append(replace(formato, embutido=False))
    gravar(formatos)
    return buscar(formato.id)


def duplicar(formato_id: str, novo_nome: str | None = None) -> Formato:
    """Copiar é como se edita um embutido sem perder o original."""
    original = buscar(formato_id)
    copia = replace(
        original, id="", nome=novo_nome or f"{original.nome} (cópia)", embutido=False
    )
    return salvar(copia)


def apagar(formato_id: str) -> None:
    formatos = carregar()
    alvo = next((f for f in formatos if f.id == formato_id), None)
    if alvo is None:
        raise KeyError(f"formato {formato_id!r} não existe")
    if alvo.embutido:
        raise FormatoInvalido(
            f"{alvo.nome} é um formato de fábrica e não se apaga — duplique e edite a cópia"
        )
    gravar([f for f in formatos if f.id != formato_id])


def restaurar_padroes() -> list[Formato]:
    """Volta tudo de fábrica. Só apaga o JSON, os embutidos são código."""
    arquivo = caminho()
    if arquivo.exists():
        arquivo.unlink()
    return carregar()


__all__ = [
    "DPMM_VALIDOS",
    "EMBUTIDOS",
    "LARGURA_MAXIMA_MM",
    "PADRAO",
    "PADRAO_PDF",
    "Formato",
    "FormatoInvalido",
    "apagar",
    "buscar",
    "caminho",
    "carregar",
    "dir_dados",
    "duplicar",
    "formato_padrao",
    "gravar",
    "restaurar_padroes",
    "salvar",
]
