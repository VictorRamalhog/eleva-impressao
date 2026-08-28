"""Ajuste automático de posição: encaixa a etiqueta sem reescrever o conteúdo.

O problema que isto resolve é o do dia a dia, não o do ZPL bonito: o nome de um
produto é maior que o do outro, então às vezes ele passa por cima do SKU, às
vezes o código de barras estoura a largura da etiqueta, e a operadora acaba
mexendo em ``^FO`` na mão, etiqueta por etiqueta.

**A regra que não se negocia:** nome, SKU e código de barras são dados do
produto — o ajustador **nunca** toca em ``^FD``. Ele só reescreve parâmetros
numéricos de ``^FO``/``^FT`` (posição), ``^A`` (corpo da fonte), ``^FB`` (caixa
do texto), ``^BY`` (largura do módulo) e a altura do código de barras. Há teste
comparando os ``^FD`` antes e depois, um a um.

Por que dá para acertar em vez de tentar: o motor de desenho já mede o texto com
a métrica real da fonte (``render.largura_do_texto``). O ajustador usa a **mesma
medida** que o desenho vai usar, então "cabe em duas linhas" aqui significa cabe
em duas linhas no papel.

O que ele faz, por coluna:

1. separa os campos em linhas, na ordem em que já estavam (nada troca de lugar);
2. quebra o texto na largura útil e descobre de quantas linhas ele precisa;
3. encolhe o corpo da fonte e a altura das barras, junto, até o conjunto caber;
4. estreita o módulo do código de barras se ele passar da largura;
5. distribui a sobra em espaços iguais e centraliza tudo na coluna.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from eleva_impressao import barras, render
from eleva_impressao.formatos import Formato

# Quanto o conteúdo se afasta da borda da etiqueta. As etiquetas têm canto
# arredondado e o corte nunca é perfeito; 1,5 mm é o que evita texto na dobra.
MARGEM_MM = 1.5
# Espaço mínimo entre um campo e o seguinte, para nada encostar.
ESPACO_MINIMO_MM = 0.5
# Até onde vale encolher para fazer caber. Abaixo disto, é melhor avisar do que
# imprimir algo que ninguém lê no balcão.
ESCALA_MINIMA = 0.5
ALTURA_MINIMA_FONTE = 10


class NaoAjustavel(ValueError):
    """ZPL sem nada que dê para posicionar."""


@dataclass(slots=True)
class Peca:
    """Um campo do ZPL, do ``^FO`` até o ``^FS``."""

    fo: render.Comando
    fonte: render.Comando | None = None
    bloco: render.Comando | None = None
    barra: render.Comando | None = None
    by: render.Comando | None = None
    by_governante: render.Comando | None = None  # ^BY de fora do campo
    dados: str = ""
    hexadecimal: bool = False
    lh: tuple[int, int] = (0, 0)
    x: int = 0
    y: int = 0
    designador: str = "0"
    altura_fonte: int = 30
    largura_fonte: int = 0
    tipo: str = "texto"  # 'texto' | 'barras' | 'grafico' | 'vazio'

    # medidas calculadas durante o ajuste
    largura_calculada: int = 0
    altura_calculada: int = 0
    modulo_calculado: int = 0
    altura_barras_calculada: int = 0
    linhas_calculadas: int = 1
    altura_fonte_calculada: int = 0
    barras_estouram: bool = False
    # Altura que o campo ocupava no desenho original. É o que define quem estava
    # lado a lado com quem — e por isso não pode ser recalculada.
    altura_original: int = 0


@dataclass(slots=True)
class Resultado:
    zpl: str
    mudou: bool
    mudancas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #

_FONTE = re.compile(r"^A[0-9A-Z@]$")
_BARRAS = {"BC": "CODE128", "B3": "CODE39", "B2": "I25", "BE": "EAN13",
           "B8": "EAN8", "BU": "UPCA", "BQ": "QR"}


def _pecas_da_etiqueta(zpl: str) -> list[Peca]:
    """Agrupa os comandos em campos, guardando onde cada um está no texto."""
    pecas: list[Peca] = []
    lh = (0, 0)
    by_global: render.Comando | None = None
    atual: Peca | None = None

    for comando in render.analisar(zpl):
        nome = comando.nome
        if nome == "LH":
            lh = (comando.numero(0, 0), comando.numero(1, 0))
            continue
        if nome == "BY" and atual is None:
            by_global = comando
            continue
        if nome in ("FO", "FT"):
            atual = Peca(fo=comando, lh=lh, by_governante=by_global,
                         x=comando.numero(0, 0), y=comando.numero(1, 0))
            continue
        if atual is None:
            continue

        if _FONTE.match(nome):
            atual.fonte = comando
            atual.designador = nome[1]
            partes = comando.lista(3)
            atual.altura_fonte = _inteiro(partes[1], 30)
            atual.largura_fonte = _inteiro(partes[2], 0)
        elif nome == "FB":
            atual.bloco = comando
        elif nome == "BY":
            atual.by = comando
        elif nome in _BARRAS:
            atual.barra = comando
            atual.tipo = "barras"
        elif nome == "GF":
            atual.tipo = "grafico"
        elif nome == "FH":
            atual.hexadecimal = True
        elif nome in ("FD", "FV"):
            atual.dados = comando.params
        elif nome == "FS":
            if atual.tipo == "texto" and not render.texto_do_campo(
                atual.dados, atual.hexadecimal
            ).strip():
                atual.tipo = "vazio"
            atual.altura_original = _altura_original(atual)
            pecas.append(atual)
            atual = None
    return pecas


def _altura_original(peca: Peca) -> int:
    """Quanto o campo ocupava na vertical antes de qualquer ajuste."""
    if peca.tipo == "barras" and peca.barra is not None:
        altura = _inteiro(peca.barra.lista(2)[1], 50)
        legivel = (peca.barra.lista(3)[2] or "Y").upper() != "N"
        return altura + (render.FOLGA_LEGENDA + 18 if legivel else 0)
    if peca.tipo == "grafico":
        return peca.altura_fonte
    if peca.bloco is not None:
        linhas = max(1, _inteiro(peca.bloco.lista(2)[1], 1))
        espaco = _inteiro(peca.bloco.lista(3)[2], 0)
        return linhas * peca.altura_fonte + (linhas - 1) * espaco
    return peca.altura_fonte


def _inteiro(texto: str, padrao: int) -> int:
    limpo = re.sub(r"[^\d-]", "", texto or "")
    try:
        return int(limpo)
    except ValueError:
        return padrao


# --------------------------------------------------------------------------- #
# Medidas
# --------------------------------------------------------------------------- #


def _texto(peca: Peca) -> str:
    return render.texto_do_campo(peca.dados, peca.hexadecimal)


def _simbolo(peca: Peca) -> barras.Simbolo | None:
    """O código de barras já codificado, para saber quantos módulos ele ocupa."""
    tipo = _BARRAS.get(peca.barra.nome if peca.barra else "", "")
    dados = _texto(peca)
    try:
        if tipo == "CODE128":
            modo = (peca.barra.lista(6)[5] or "").upper()[:1]
            return barras.code128(dados, subset="auto" if modo == "A" else "B")
        if tipo == "CODE39":
            return barras.code39(dados)
        if tipo == "I25":
            return barras.intercalado25(dados)
        if tipo == "EAN13":
            return barras.ean13(dados)
        if tipo == "EAN8":
            return barras.ean8(dados)
        if tipo == "UPCA":
            return barras.upca(dados)
    except barras.CodigoInvalido:
        return None
    return None


def _modulo_atual(peca: Peca) -> int:
    fonte = peca.by or peca.by_governante
    return max(1, _inteiro(fonte.lista(1)[0], 2) if fonte else 2)


def _medir(peca: Peca, util: int, escala: float) -> None:
    """Preenche as medidas da peça para uma dada escala de redução."""
    if peca.tipo == "vazio":
        peca.largura_calculada = peca.altura_calculada = 0
        return

    if peca.tipo == "barras":
        simbolo = _simbolo(peca)
        altura = max(8, int(round(_inteiro(peca.barra.lista(2)[1], 50) * escala)))
        peca.altura_barras_calculada = altura
        if simbolo is None:
            peca.largura_calculada, peca.altura_calculada = util, altura
            return
        modulo = _modulo_atual(peca)
        # Estreitar o módulo é o que faz um código largo caber sem mexer no dado.
        # Só que o módulo tem um piso: 1 ponto. Se nem assim couber, o dado é
        # comprido demais para a etiqueta e isso vira aviso, não silêncio — um
        # código de barras espremido além da conta não é lido pelo coletor.
        cabe = int(util // simbolo.modulos)
        peca.modulo_calculado = max(1, min(modulo, cabe))
        peca.barras_estouram = simbolo.modulos > util
        peca.largura_calculada = int(round(simbolo.modulos * peca.modulo_calculado))
        legenda = (peca.barra.lista(3)[2] or "Y").upper() != "N"
        extra = (render.FOLGA_LEGENDA + 9 * peca.modulo_calculado) if legenda else 0
        peca.altura_calculada = altura + extra
        return

    if peca.tipo == "grafico":
        # Imagem embutida não se redimensiona por parâmetro; fica como está.
        peca.largura_calculada = min(util, util)
        peca.altura_calculada = 0
        return

    corpo = max(ALTURA_MINIMA_FONTE, int(round(peca.altura_fonte * escala)))
    peca.altura_fonte_calculada = corpo
    texto = _texto(peca)
    # ``^A0N,h,w`` com w diferente de h estica o glifo. Ignorar isso foi o que
    # fez a primeira versão "caber" na conta e estourar no papel: um ^A0N,20,25
    # ocupa 25% a mais do que a medida proporcional.
    estica = _estiramento(peca)

    if peca.bloco is not None:
        # A caixa está em pontos da etiqueta; a quebra tem de ser medida no
        # espaço do glifo esticado, senão a conta de linhas sai errada.
        linhas = render.linhas_do_texto(
            texto, max(1, int(util / estica)), peca.designador, corpo
        )
        peca.linhas_calculadas = max(1, len(linhas))
        espaco = _inteiro(peca.bloco.lista(3)[2], 0)
        peca.largura_calculada = util
        peca.altura_calculada = (
            peca.linhas_calculadas * corpo + (peca.linhas_calculadas - 1) * espaco
        )
        return

    def largura_em(altura: int) -> int:
        return int(round(render.largura_do_texto(texto, peca.designador, altura) * estica))

    largura = largura_em(corpo)
    # Texto solto que não cabe encolhe sozinho até caber — sem virar bloco, que
    # mudaria a estrutura do ZPL.
    while largura > util and corpo > ALTURA_MINIMA_FONTE:
        corpo -= 1
        largura = largura_em(corpo)
    peca.altura_fonte_calculada = corpo
    peca.linhas_calculadas = 1
    peca.largura_calculada = largura
    peca.altura_calculada = corpo


def _estiramento(peca: Peca) -> float:
    """Quanto o ``w`` do ``^A`` alarga o glifo em relação à altura (1 = normal)."""
    if not peca.largura_fonte or not peca.altura_fonte:
        return 1.0
    return peca.largura_fonte / peca.altura_fonte


# --------------------------------------------------------------------------- #
# Disposição
# --------------------------------------------------------------------------- #


def _linhas_de_pecas(pecas: list[Peca]) -> list[list[Peca]]:
    """Agrupa em faixas o que já estava lado a lado **no desenho original**.

    Serve para preservar a intenção de quem escreveu o ZPL: dois campos que
    dividiam a mesma altura (um preço à esquerda, um peso à direita) continuam
    lado a lado em vez de virarem duas linhas empilhadas.

    O agrupamento olha a geometria **original**, nunca a recalculada. Usar a
    recalculada foi um erro caro: quando o nome do produto passava de 2 para 3
    linhas, ele encostava no SKU, os dois viravam uma faixa só, e o ajuste
    espalhava um ao lado do outro — jogando o SKU para fora da etiqueta. O
    tamanho novo é consequência do ajuste; não pode ser entrada dele.
    """
    ordenadas = sorted(pecas, key=lambda p: (p.y, p.x))
    faixas: list[list[Peca]] = []
    for peca in ordenadas:
        topo, fundo = peca.y, peca.y + peca.altura_original
        if faixas:
            grupo = faixas[-1]
            g_topo = min(p.y for p in grupo)
            g_fundo = max(p.y + p.altura_original for p in grupo)
            sobreposicao = min(fundo, g_fundo) - max(topo, g_topo)
            menor = max(1, min(fundo - topo, g_fundo - g_topo))
            if sobreposicao > menor * 0.4:
                grupo.append(peca)
                continue
        faixas.append([peca])
    return faixas


def _dispor(
    pecas: list[Peca], coluna_x: int, largura: int, altura: int, margem: int, espaco: int
) -> tuple[list[tuple[Peca, int, int]], float, bool]:
    """Escolhe a escala que faz tudo caber e devolve as posições finais."""
    uteis = [p for p in pecas if p.tipo != "vazio"]
    if not uteis:
        return [], 1.0, True

    util_largura = max(8, largura - 2 * margem)
    util_altura = max(8, altura - 2 * margem)

    escala = 1.0
    while True:
        for peca in uteis:
            _medir(peca, util_largura, escala)
        faixas = _linhas_de_pecas(uteis)
        alturas = [max(p.altura_calculada for p in faixa) for faixa in faixas]
        total = sum(alturas) + espaco * max(0, len(faixas) - 1)
        if total <= util_altura or escala <= ESCALA_MINIMA:
            break
        escala = round(escala - 0.02, 3)

    coube = total <= util_altura
    sobra = max(0, util_altura - total)
    folga = sobra // (len(faixas) + 1)

    posicoes: list[tuple[Peca, int, int]] = []
    y = margem + folga
    for faixa, altura_faixa in zip(faixas, alturas, strict=True):
        largura_faixa = sum(p.largura_calculada for p in faixa)
        vao = (util_largura - largura_faixa) // max(1, len(faixa))
        x = coluna_x + margem + max(0, vao // 2)
        for peca in sorted(faixa, key=lambda p: p.x):
            posicoes.append((peca, x, y))
            x += peca.largura_calculada + max(0, vao)
        y += altura_faixa + espaco + folga
    return posicoes, escala, coube


# --------------------------------------------------------------------------- #
# Reescrita
# --------------------------------------------------------------------------- #


def _trocar(zpl: str, comando: render.Comando, params: str) -> tuple[int, int, str]:
    """Um pedaço a substituir: (início dos parâmetros, fim, texto novo)."""
    inicio = comando.posicao + 1 + len(comando.nome)
    return (inicio, comando.fim, params)


def _aplicar(zpl: str, trocas: list[tuple[int, int, str]]) -> str:
    partes = []
    fim_anterior = 0
    for inicio, fim, texto in sorted(trocas):
        partes.append(zpl[fim_anterior:inicio])
        partes.append(texto)
        fim_anterior = fim
    partes.append(zpl[fim_anterior:])
    return "".join(partes)


def _ajustar_etiqueta(
    zpl: str, formato: Formato, mudancas: list[str], avisos: list[str]
) -> str:
    dpmm = formato.dpmm
    margem = max(4, int(round(MARGEM_MM * dpmm)))
    espaco = max(2, int(round(ESPACO_MINIMO_MM * dpmm)))
    colunas = formato.colunas if formato.colunas_no_zpl else 1
    largura_coluna = formato.largura_pontos if colunas > 1 else int(
        round(formato.largura_desenho_mm * dpmm)
    )
    passo = largura_coluna + formato.espaco_pontos

    pecas = _pecas_da_etiqueta(zpl)
    if not pecas:
        raise NaoAjustavel("não encontrei campos para posicionar")

    trocas: list[tuple[int, int, str]] = []
    for indice in range(colunas):
        inicio_mm = indice * passo
        do_grupo = [
            p for p in pecas
            if inicio_mm <= p.x + p.lh[0] < inicio_mm + passo or (
                colunas == 1 or indice == colunas - 1
            ) and p.x + p.lh[0] >= inicio_mm
        ]
        if not do_grupo:
            continue

        posicoes, escala, coube = _dispor(
            do_grupo, inicio_mm, largura_coluna, formato.altura_pontos, margem, espaco
        )
        if not coube:
            avisos.append(
                f"coluna {indice + 1}: mesmo reduzindo ao mínimo o conteúdo não coube "
                f"em {formato.largura_mm:g} × {formato.altura_mm:g} mm"
            )
        for peca, _, _ in posicoes:
            if peca.barras_estouram:
                avisos.append(
                    f"coluna {indice + 1}: o código de barras é mais largo que a "
                    "etiqueta mesmo no módulo mínimo — encurtar o dado é a única "
                    "saída, e isso só você pode decidir"
                )
        if escala < 1.0:
            mudancas.append(
                f"coluna {indice + 1}: tudo reduzido a {escala:.0%} para caber na altura"
            )

        movidos = 0
        for peca, x, y in posicoes:
            antes = (peca.x, peca.y)
            novas = _trocas_da_peca(peca, x, y, mudancas, indice + 1)
            if antes != (max(0, x - peca.lh[0]), max(0, y - peca.lh[1])):
                movidos += 1
            trocas.extend(novas)
        if movidos:
            mudancas.append(f"coluna {indice + 1}: {movidos} campo(s) reposicionado(s)")

    return _aplicar(zpl, trocas)


def _trocas_da_peca(
    peca: Peca, x: int, y: int, mudancas: list[str], coluna: int
) -> list[tuple[int, int, str]]:
    trocas: list[tuple[int, int, str]] = []

    # ^FO/^FT: posição, descontando o ^LH que já desloca o campo.
    novo_x = max(0, x - peca.lh[0])
    novo_y = max(0, y - peca.lh[1])
    resto = peca.fo.lista(3)[2]
    params = f"{novo_x},{novo_y}" + (f",{resto}" if resto else "")
    if (peca.x, peca.y) != (novo_x, novo_y):
        trocas.append(_trocar("", peca.fo, params))

    # ^A: corpo da fonte.
    if peca.fonte is not None and peca.altura_fonte_calculada:
        corpo = peca.altura_fonte_calculada
        if corpo != peca.altura_fonte:
            orientacao = (peca.fonte.lista(3)[0] or "N")
            largura = (
                round(peca.largura_fonte * corpo / peca.altura_fonte)
                if peca.largura_fonte and peca.altura_fonte else peca.largura_fonte
            )
            trocas.append(_trocar("", peca.fonte, f"{orientacao},{corpo},{largura}"))
            mudancas.append(
                f"coluna {coluna}: fonte de {peca.altura_fonte} para {corpo} pontos"
            )

    # ^FB: caixa do texto e número de linhas de que ele realmente precisa.
    if peca.bloco is not None:
        atual = peca.bloco.lista(5)
        linhas_antes = _inteiro(atual[1], 1)
        novo = ",".join([
            str(peca.largura_calculada),
            str(peca.linhas_calculadas),
            atual[2] or "0",
            atual[3] or "L",
            atual[4] or "0",
        ])
        trocas.append(_trocar("", peca.bloco, novo))
        if linhas_antes != peca.linhas_calculadas:
            mudancas.append(
                f"coluna {coluna}: o texto passou a caber em "
                f"{peca.linhas_calculadas} linha(s) — antes o ^FB dizia {linhas_antes}"
            )

    # ^BY e altura das barras.
    if peca.tipo == "barras" and peca.barra is not None:
        if peca.modulo_calculado and peca.by is not None:
            atual = peca.by.lista(3)
            anterior = _inteiro(atual[0], 2)
            if anterior != peca.modulo_calculado:
                trocas.append(_trocar("", peca.by, ",".join(
                    [str(peca.modulo_calculado), atual[1], atual[2]]
                ).rstrip(",")))
                mudancas.append(
                    f"coluna {coluna}: barras estreitadas do módulo {anterior} "
                    f"para {peca.modulo_calculado} para caber na largura"
                )
        partes = peca.barra.lista(6)
        altura_antes = _inteiro(partes[1], 0)
        if peca.altura_barras_calculada and altura_antes != peca.altura_barras_calculada:
            partes[1] = str(peca.altura_barras_calculada)
            trocas.append(_trocar("", peca.barra, ",".join(p for p in partes if p != "")))
            mudancas.append(
                f"coluna {coluna}: altura das barras de {altura_antes} "
                f"para {peca.altura_barras_calculada} pontos"
            )
    return trocas


# --------------------------------------------------------------------------- #
# Porta de entrada
# --------------------------------------------------------------------------- #


def ajustar(zpl: str, formato: Formato) -> Resultado:
    """Reposiciona tudo dentro da etiqueta, sem tocar em nenhum ``^FD``."""
    if not (zpl or "").strip():
        raise NaoAjustavel("não há ZPL para ajustar")

    mudancas: list[str] = []
    avisos: list[str] = []
    saida: list[str] = []
    for etiqueta in render.separar_etiquetas(zpl):
        saida.append(_ajustar_etiqueta(etiqueta, formato, mudancas, avisos))
    novo = "\n".join(saida)

    if _dados_de(novo) != _dados_de(zpl):  # pragma: no cover - trava de segurança
        raise NaoAjustavel(
            "o ajuste teria mudado o conteúdo da etiqueta; nada foi alterado"
        )
    return Resultado(zpl=novo, mudou=novo != zpl, mudancas=mudancas, avisos=avisos)


def _dados_de(zpl: str) -> list[str]:
    """Todos os ``^FD`` do ZPL, em ordem — a prova de que nada foi reescrito."""
    return [c.params for c in render.analisar(zpl) if c.nome in ("FD", "FV")]


__all__ = ["ESCALA_MINIMA", "MARGEM_MM", "NaoAjustavel", "Peca", "Resultado", "ajustar"]
