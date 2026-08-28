"""Motor ZPL → bitmap. O preview e o papel saem daqui, da mesma função.

Por que renderizar em casa em vez de só chamar o Labelary: o que a operadora vê
na tela **precisa** ser o que sai na etiqueta. Se o preview vem de um serviço e
a impressão vem de outro caminho, os dois divergem no dia em que mais importa.
Aqui a imagem do preview e o bitmap enviado à impressora são o mesmo objeto.

De brinde: funciona sem internet e sem limite de requisições.

Unidades: tudo em **pontos** (dots). ``dpmm`` converte para milímetro — 8 dpmm
(203 dpi) é o padrão das térmicas de etiqueta, inclusive a KP-IM604.

Cobertura: os comandos que aparecem em etiqueta de verdade (a do Mercado Livre
inclusive). O que não é reconhecido não quebra a renderização — vira um aviso na
lista de ``avisos``, para a operadora saber que aquele pedaço não foi desenhado.
"""

from __future__ import annotations

import base64
import binascii
import re
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from eleva_impressao import barras

DPMM_PADRAO = 8
BRANCO = 255
PRETO = 0

# Fonte escalável ``^A0``: a Zebra usa CG Triumvirate Bold Condensed. Arial
# Narrow Bold é a que existe no Windows com a mesma proporção condensada — a
# diferença de largura por caractere fica abaixo de um ponto na 203 dpi.
_FONTES_ESCALAVEIS = ("ARIALNB.TTF", "arialbd.ttf", "DejaVuSans-Bold.ttf")
# Fontes de bitmap ``^AA``…``^AH``: monoespaçadas na impressora.
_FONTES_FIXAS = ("consolab.ttf", "cour.ttf", "DejaVuSansMono-Bold.ttf")

# Calibração contra o Labelary (que é a referência de como uma Zebra imprime).
# Medido em 8 dpmm para h de 20 a 80 pontos, com ``_AMOSTRA``:
#
#   altura da maiúscula = 0,775 × h      (o ``h`` do ^A0 é a célula, não o corpo)
#   largura da amostra  = 10,7 × altura da maiúscula
#
# Guardar a *proporção* em vez do corpo da fonte é o que faz a calibração
# sobreviver à troca de fonte: se a Arial Narrow Bold não existir na máquina e
# cair na Arial Bold, o corpo e o aperto horizontal se recalculam sozinhos a
# partir da métrica real da fonte carregada, e a quebra de linha continua caindo
# na mesma palavra que cairia numa Zebra.
_ALTURA_MAIUSCULA = 0.775
_PROPORCAO_ZEBRA = 10.71
_AMOSTRA = "HAMBURGEFONTSIV"

# Folga entre as barras e a linha de interpretação: 6 pontos, fixos, não importa
# a largura do módulo (medido no Labelary com ^BY1, 2, 3 e 5).
FOLGA_LEGENDA = 6
# A Zebra desenha o QR 10 pontos abaixo do ^FO. Não é arredondamento: é o mesmo
# deslocamento em qualquer magnificação.
DESLOCAMENTO_QR = 10

# EAN/UPC não seguem a regra das outras simbologias: os dígitos ficam *entre* as
# guardas, que descem até a linha do texto. Cada elemento do EAN vale um módulo,
# então estes intervalos servem tanto de índice quanto de posição.
_FAMILIA_EAN = ("EAN13", "EAN8", "UPCA")
_GUARDAS_EAN = {
    "EAN13": ((0, 3), (45, 50), (92, 95)),
    "UPCA": ((0, 3), (45, 50), (92, 95)),
    "EAN8": ((0, 3), (31, 36), (64, 67)),
}


class ZplInvalido(ValueError):
    """ZPL que não dá para desenhar de jeito nenhum (vazio, sem ^XA…)."""


# --------------------------------------------------------------------------- #
# Análise
# --------------------------------------------------------------------------- #

_COMANDO = re.compile(r"[\^~]([A-Za-z@][A-Za-z0-9@]?)")


@dataclass(slots=True)
class Comando:
    prefixo: str  # '^' (formato) ou '~' (controle)
    nome: str
    params: str
    posicao: int  # onde o comando começa no texto original
    fim: int = 0  # e onde termina — é o que o ajustador usa para reescrever

    def lista(self, quantos: int, padrao: str = "") -> list[str]:
        partes = [p.strip() for p in self.params.split(",")]
        partes += [padrao] * max(0, quantos - len(partes))
        return partes[:quantos]

    def numero(self, indice: int, padrao: int = 0) -> int:
        partes = self.params.split(",")
        if indice >= len(partes):
            return padrao
        texto = re.sub(r"[^\d-]", "", partes[indice])
        try:
            return int(texto)
        except ValueError:
            return padrao


def analisar(zpl: str) -> list[Comando]:
    """Quebra o ZPL em comandos. Dado de ``^FD`` vai até o próximo ``^``/``~``."""
    comandos: list[Comando] = []
    i, n = 0, len(zpl)
    while i < n:
        if zpl[i] not in "^~":
            i += 1
            continue
        casou = _COMANDO.match(zpl, i)
        if not casou:
            i += 1
            continue
        fim = casou.end()
        while fim < n and zpl[fim] not in "^~":
            fim += 1
        comandos.append(
            Comando(zpl[i], casou.group(1).upper(), zpl[casou.end():fim], i, fim)
        )
        i = fim
    return comandos


def separar_etiquetas(zpl: str) -> list[str]:
    """Uma etiqueta por ``^XA``…``^XZ``. É o que alimenta o 'etiqueta N de M'."""
    partes = re.split(r"(?=\^XA)", zpl or "")
    etiquetas = [p.strip() for p in partes if "^XA" in p.upper()]
    return etiquetas or ([zpl.strip()] if (zpl or "").strip() else [])


# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Fonte:
    designador: str = "0"
    altura: int = 30
    largura: int = 0  # 0 = proporcional


@dataclass(slots=True)
class PadraoBarras:
    modulo: int = 2
    razao: float = 3.0
    altura: int = 10


@dataclass(slots=True)
class Bloco:
    largura: int
    linhas: int = 1
    espaco: int = 0
    alinhamento: str = "L"
    recuo: int = 0


@dataclass(slots=True)
class Barra:
    """Um ``^BC``/``^B3``/``^BQ``… esperando o ``^FD`` que traz o dado."""

    tipo: str
    orientacao: str = "N"
    altura: int = 0
    legivel: bool = True
    legivel_acima: bool = False
    verificador: bool = False
    magnificacao: int = 2
    correcao: str = "M"
    modo: str = ""


@dataclass(slots=True)
class Campo:
    """O que se acumula entre um ``^FO`` e o ``^FS``."""

    x: int = 0
    y: int = 0
    baseline: bool = False  # ^FT posiciona pela linha de base, ^FO pelo topo
    orientacao: str | None = None
    fonte: Fonte | None = None
    bloco: Bloco | None = None
    barra: Barra | None = None
    reverso: bool = False
    hexadecimal: bool = False
    origem_definida: bool = False


@dataclass(slots=True)
class Resultado:
    imagem: Image.Image
    largura_pontos: int
    altura_pontos: int
    dpmm: int
    avisos: list[str] = field(default_factory=list)
    comandos_ignorados: list[str] = field(default_factory=list)
    # Até onde o ZPL pediu para desenhar, contando o que ficou fora da tela.
    extensao_pontos: tuple[int, int] = (0, 0)


# --------------------------------------------------------------------------- #
# Fontes
# --------------------------------------------------------------------------- #


def _procurar_fonte(nomes: tuple[str, ...]) -> str | None:
    diretorios = [
        Path(r"C:\Windows\Fonts"),
        Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
        Path("/usr/share/fonts/truetype/dejavu"),
    ]
    for nome in nomes:
        for pasta in diretorios:
            caminho = pasta / nome
            if caminho.exists():
                return str(caminho)
    return None


@lru_cache(maxsize=8)
def _metrica(arquivo: str | None) -> tuple[float, float]:
    """(altura da maiúscula por corpo, aperto horizontal) da fonte de verdade.

    É o que permite calibrar sem depender de qual fonte o Windows tem: mede-se
    a fonte que foi carregada e ajusta-se para as proporções da Zebra.
    """
    referencia = 100
    fonte = (
        ImageFont.truetype(arquivo, referencia)
        if arquivo
        else ImageFont.load_default(size=referencia)
    )
    caixa = fonte.getbbox("H")
    alta = (caixa[3] - caixa[1]) / referencia or 0.72
    larga = fonte.getlength(_AMOSTRA) / referencia
    aperto = (_PROPORCAO_ZEBRA * alta) / larga if larga else 1.0
    return alta, aperto


@lru_cache(maxsize=256)
def _fonte_para(designador: str, altura: int) -> tuple[ImageFont.FreeTypeFont, int, float]:
    """Devolve (fonte, altura da maiúscula em pontos, aperto horizontal)."""
    escalavel = designador in "0@"
    arquivo = _procurar_fonte(_FONTES_ESCALAVEIS if escalavel else _FONTES_FIXAS)
    alta, aperto = _metrica(arquivo)
    maiuscula = max(3, int(round(altura * _ALTURA_MAIUSCULA)))
    corpo = max(4, int(round(maiuscula / alta)))
    fonte = (
        ImageFont.truetype(arquivo, corpo) if arquivo else ImageFont.load_default(size=corpo)
    )
    return fonte, maiuscula, aperto


def _ampliar(fonte: ImageFont.FreeTypeFont, fator: int) -> ImageFont.FreeTypeFont:
    """A mesma fonte num corpo ``fator`` vezes maior, para desenhar e reduzir."""
    try:
        return fonte.font_variant(size=int(fonte.size * fator))
    except (AttributeError, OSError):  # pragma: no cover - fonte embutida do PIL
        return fonte


def medir_na_fonte(texto: str, fonte: ImageFont.FreeTypeFont) -> int:
    """Largura no espaço da fonte, antes do aperto horizontal."""
    if not texto:
        return 0
    return int(round(fonte.getlength(texto)))


def quebrar_na_fonte(
    texto: str, largura_max: int, fonte: ImageFont.FreeTypeFont, recuo: int = 0
) -> list[str]:
    """Quebra igual à da impressora: por palavra, e fatia a palavra gigante.

    ``\\&`` no dado é quebra de linha forçada — é assim que o ZPL manda pular
    linha dentro de um ``^FB``.
    """
    linhas: list[str] = []
    for pedaco in texto.split("\\&"):
        atual = ""
        for palavra in pedaco.split(" "):
            limite = max(1, largura_max - (recuo if (linhas or atual) else 0))
            candidata = f"{atual} {palavra}" if atual else palavra
            if medir_na_fonte(candidata, fonte) <= limite:
                atual = candidata
                continue
            if atual:
                linhas.append(atual)
            # Palavra sozinha maior que a caixa: corta no ponto exato.
            while palavra and medir_na_fonte(palavra, fonte) > limite:
                corte = len(palavra)
                while corte > 1 and medir_na_fonte(palavra[:corte], fonte) > limite:
                    corte -= 1
                linhas.append(palavra[:corte])
                palavra = palavra[corte:]
            atual = palavra
        linhas.append(atual)
    return linhas or [""]


def texto_do_campo(dados: str, hexadecimal: bool = False) -> str:
    """O texto que a impressora vai desenhar, já sem os escapes ``_XX`` do ^FH."""
    limpo = (dados or "").replace(chr(13), "").replace(chr(10), "")
    return _decodificar_hex(limpo) if hexadecimal else limpo


def largura_do_texto(texto: str, designador: str, altura: int) -> int:
    """Largura em pontos que este texto ocupa na etiqueta, já com o aperto.

    É a mesma medida que o desenho usa — por isso o ajustador automático pode
    decidir se algo cabe **antes** de desenhar, e acertar.
    """
    fonte, _, aperto = _fonte_para(designador, altura)
    return int(round(medir_na_fonte(texto, fonte) * aperto))


def linhas_do_texto(texto: str, largura_pontos: int, designador: str, altura: int) -> list[str]:
    """Em quantas linhas este texto cai dentro de ``largura_pontos``."""
    fonte, _, aperto = _fonte_para(designador, altura)
    largura_fonte = max(1, int(round(largura_pontos / aperto)) if aperto else largura_pontos)
    return quebrar_na_fonte(texto, largura_fonte, fonte)


def _escala_horizontal(fonte_altura: int, fonte_largura: int) -> float:
    """``w`` diferente de ``h`` estica horizontalmente; ``w=0`` é proporcional."""
    if not fonte_largura or not fonte_altura:
        return 1.0
    return fonte_largura / fonte_altura


# --------------------------------------------------------------------------- #
# Renderização
# --------------------------------------------------------------------------- #


class Renderizador:
    """Desenha **uma** etiqueta. Instância nova por etiqueta — sem estado velho."""

    def __init__(
        self,
        largura_pontos: int,
        altura_pontos: int,
        *,
        dpmm: int = DPMM_PADRAO,
    ) -> None:
        self.largura = max(1, largura_pontos)
        self.altura = max(1, altura_pontos)
        self.dpmm = dpmm
        self.tela = Image.new("L", (self.largura, self.altura), BRANCO)
        self.avisos: list[str] = []
        self.ignorados: list[str] = []
        self.extensao_direita = 0
        self.extensao_baixo = 0

        self.fonte_padrao = Fonte()
        self.padrao_barras = PadraoBarras()
        self.orientacao_padrao = "N"
        self.home = (0, 0)
        self.inverter_tudo = False
        self.reverso_etiqueta = False
        self.campo = Campo()
        self._dados_pendentes: str | None = None

    # -- utilidades ------------------------------------------------------- #

    def _avisar(self, mensagem: str) -> None:
        if mensagem not in self.avisos:
            self.avisos.append(mensagem)

    def _colar(self, mascara: Image.Image, x: int, y: int, *, reverso: bool) -> None:
        """Aplica a tinta da máscara na tela, no lugar certo.

        Máscara em 'L': 255 onde há tinta. ``reverso`` (``^FR``) inverte o que
        já está embaixo em vez de pintar de preto — é assim que sai texto branco
        sobre caixa preta.
        """
        if mascara.width <= 0 or mascara.height <= 0:
            return
        caixa = (x, y, x + mascara.width, y + mascara.height)
        # Guarda até onde o ZPL *quis* desenhar, mesmo fora da tela: é com isso
        # que a tela descobre depois que o arquivo pede uma etiqueta mais larga
        # do que o formato escolhido — e sabe dizer o que fazer a respeito.
        self.extensao_direita = max(self.extensao_direita, caixa[2])
        self.extensao_baixo = max(self.extensao_baixo, caixa[3])
        if caixa[0] >= self.largura or caixa[1] >= self.altura or caixa[2] <= 0 or caixa[3] <= 0:
            self._avisar("um campo caiu inteiro fora da etiqueta e não aparece")
            return
        if reverso != self.reverso_etiqueta:
            regiao = self.tela.crop(caixa)
            self.tela.paste(ImageChops.invert(regiao), caixa, mascara)
        else:
            self.tela.paste(PRETO, caixa, mascara)

    def _fonte_do_campo(self) -> Fonte:
        return self.campo.fonte or self.fonte_padrao

    def _orientacao(self) -> str:
        return self.campo.orientacao or self.orientacao_padrao

    @staticmethod
    def _girar(mascara: Image.Image, orientacao: str) -> Image.Image:
        if orientacao == "R":
            return mascara.transpose(Image.Transpose.ROTATE_270)
        if orientacao == "I":
            return mascara.transpose(Image.Transpose.ROTATE_180)
        if orientacao == "B":
            return mascara.transpose(Image.Transpose.ROTATE_90)
        return mascara

    # -- texto ------------------------------------------------------------ #

    def _medir(self, texto: str, fonte: ImageFont.FreeTypeFont) -> int:
        return medir_na_fonte(texto, fonte)

    def _quebrar(
        self,
        texto: str,
        largura_max: int,
        fonte: ImageFont.FreeTypeFont,
        recuo: int,
    ) -> list[str]:
        return quebrar_na_fonte(texto, largura_max, fonte, recuo)

    def _desenhar_texto(self, dados: str) -> None:
        fonte_zpl = self._fonte_do_campo()
        fonte, maiuscula, aperto = _fonte_para(fonte_zpl.designador, fonte_zpl.altura)
        # O aperto da calibração e o esticamento pedido no ``^A0N,h,w`` se somam.
        escala = aperto * _escala_horizontal(fonte_zpl.altura, fonte_zpl.largura)
        altura_linha = fonte_zpl.altura

        bloco = self.campo.bloco
        if bloco:
            # A quebra é decidida no espaço da fonte: a caixa do ^FB está em
            # pontos da etiqueta, então volta pelo aperto antes de comparar.
            largura_fonte = max(1, int(round(bloco.largura / escala)))
            recuo_fonte = int(round(bloco.recuo / escala))
            linhas = self._quebrar(dados, largura_fonte, fonte, recuo_fonte)
            if len(linhas) > bloco.linhas:
                self._avisar(
                    f"o texto não coube nas {bloco.linhas} linha(s) do ^FB e foi cortado"
                )
                linhas = linhas[: bloco.linhas]
            espaco = bloco.espaco
            alinhamento = bloco.alinhamento
        else:
            linhas = [dados.replace("\\&", " ")]
            largura_fonte = self._medir(linhas[0], fonte) + 2
            recuo_fonte = 0
            espaco = 0
            alinhamento = "L"

        altura_total = len(linhas) * altura_linha + max(0, len(linhas) - 1) * espaco
        # Desenha ampliado e reduz: o traço fica limpo depois do aperto horizontal.
        s = 3
        mascara = Image.new("L", (max(1, largura_fonte) * s, max(1, altura_total) * s), 0)
        pincel = ImageDraw.Draw(mascara)
        fonte_ampliada = _ampliar(fonte, s)

        for i, linha in enumerate(linhas):
            largura_linha = self._medir(linha, fonte)
            if alinhamento == "C":
                x = (largura_fonte - largura_linha) // 2
            elif alinhamento == "R":
                x = largura_fonte - largura_linha
            else:
                x = recuo_fonte if i else 0
            # ``anchor='ls'`` = linha de base. A maiúscula encosta no topo da
            # célula, que é onde o ^FO manda desenhar.
            base = maiuscula + i * (altura_linha + espaco)
            pincel.text((max(0, x) * s, base * s), linha, font=fonte_ampliada,
                        fill=255, anchor="ls")

        mascara = mascara.resize(
            (max(1, int(round(largura_fonte * escala))), max(1, altura_total)),
            Image.LANCZOS,
        )

        x, y = self._posicao_do_campo(maiuscula)
        mascara = self._girar(mascara, self._orientacao())
        self._colar(mascara, x, y, reverso=self.campo.reverso)

    def _posicao_do_campo(self, maiuscula: int) -> tuple[int, int]:
        """``^FO`` posiciona pelo topo da célula; ``^FT``, pela linha de base."""
        x = self.campo.x + self.home[0]
        y = self.campo.y + self.home[1]
        if self.campo.baseline:
            if self._orientacao() in ("N", "I"):
                y -= maiuscula
            else:
                x -= maiuscula
        return x, y

    # -- código de barras ------------------------------------------------- #

    def _desenhar_barras(self, dados: str) -> None:
        barra = self.campo.barra
        assert barra is not None
        padrao = self.padrao_barras
        altura = max(1, barra.altura or padrao.altura)

        if barra.tipo == "QR":
            self._desenhar_qr(dados, barra)
            return
        if barra.tipo in ("DATAMATRIX", "PDF417", "AZTEC"):
            self._desenhar_placeholder(dados, barra)
            return

        try:
            simbolo = self._codificar(barra, dados, padrao)
        except barras.CodigoInvalido as exc:
            self._avisar(f"código de barras não gerado: {exc}")
            return

        modulo = max(1, padrao.modulo)
        if barra.tipo in _FAMILIA_EAN:
            mascara, recuo_esquerda = self._mascara_ean(simbolo, barra, modulo, altura)
        else:
            mascara, recuo_esquerda = self._mascara_comum(simbolo, barra, modulo, altura), 0

        x0 = self.campo.x + self.home[0] - recuo_esquerda
        y0 = self.campo.y + self.home[1]
        if barra.legivel and barra.legivel_acima and barra.tipo not in _FAMILIA_EAN:
            # A linha de interpretação acima sobe *fora* do campo: as barras
            # continuam começando na altura do ^FO (conferido no Labelary).
            y0 -= FOLGA_LEGENDA + self._altura_legivel(modulo)
        if self.campo.baseline:
            y0 -= mascara.height
        mascara = self._girar(mascara, barra.orientacao)
        self._colar(mascara, x0, y0, reverso=self.campo.reverso)

    def _mascara_comum(
        self, simbolo: barras.Simbolo, barra: Barra, modulo: int, altura: int
    ) -> Image.Image:
        largura = int(round(simbolo.modulos * modulo))
        celula = self._altura_legivel(modulo) if barra.legivel else 0
        folga = FOLGA_LEGENDA if barra.legivel else 0

        mascara = Image.new("L", (max(1, largura), max(1, altura + folga + celula)), 0)
        pincel = ImageDraw.Draw(mascara)

        topo_barras = (folga + celula) if (barra.legivel and barra.legivel_acima) else 0
        x = 0.0
        for larg, e_barra in simbolo.elementos:
            passo = larg * modulo
            if e_barra:
                pincel.rectangle(
                    [int(round(x)), topo_barras,
                     int(round(x + passo)) - 1, topo_barras + altura - 1],
                    fill=255,
                )
            x += passo

        if barra.legivel:
            self._legenda(
                mascara, simbolo.texto_legivel, largura, celula,
                0 if barra.legivel_acima else topo_barras + altura + folga,
            )
        return mascara

    def _mascara_ean(
        self, simbolo: barras.Simbolo, barra: Barra, modulo: int, altura: int
    ) -> tuple[Image.Image, int]:
        """EAN/UPC tem layout próprio: guardas compridas e dígitos entre elas.

        Devolve também o quanto a máscara avança para a **esquerda** do ``^FO``:
        no EAN-13 o primeiro dígito é impresso fora do símbolo, e sem esse recuo
        ele sairia por cima do que estiver ao lado.
        """
        celula = self._altura_legivel(modulo) if barra.legivel else 0
        digitos = simbolo.texto_legivel
        fora_esq, esquerda, direita, fora_dir = _reparticao_ean(barra.tipo, digitos)
        if not barra.legivel:
            fora_esq = esquerda = direita = fora_dir = ""

        largura_digito = self._largura_digito(celula) if barra.legivel else 0
        recuo = (largura_digito + modulo * 6) if fora_esq else 0
        sobra = (largura_digito + modulo * 4) if fora_dir else 0

        largura = int(round(simbolo.modulos * modulo))
        mascara = Image.new("L", (max(1, largura + recuo + sobra), max(1, altura + celula)), 0)
        pincel = ImageDraw.Draw(mascara)

        # As guardas (início, meio, fim) descem até a linha dos dígitos.
        guardas = _GUARDAS_EAN.get(barra.tipo, ())
        x = 0.0
        posicoes: list[tuple[int, int]] = []  # (x inicial, x final) de cada módulo-barra
        for indice, (larg, e_barra) in enumerate(simbolo.elementos):
            passo = larg * modulo
            if e_barra:
                comprida = any(inicio <= indice < fim for inicio, fim in guardas)
                fundo = altura + (celula if comprida and barra.legivel else 0)
                pincel.rectangle(
                    [recuo + int(round(x)), 0, recuo + int(round(x + passo)) - 1, fundo - 1],
                    fill=255,
                )
                posicoes.append((int(round(x)), int(round(x + passo))))
            x += passo

        if barra.legivel and len(guardas) == 3:
            (_, fim_inicial), (inicio_meio, fim_meio), (inicio_final, _) = guardas
            self._digitos_ean(mascara, fora_esq, 0, 0, altura, celula)
            self._digitos_ean(
                mascara, esquerda,
                recuo + fim_inicial * modulo, (inicio_meio - fim_inicial) * modulo,
                altura, celula,
            )
            self._digitos_ean(
                mascara, direita,
                recuo + fim_meio * modulo, (inicio_final - fim_meio) * modulo,
                altura, celula,
            )
            self._digitos_ean(
                mascara, fora_dir, recuo + largura + modulo * 4, 0, altura, celula
            )
        return mascara, recuo

    def _largura_digito(self, celula: int) -> int:
        fonte, _, aperto = _fonte_para("0", celula)
        return max(1, int(round(fonte.getlength("0") * aperto)))

    def _digitos_ean(
        self, mascara: Image.Image, texto: str, x: int, largura: int, y: int, celula: int
    ) -> None:
        """Desenha um grupo de dígitos centrado em ``largura`` (0 = à esquerda)."""
        if not texto:
            return
        fonte, maiuscula, aperto = _fonte_para("0", celula)
        largura_fonte = max(1, int(fonte.getlength(texto)) + 2)
        pedaco = Image.new("L", (largura_fonte, max(1, celula)), 0)
        ImageDraw.Draw(pedaco).text((0, maiuscula), texto, font=fonte, fill=255, anchor="ls")
        pedaco = pedaco.resize(
            (max(1, int(round(largura_fonte * aperto))), pedaco.height), Image.LANCZOS
        )
        mascara.paste(pedaco, (x + max(0, (largura - pedaco.width) // 2), y), pedaco)

    def _codificar(self, barra: Barra, dados: str, padrao: PadraoBarras) -> barras.Simbolo:
        if barra.tipo == "CODE128":
            # Sem modo (o padrão do ^BC), a Zebra fica no subset B do começo ao
            # fim — quem liga a troca automática para o C é o modo 'A'. Medido
            # contra o Labelary: '12345678' dá 123 módulos no N e 79 no A.
            if barra.modo in ("U", "D"):
                self._avisar(
                    f"^BC modo {barra.modo} (UCC/EAN) não é implementado; "
                    "desenhado como Code 128 comum"
                )
            return barras.code128(dados, subset="auto" if barra.modo == "A" else "B")
        if barra.tipo == "CODE39":
            return barras.code39(dados, razao=padrao.razao, verificador=barra.verificador)
        if barra.tipo == "I25":
            return barras.intercalado25(dados, razao=padrao.razao, verificador=barra.verificador)
        if barra.tipo == "EAN13":
            return barras.ean13(dados)
        if barra.tipo == "EAN8":
            return barras.ean8(dados)
        if barra.tipo == "UPCA":
            return barras.upca(dados)
        raise barras.CodigoInvalido(f"simbologia {barra.tipo} não implementada")

    def _altura_legivel(self, modulo: int) -> int:
        """Célula da linha de interpretação: a fonte A da Zebra magnificada."""
        return max(9, modulo * 9)

    def _legenda(
        self, mascara: Image.Image, texto: str, largura: int, celula: int, y: int
    ) -> None:
        """Linha de interpretação — o número embaixo (ou acima) das barras."""
        fonte, maiuscula, aperto = _fonte_para("0", celula)
        largura_fonte = max(1, int(fonte.getlength(texto)) + 2)
        etiqueta = Image.new("L", (largura_fonte, max(1, celula)), 0)
        ImageDraw.Draw(etiqueta).text((0, maiuscula), texto, font=fonte, fill=255, anchor="ls")
        etiqueta = etiqueta.resize(
            (max(1, int(round(largura_fonte * aperto))), etiqueta.height), Image.LANCZOS
        )
        mascara.paste(etiqueta, ((largura - etiqueta.width) // 2, y), etiqueta)

    def _desenhar_qr(self, dados: str, barra: Barra) -> None:
        conteudo, correcao = _dados_do_qr(dados, barra.correcao)
        try:
            matriz = barras.qrcode(conteudo, correcao=correcao)
        except Exception as exc:  # noqa: BLE001 - segno levanta tipos variados
            self._avisar(f"QR não gerado: {exc}")
            return
        escala = max(1, barra.magnificacao)
        lado = len(matriz) * escala
        mascara = Image.new("L", (lado, lado), 0)
        pincel = ImageDraw.Draw(mascara)
        for linha_i, linha in enumerate(matriz):
            for col_i, aceso in enumerate(linha):
                if aceso:
                    pincel.rectangle(
                        [col_i * escala, linha_i * escala,
                         (col_i + 1) * escala - 1, (linha_i + 1) * escala - 1],
                        fill=255,
                    )
        mascara = self._girar(mascara, barra.orientacao)
        self._colar(mascara, self.campo.x + self.home[0],
                    self.campo.y + self.home[1] + DESLOCAMENTO_QR,
                    reverso=self.campo.reverso)

    def _desenhar_placeholder(self, dados: str, barra: Barra) -> None:
        """2D ainda não implementado: desenha a área e avisa, em vez de mentir.

        Deixar em branco seria pior — a operadora só descobriria na hora que a
        etiqueta saísse sem o código.
        """
        self._avisar(
            f"{barra.tipo} ainda não é desenhado aqui; use o motor Labelary para conferir"
        )
        lado = max(40, barra.altura or 80)
        mascara = Image.new("L", (lado, lado), 0)
        pincel = ImageDraw.Draw(mascara)
        pincel.rectangle([0, 0, lado - 1, lado - 1], outline=255, width=2)
        pincel.line([0, 0, lado - 1, lado - 1], fill=255, width=2)
        pincel.line([lado - 1, 0, 0, lado - 1], fill=255, width=2)
        self._colar(mascara, self.campo.x + self.home[0], self.campo.y + self.home[1],
                    reverso=False)

    # -- gráficos --------------------------------------------------------- #

    def _caixa(self, cmd: Comando) -> None:
        largura = cmd.numero(0, 1)
        altura = cmd.numero(1, 1)
        espessura = max(1, cmd.numero(2, 1))
        cor = (cmd.lista(4)[3] or "B").upper()
        arredondamento = cmd.numero(4, 0)

        largura = max(largura, espessura)
        altura = max(altura, espessura)
        mascara = Image.new("L", (max(1, largura), max(1, altura)), 0)
        pincel = ImageDraw.Draw(mascara)
        raio = int(min(largura, altura) / 2 * (min(arredondamento, 8) / 8)) if arredondamento else 0
        if espessura * 2 >= min(largura, altura):
            pincel.rectangle([0, 0, largura - 1, altura - 1], fill=255)
        elif raio:
            pincel.rounded_rectangle(
                [0, 0, largura - 1, altura - 1], radius=raio, outline=255, width=espessura
            )
        else:
            pincel.rectangle([0, 0, largura - 1, altura - 1], outline=255, width=espessura)

        reverso = self.campo.reverso or cor == "W"
        self._colar(mascara, self.campo.x + self.home[0], self.campo.y + self.home[1],
                    reverso=reverso)

    def _diagonal(self, cmd: Comando) -> None:
        largura = max(1, cmd.numero(0, 1))
        altura = max(1, cmd.numero(1, 1))
        espessura = max(1, cmd.numero(2, 1))
        orientacao = (cmd.lista(4)[3] or "R").upper()
        mascara = Image.new("L", (largura + espessura, altura + espessura), 0)
        pincel = ImageDraw.Draw(mascara)
        if orientacao == "L":
            pincel.line([0, 0, largura, altura], fill=255, width=espessura)
        else:
            pincel.line([0, altura, largura, 0], fill=255, width=espessura)
        self._colar(mascara, self.campo.x + self.home[0], self.campo.y + self.home[1],
                    reverso=self.campo.reverso)

    def _elipse(self, cmd: Comando, *, circulo: bool) -> None:
        if circulo:
            largura = altura = max(1, cmd.numero(0, 10))
            espessura = max(1, cmd.numero(1, 1))
            cor = (cmd.lista(3)[2] or "B").upper()
        else:
            largura = max(1, cmd.numero(0, 10))
            altura = max(1, cmd.numero(1, 10))
            espessura = max(1, cmd.numero(2, 1))
            cor = (cmd.lista(4)[3] or "B").upper()
        mascara = Image.new("L", (largura, altura), 0)
        pincel = ImageDraw.Draw(mascara)
        pincel.ellipse([0, 0, largura - 1, altura - 1], outline=255, width=espessura)
        self._colar(mascara, self.campo.x + self.home[0], self.campo.y + self.home[1],
                    reverso=self.campo.reverso or cor == "W")

    def _grafico(self, cmd: Comando) -> None:
        """``^GF`` — a logomarca do Mercado Livre chega por aqui."""
        partes = cmd.params.split(",", 4)
        if len(partes) < 5:
            self._avisar("^GF sem os cinco parâmetros; imagem ignorada")
            return
        formato = (partes[0].strip() or "A").upper()
        try:
            bytes_por_linha = int(re.sub(r"\D", "", partes[3]) or 0)
        except ValueError:
            bytes_por_linha = 0
        if bytes_por_linha <= 0:
            self._avisar("^GF sem bytes por linha; imagem ignorada")
            return

        try:
            dados = _bytes_do_grafico(partes[4], formato, bytes_por_linha)
        except (ValueError, binascii.Error, zlib.error) as exc:
            self._avisar(f"^GF não decodificado: {exc}")
            return
        if not dados:
            return

        alturas = len(dados) // bytes_por_linha
        if alturas <= 0:
            return
        imagem = Image.frombytes("1", (bytes_por_linha * 8, alturas), dados)
        mascara = imagem.convert("L")  # 1 = bit ligado = tinta
        self._colar(mascara, self.campo.x + self.home[0], self.campo.y + self.home[1],
                    reverso=self.campo.reverso)

    # -- laço principal --------------------------------------------------- #

    def executar(self, comandos: list[Comando]) -> None:
        for cmd in comandos:
            self._executar_um(cmd)

    def _executar_um(self, cmd: Comando) -> None:  # noqa: C901 - despacho por comando
        nome = cmd.nome

        if nome in ("XA", "XZ", "CI", "MN", "MM", "MT", "MD", "PR", "PQ", "SD", "PO",
                    "LR", "CW", "JU", "JM", "SS", "SZ", "MU", "LT", "LS", "CN", "PA"):
            if nome == "PO" and (cmd.lista(1)[0] or "N").upper() == "I":
                self.inverter_tudo = True
            if nome == "LR" and (cmd.lista(1)[0] or "N").upper() == "Y":
                self.reverso_etiqueta = True
            return

        if nome == "FX":
            return
        if nome == "PW":
            return  # a largura vem do formato escolhido, não do ZPL
        if nome == "LL":
            return
        if nome == "LH":
            self.home = (cmd.numero(0, 0), cmd.numero(1, 0))
            return
        if nome == "CF":
            designador = (cmd.lista(3)[0] or "0").upper()
            self.fonte_padrao = Fonte(designador, cmd.numero(1, 30), cmd.numero(2, 0))
            return
        if nome == "FW":
            self.orientacao_padrao = (cmd.lista(1)[0] or "N").upper()[:1]
            return
        if nome == "BY":
            self.padrao_barras = PadraoBarras(
                max(1, cmd.numero(0, 2)),
                _decimal(cmd.lista(3)[1], 3.0),
                cmd.numero(2, self.padrao_barras.altura),
            )
            return

        if nome.startswith("A") and len(nome) == 2 and nome[1] in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ@":
            designador = nome[1]
            partes = cmd.lista(3)
            self.campo.orientacao = (partes[0] or "N").upper()[:1] or "N"
            self.campo.fonte = Fonte(
                designador,
                _inteiro(partes[1], self.fonte_padrao.altura),
                _inteiro(partes[2], 0),
            )
            return

        if nome in ("FO", "FT"):
            self.campo.x = cmd.numero(0, 0)
            self.campo.y = cmd.numero(1, 0)
            self.campo.baseline = nome == "FT"
            self.campo.origem_definida = True
            return
        if nome == "FB":
            self.campo.bloco = Bloco(
                max(1, cmd.numero(0, self.largura)),
                max(1, cmd.numero(1, 1)),
                cmd.numero(2, 0),
                (cmd.lista(5)[3] or "L").upper()[:1] or "L",
                cmd.numero(4, 0),
            )
            return
        if nome == "FR":
            self.campo.reverso = True
            return
        if nome == "FH":
            self.campo.hexadecimal = True
            return

        if nome in ("FD", "FV"):
            self._dados_pendentes = cmd.params
            return
        if nome == "FS":
            self._encerrar_campo()
            return

        barra = _barra_do_comando(nome, cmd, self.padrao_barras)
        if barra is not None:
            self.campo.barra = barra
            return

        if nome == "GB":
            self._caixa(cmd)
            self._campo_grafico_encerrado()
            return
        if nome == "GD":
            self._diagonal(cmd)
            self._campo_grafico_encerrado()
            return
        if nome == "GC":
            self._elipse(cmd, circulo=True)
            self._campo_grafico_encerrado()
            return
        if nome == "GE":
            self._elipse(cmd, circulo=False)
            self._campo_grafico_encerrado()
            return
        if nome == "GF":
            self._grafico(cmd)
            self._campo_grafico_encerrado()
            return

        if cmd.prefixo == "~":
            return  # comandos de controle da impressora não desenham nada
        rotulo = f"^{nome}"
        if rotulo not in self.ignorados:
            self.ignorados.append(rotulo)

    def _campo_grafico_encerrado(self) -> None:
        """``^GB`` e companhia desenham na hora; o ``^FS`` seguinte só limpa."""
        self._dados_pendentes = None

    def _encerrar_campo(self) -> None:
        dados = self._dados_pendentes
        self._dados_pendentes = None
        if dados is not None:
            dados = dados.replace("\r", "").replace("\n", "")
            if self.campo.hexadecimal:
                dados = _decodificar_hex(dados)
            if self.campo.barra is not None:
                self._desenhar_barras(dados)
            elif dados:
                self._desenhar_texto(dados)
        self.campo = Campo()

    def finalizar(self) -> Image.Image:
        if self.inverter_tudo:
            return self.tela.transpose(Image.Transpose.ROTATE_180)
        return self.tela


# --------------------------------------------------------------------------- #
# Auxiliares de parsing
# --------------------------------------------------------------------------- #


def _inteiro(texto: str, padrao: int) -> int:
    limpo = re.sub(r"[^\d-]", "", texto or "")
    try:
        return int(limpo)
    except ValueError:
        return padrao


def _decimal(texto: str, padrao: float) -> float:
    limpo = re.sub(r"[^\d.]", "", texto or "")
    try:
        return float(limpo)
    except ValueError:
        return padrao


def _decodificar_hex(dados: str) -> str:
    """``^FH`` liga o escape ``_XX``: ``_5F`` vira ``_``, ``_C3_A9`` vira ``é``."""
    def trocar(casou: re.Match[str]) -> str:
        return chr(int(casou.group(1), 16))

    bruto = re.sub(r"_([0-9A-Fa-f]{2})", trocar, dados)
    try:
        return bruto.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return bruto


def _dados_do_qr(dados: str, correcao_padrao: str) -> tuple[str, str]:
    """O ``^FD`` do QR vem com prefixo tipo ``QA,`` — nível de correção e modo."""
    casou = re.match(r"^([HQMLhqml])([AMBamb]),(.*)$", dados, re.DOTALL)
    if casou:
        return casou.group(3), casou.group(1).upper()
    casou = re.match(r"^[A-Za-z]{2},(.*)$", dados, re.DOTALL)
    if casou:
        return casou.group(1), correcao_padrao
    return dados, correcao_padrao


def _barra_do_comando(nome: str, cmd: Comando, padrao: PadraoBarras) -> Barra | None:
    """Traduz ``^BC``/``^B3``/… no que o desenhista precisa saber."""
    partes = cmd.lista(8)
    orientacao = (partes[0] or "N").upper()[:1] or "N"

    if nome == "BC":
        return Barra(
            "CODE128", orientacao, _inteiro(partes[1], padrao.altura),
            (partes[2] or "Y").upper() != "N", (partes[3] or "N").upper() == "Y",
            (partes[4] or "N").upper() == "Y", modo=(partes[5] or "").upper()[:1],
        )
    if nome == "B3":
        # ^B3 troca a ordem: orientação, verificador, altura, legível, acima.
        return Barra(
            "CODE39", orientacao, _inteiro(partes[2], padrao.altura),
            (partes[3] or "Y").upper() != "N", (partes[4] or "N").upper() == "Y",
            (partes[1] or "N").upper() == "Y",
        )
    if nome == "B2":
        return Barra(
            "I25", orientacao, _inteiro(partes[1], padrao.altura),
            (partes[2] or "Y").upper() != "N", (partes[3] or "N").upper() == "Y",
            (partes[4] or "N").upper() == "Y",
        )
    if nome == "BE":
        return Barra("EAN13", orientacao, _inteiro(partes[1], padrao.altura),
                     (partes[2] or "Y").upper() != "N", (partes[3] or "N").upper() == "Y")
    if nome == "B8":
        return Barra("EAN8", orientacao, _inteiro(partes[1], padrao.altura),
                     (partes[2] or "Y").upper() != "N", (partes[3] or "N").upper() == "Y")
    if nome == "BU":
        return Barra("UPCA", orientacao, _inteiro(partes[1], padrao.altura),
                     (partes[2] or "Y").upper() != "N", (partes[3] or "N").upper() == "Y")
    if nome == "BQ":
        return Barra(
            "QR", orientacao, 0, False, False, False,
            magnificacao=max(1, _inteiro(partes[2], 2)),
            correcao=(partes[3] or "M").upper()[:1] or "M",
        )
    if nome == "BX":
        return Barra("DATAMATRIX", orientacao, _inteiro(partes[1], 0) * 10)
    if nome == "B7":
        return Barra("PDF417", orientacao, _inteiro(partes[1], 0) * 10)
    if nome == "B0":
        return Barra("AZTEC", orientacao, _inteiro(partes[1], 0) * 10)
    return None


def _reparticao_ean(tipo: str, digitos: str) -> tuple[str, str, str, str]:
    """Como os dígitos se dividem: (fora à esquerda, metade, metade, fora à direita).

    O EAN-13 joga o primeiro dígito para fora do símbolo; o UPC-A joga o
    primeiro **e** o último. Sem isso, a legenda fica com dígito a mais no meio.
    """
    if tipo == "EAN13":
        return digitos[0], digitos[1:7], digitos[7:], ""
    if tipo == "UPCA":
        return digitos[0], digitos[1:6], digitos[6:11], digitos[11:]
    metade = len(digitos) // 2
    return "", digitos[:metade], digitos[metade:], ""


def _bytes_do_grafico(dados: str, formato: str, bytes_por_linha: int) -> bytes:
    """Decodifica o payload do ``^GF``, com as três compressões que aparecem."""
    dados = dados.strip()
    casou = re.match(r"^:?([ZB])64:(.*?):([0-9A-Fa-f]+)\s*$", dados, re.DOTALL)
    if casou:
        cru = base64.b64decode(re.sub(r"\s", "", casou.group(2)))
        return zlib.decompress(cru) if casou.group(1) == "Z" else cru
    if formato in ("B", "C"):
        return dados.encode("latin-1")
    return _hex_comprimido(dados, bytes_por_linha)


def _hex_comprimido(dados: str, bytes_por_linha: int) -> bytes:
    """Hex ASCII com a compressão da Zebra.

    ``G``–``Y`` repetem o próximo dígito 1–19 vezes, ``g``–``z`` repetem 20–400
    (de 20 em 20) e os dois se somam. ``,`` completa a linha com zeros, ``!``
    completa com uns e ``:`` repete a linha inteira anterior.
    """
    largura_hex = bytes_por_linha * 2
    linhas: list[str] = []
    atual: list[str] = []
    repeticao = 0
    i = 0

    def fechar(preenchimento: str) -> None:
        nonlocal atual
        linha = "".join(atual)
        linha = linha[:largura_hex].ljust(largura_hex, preenchimento)
        linhas.append(linha)
        atual = []

    while i < len(dados):
        c = dados[i]
        i += 1
        if c in " \r\n\t":
            continue
        if "G" <= c <= "Y":
            repeticao += ord(c) - ord("G") + 1
            continue
        if "g" <= c <= "z":
            repeticao += (ord(c) - ord("g") + 1) * 20
            continue
        if c == ",":
            fechar("0")
            repeticao = 0
            continue
        if c == "!":
            fechar("F")
            repeticao = 0
            continue
        if c == ":":
            linhas.append(linhas[-1] if linhas else "0" * largura_hex)
            atual = []
            repeticao = 0
            continue
        if c in "0123456789ABCDEFabcdef":
            atual.append(c.upper() * max(1, repeticao))
            repeticao = 0
            if len("".join(atual)) >= largura_hex:
                fechar("0")
            continue
        # Qualquer outra coisa é ruído — ignora em vez de derrubar a etiqueta.
    if atual:
        fechar("0")

    corpo = "".join(linhas)
    if len(corpo) % 2:
        corpo += "0"
    return bytes.fromhex(corpo)


# --------------------------------------------------------------------------- #
# Porta de entrada
# --------------------------------------------------------------------------- #


def renderizar(
    zpl: str,
    *,
    largura_mm: float = 100,
    altura_mm: float = 150,
    dpmm: int = DPMM_PADRAO,
    indice: int = 0,
    rotacao: int = 0,
    bitonal: bool = False,
) -> Resultado:
    """Renderiza **uma** etiqueta (a de índice ``indice``) do ZPL dado."""
    etiquetas = separar_etiquetas(zpl)
    if not etiquetas:
        raise ZplInvalido("nada para renderizar")
    indice = max(0, min(indice, len(etiquetas) - 1))

    largura_pontos = max(1, int(round(largura_mm * dpmm)))
    altura_pontos = max(1, int(round(altura_mm * dpmm)))

    motor = Renderizador(largura_pontos, altura_pontos, dpmm=dpmm)
    motor.executar(analisar(etiquetas[indice]))
    imagem = motor.finalizar()

    if rotacao:
        imagem = imagem.rotate(-rotacao % 360, expand=True, fillcolor=BRANCO)
    if bitonal:
        imagem = imagem.point(lambda v: 0 if v < 128 else 255, mode="L")

    return Resultado(
        imagem=imagem,
        largura_pontos=imagem.width,
        altura_pontos=imagem.height,
        dpmm=dpmm,
        avisos=list(motor.avisos),
        comandos_ignorados=list(motor.ignorados),
        extensao_pontos=(motor.extensao_direita, motor.extensao_baixo),
    )


def compor(
    imagens: list[Image.Image],
    *,
    colunas: int = 1,
    espaco_pontos: int = 0,
    margem_pontos: int = 0,
) -> list[Image.Image]:
    """Distribui as etiquetas pelas colunas do papel, uma folha por linha.

    A última folha sai completa mesmo com uma etiqueta só: o espaço vazio à
    direita tem de existir, senão o papel avança errado e a próxima etiqueta
    nasce torta.
    """
    if not imagens:
        return []
    colunas = max(1, colunas)
    if colunas == 1 and not margem_pontos:
        return list(imagens)

    largura_etiqueta = max(i.width for i in imagens)
    altura_etiqueta = max(i.height for i in imagens)
    largura = largura_etiqueta * colunas + espaco_pontos * (colunas - 1) + margem_pontos * 2
    altura = altura_etiqueta + margem_pontos * 2

    folhas: list[Image.Image] = []
    for inicio in range(0, len(imagens), colunas):
        folha = Image.new("L", (largura, altura), BRANCO)
        for coluna, imagem in enumerate(imagens[inicio:inicio + colunas]):
            x = margem_pontos + coluna * (largura_etiqueta + espaco_pontos)
            folha.paste(imagem, (x, margem_pontos))
        folhas.append(folha)
    return folhas


def contar_etiquetas(zpl: str) -> int:
    return len(separar_etiquetas(zpl))


def mm_para_pontos(mm: float, dpmm: int = DPMM_PADRAO) -> int:
    return int(round(mm * dpmm))


def pontos_para_mm(pontos: int, dpmm: int = DPMM_PADRAO) -> float:
    return pontos / dpmm if dpmm else 0.0


__all__ = [
    "DPMM_PADRAO",
    "largura_do_texto",
    "linhas_do_texto",
    "medir_na_fonte",
    "quebrar_na_fonte",
    "texto_do_campo",
    "Comando",
    "Renderizador",
    "Resultado",
    "ZplInvalido",
    "analisar",
    "contar_etiquetas",
    "mm_para_pontos",
    "pontos_para_mm",
    "renderizar",
    "separar_etiquetas",
]
