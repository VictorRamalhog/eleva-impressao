"""Códigos de barras desenhados em casa, sem depender de serviço externo.

Cada gerador devolve uma lista de *elementos* — ``(largura_em_modulos, e_barra)``
— e não uma imagem. Quem desenha é o ``render``, que sabe a largura do módulo
(``^BY``) e a altura pedida. Assim o mesmo código serve para o preview na tela e
para o bitmap que vai para a impressora, na mesma geometria.

Unidade: *módulo* é o traço mais fino. Em ZPL, ``^BY2`` significa módulo de 2
pontos; num cabeçote de 203 dpi (8 pontos/mm), 2 pontos = 0,25 mm.
"""

from __future__ import annotations

from dataclasses import dataclass

Elemento = tuple[float, bool]  # (largura em módulos, é barra?)


class CodigoInvalido(ValueError):
    """Dado que não cabe na simbologia pedida (letra em EAN, tamanho errado…)."""


@dataclass(slots=True)
class Simbolo:
    elementos: list[Elemento]
    texto_legivel: str

    @property
    def modulos(self) -> float:
        return sum(largura for largura, _ in self.elementos)


# --------------------------------------------------------------------------- #
# Code 128 — o que o Mercado Livre usa no número do pedido
# --------------------------------------------------------------------------- #

# 107 padrões (0–102 = dados, 103–105 = início A/B/C, 106 = parada). Cada dígito
# é a largura de um elemento, alternando barra/espaço a partir de barra.
_C128 = (  # noqa: SIM905 - em texto a tabela se confere linha a linha; em lista, não
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()

INICIO_A, INICIO_B, INICIO_C, PARADA = 103, 104, 105, 106
_TROCA_A, _TROCA_B, _TROCA_C = 101, 100, 99


def _elementos_de(valores: list[int]) -> list[Elemento]:
    elementos: list[Elemento] = []
    for valor in valores:
        for i, larg in enumerate(_C128[valor]):
            elementos.append((float(larg), i % 2 == 0))
    return elementos


def _valor_b(c: str) -> int:
    """Subset B cobre ASCII 32–127 nos valores 0–95."""
    codigo = ord(c)
    if not 32 <= codigo <= 127:
        raise CodigoInvalido(f"caractere {c!r} não cabe no Code 128 subset B")
    return codigo - 32


def _valor_a(c: str) -> int:
    """Subset A cobre ASCII 32–95 (valores 0–63) e os de controle 0–31 (64–95)."""
    codigo = ord(c)
    if 32 <= codigo <= 95:
        return codigo - 32
    if codigo <= 31:
        return codigo + 64
    raise CodigoInvalido(f"caractere {c!r} não cabe no Code 128 subset A")


def _corrida_de_digitos(dados: str, i: int) -> int:
    n = 0
    while i + n < len(dados) and dados[i + n].isdigit():
        n += 1
    return n


def code128(dados: str, *, subset: str = "auto") -> Simbolo:
    """Codifica em Code 128 com troca automática de subset.

    A regra de troca é a canônica: entra no subset C quando há 4 dígitos no
    começo/fim ou 6 no meio — é o que encolhe a etiqueta sem perder nada. Com
    ``subset`` fixo ('A', 'B' ou 'C') a escolha do chamador é respeitada.
    """
    if not dados:
        raise CodigoInvalido("código de barras sem dados")

    escolhido = subset.upper()
    if escolhido == "C":
        if not dados.isdigit() or len(dados) % 2:
            raise CodigoInvalido("subset C exige uma quantidade par de dígitos")
        return _fechar([INICIO_C] + [int(dados[i:i + 2]) for i in range(0, len(dados), 2)], dados)
    if escolhido == "A":
        return _fechar([INICIO_A] + [_valor_a(c) for c in dados], dados)
    if escolhido == "B":
        return _fechar([INICIO_B] + [_valor_b(c) for c in dados], dados)

    # auto: começa no subset que já compensa e troca no meio do caminho.
    corrida = _corrida_de_digitos(dados, 0)
    if corrida >= 4 and corrida % 2 == 0:
        atual, valores = "C", [INICIO_C]
    else:
        atual, valores = "B", [INICIO_B]

    i = 0
    while i < len(dados):
        if atual == "C":
            corrida = _corrida_de_digitos(dados, i)
            if corrida >= 2:
                for _ in range(corrida // 2):
                    valores.append(int(dados[i:i + 2]))
                    i += 2
                continue
            valores.append(_TROCA_B)
            atual = "B"
            continue

        corrida = _corrida_de_digitos(dados, i)
        # No fim da cadeia, 4 dígitos já pagam a troca; no meio, só a partir de 6.
        limite = 4 if i + corrida >= len(dados) else 6
        if corrida >= limite and corrida % 2 == 0:
            valores.append(_TROCA_C)
            atual = "C"
            continue
        valores.append(_valor_b(dados[i]))
        i += 1

    return _fechar(valores, dados)


def _fechar(valores: list[int], legivel: str) -> Simbolo:
    soma = valores[0] + sum(v * i for i, v in enumerate(valores[1:], start=1))
    valores = [*valores, soma % 103, PARADA]
    return Simbolo(_elementos_de(valores), legivel)


# --------------------------------------------------------------------------- #
# Code 39
# --------------------------------------------------------------------------- #

_C39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw",
    "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn", "F": "nnwnwwnnn",
    "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww",
    "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn",
    "S": "nnwnnnwwn", "T": "nnnnwnwwn", "U": "wwnnnnnnw", "V": "nwwnnnnnw",
    "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn", "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnnwn", "$": "nwnwnwnnn",
    "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn", "*": "nwnnwnwnn",
}
_C39_ALFABETO = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"


def code39(dados: str, *, razao: float = 3.0, verificador: bool = False) -> Simbolo:
    """Code 39. ``razao`` é a relação largo/estreito do ``^BY`` (2,0 a 3,0)."""
    texto = (dados or "").upper()
    for c in texto:
        if c not in _C39 or c == "*":
            raise CodigoInvalido(f"caractere {c!r} não existe no Code 39")
    if verificador:
        soma = sum(_C39_ALFABETO.index(c) for c in texto)
        texto += _C39_ALFABETO[soma % 43]

    # A legenda do Code 39 sai com os asteriscos de inicio/fim, como na Zebra.
    elementos: list[Elemento] = []
    for pos, c in enumerate(f"*{texto}*"):
        if pos:
            elementos.append((1.0, False))  # espaço entre caracteres
        for i, larg in enumerate(_C39[c]):
            elementos.append((razao if larg == "w" else 1.0, i % 2 == 0))
    return Simbolo(elementos, f"*{texto}*")


# --------------------------------------------------------------------------- #
# Intercalado 2 de 5
# --------------------------------------------------------------------------- #

_I25 = {
    "0": "nnwwn", "1": "wnnnw", "2": "nwnnw", "3": "wwnnn", "4": "nnwnw",
    "5": "wnwnn", "6": "nwwnn", "7": "nnnww", "8": "wnnwn", "9": "nwnwn",
}


def intercalado25(dados: str, *, razao: float = 3.0, verificador: bool = False) -> Simbolo:
    texto = dados or ""
    if not texto.isdigit():
        raise CodigoInvalido("intercalado 2 de 5 só aceita dígitos")
    if verificador:
        pesos = [3 if (len(texto) - i) % 2 else 1 for i in range(len(texto))]
        soma = sum(int(d) * p for d, p in zip(texto, pesos, strict=True))
        texto += str((10 - soma % 10) % 10)
    if len(texto) % 2:
        texto = "0" + texto  # o intercalado casa os dígitos aos pares

    elementos: list[Elemento] = [(1.0, True), (1.0, False), (1.0, True), (1.0, False)]
    for i in range(0, len(texto), 2):
        for b, e in zip(_I25[texto[i]], _I25[texto[i + 1]], strict=True):
            elementos.append((razao if b == "w" else 1.0, True))
            elementos.append((razao if e == "w" else 1.0, False))
    elementos += [(razao, True), (1.0, False), (1.0, True)]
    return Simbolo(elementos, texto)


# --------------------------------------------------------------------------- #
# EAN-13 / EAN-8 / UPC-A
# --------------------------------------------------------------------------- #

_EAN_L = ("0001101", "0011001", "0010011", "0111101", "0100011",
          "0110001", "0101111", "0111011", "0110111", "0001011")
_EAN_G = tuple("".join("1" if c == "0" else "0" for c in reversed(p)) for p in _EAN_L)
_EAN_R = tuple("".join("1" if c == "0" else "0" for c in p) for p in _EAN_L)
_EAN_PARIDADE = ("LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
                 "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL")


def _digito_ean(texto: str) -> str:
    soma = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(texto)))
    return str((10 - soma % 10) % 10)


def _bits_para_elementos(bits: str) -> list[Elemento]:
    return [(1.0, bit == "1") for bit in bits]


def ean13(dados: str) -> Simbolo:
    texto = (dados or "").strip()
    if not texto.isdigit():
        raise CodigoInvalido("EAN-13 só aceita dígitos")
    if len(texto) == 12:
        texto += _digito_ean(texto)
    if len(texto) != 13:
        raise CodigoInvalido(f"EAN-13 precisa de 12 ou 13 dígitos, vieram {len(texto)}")

    paridade = _EAN_PARIDADE[int(texto[0])]
    bits = "101"
    for i, d in enumerate(texto[1:7]):
        bits += (_EAN_L if paridade[i] == "L" else _EAN_G)[int(d)]
    bits += "01010"
    for d in texto[7:]:
        bits += _EAN_R[int(d)]
    bits += "101"
    return Simbolo(_bits_para_elementos(bits), texto)


def ean8(dados: str) -> Simbolo:
    texto = (dados or "").strip()
    if not texto.isdigit():
        raise CodigoInvalido("EAN-8 só aceita dígitos")
    if len(texto) == 7:
        texto += _digito_ean(texto)
    if len(texto) != 8:
        raise CodigoInvalido(f"EAN-8 precisa de 7 ou 8 dígitos, vieram {len(texto)}")
    bits = "101" + "".join(_EAN_L[int(d)] for d in texto[:4])
    bits += "01010" + "".join(_EAN_R[int(d)] for d in texto[4:]) + "101"
    return Simbolo(_bits_para_elementos(bits), texto)


def upca(dados: str) -> Simbolo:
    texto = (dados or "").strip()
    if not texto.isdigit():
        raise CodigoInvalido("UPC-A só aceita dígitos")
    if len(texto) == 11:
        texto += _digito_ean("0" + texto)
    if len(texto) != 12:
        raise CodigoInvalido(f"UPC-A precisa de 11 ou 12 dígitos, vieram {len(texto)}")
    return Simbolo(ean13("0" + texto).elementos, texto)


# --------------------------------------------------------------------------- #
# QR Code (2D — devolve matriz, não elementos)
# --------------------------------------------------------------------------- #


def qrcode(dados: str, *, correcao: str = "M") -> list[list[bool]]:
    """Matriz booleana do QR. ``correcao`` é H/Q/M/L, como no ``^BQ``."""
    import segno

    nivel = {"H": "h", "Q": "q", "M": "m", "L": "l"}.get((correcao or "M").upper(), "m")
    qr = segno.make(dados or " ", error=nivel, micro=False)
    return [[bool(c) for c in linha] for linha in qr.matrix]


__all__ = [
    "CodigoInvalido",
    "Elemento",
    "Simbolo",
    "code128",
    "code39",
    "ean8",
    "ean13",
    "intercalado25",
    "qrcode",
    "upca",
]
