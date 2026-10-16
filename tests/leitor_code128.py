"""Um leitor de Code 128 mínimo — o "coletor" dos testes.

Existe para responder a única pergunta que importa sobre um código de barras
impresso: **ele ainda lê?** Comparar pixels não responde isso; uma barra um
ponto mais larga pode ser irrelevante ou fatal. Então os testes leem o bitmap
que vai para a impressora como um leitor leria: largura relativa de cada
barra, caractere por caractere, e o dígito verificador no fim.

Normaliza cada caractere pela própria largura (11 módulos), como os leitores
de verdade fazem — por isso aguenta o módulo fracionário (4,3 pontos) que
sai quando um PDF é encaixado na etiqueta.
"""

from __future__ import annotations

from eleva_impressao.barras import _C128

_VALOR = {padrao: valor for valor, padrao in enumerate(_C128)}


def corridas(linha: list[int]) -> list[int]:
    """Larguras alternadas barra/espaço, da primeira barra até a última."""
    pretos = [i for i, v in enumerate(linha) if v < 128]
    if not pretos:
        return []
    trecho = linha[pretos[0]:pretos[-1] + 1]
    saida, atual, n = [], True, 0
    for v in trecho:
        if (v < 128) == atual:
            n += 1
        else:
            saida.append(n)
            atual, n = v < 128, 1
    saida.append(n)
    return saida


def _padrao(grupo: list[int], modulos: int) -> str:
    total = sum(grupo)
    larguras = [max(1, min(4, round(r * modulos / total))) for r in grupo]
    return "".join(str(x) for x in larguras)


def ler(linha: list[int]) -> str | None:
    """Decodifica uma linha de pixels (0 = preto). ``None`` se não ler."""
    r = corridas(linha)
    if len(r) < 6 + 6 + 7 or (len(r) - 7) % 6:
        return None
    valores = []
    for i in range(0, len(r) - 7, 6):
        valor = _VALOR.get(_padrao(r[i:i + 6], 11))
        if valor is None:
            return None
        valores.append(valor)
    if _padrao(r[-7:], 13) != _C128[106]:
        return None

    inicio, *dados, verificador = valores
    if inicio not in (103, 104, 105):
        return None
    soma = inicio + sum((i + 1) * v for i, v in enumerate(dados))
    if soma % 103 != verificador:
        return None

    subset = {103: "A", 104: "B", 105: "C"}[inicio]
    texto = []
    for v in dados:
        if v == 99:
            subset = "C"
        elif v == 100:
            subset = "B"
        elif v == 101:
            subset = "A"
        elif subset == "C":
            texto.append(f"{v:02d}")
        elif subset == "B":
            texto.append(chr(v + 32))
        else:
            texto.append(chr(v + 32) if v < 64 else chr(v - 64))
    return "".join(texto)
