"""PDF → bitmap da etiqueta, para quando o Mercado Livre não entrega ZPL.

O Mercado Livre passou a entregar a etiqueta de uma coluna em **PDF**: uma
folha A4 deitada com a etiqueta (≈ 90 × 149 mm, com moldura) no canto esquerdo
e o resto em branco, seguida de uma página A4 com a lista de conteúdo do
pedido. Mandar isso para a térmica do jeito que veio imprime uma folha A4
encolhida dentro de 100 × 150 mm — a etiqueta sai a um terço do tamanho e o
código de barras não lê.

O que este módulo faz:

1. **Acha a etiqueta dentro da folha.** Procura molduras — retângulos
   desenhados de pelo menos 25 mm de lado cuja borda tem tinta de verdade (um
   fundo branco do tamanho da página também é um retângulo, e não é moldura).
   Página sem moldura vira um recorte do que tem tinta nela. Se o documento tem
   moldura em alguma página, as páginas sem moldura (a lista de conteúdo) ficam
   **fora** da impressão por padrão — mas aparecem na tela e podem ser marcadas.
2. **Encaixa no formato sem distorcer.** A escala é a mesma nos dois eixos
   (esticar só um eixo muda a largura das barras e o leitor deixa de ler), o
   desenho fica centralizado e respeita a margem de segurança do formato. Se o
   conteúdo couber bem melhor de lado, gira 90°.
3. **Rasteriza direto na densidade da impressora**, a partir do vetor — e não
   uma imagem reduzida depois. Medido na etiqueta real a 8 dpmm: a barra fina
   do Code 128 sai com 4–5 pontos e as largas mantêm a proporção (8–9, 12–13,
   17), erro de no máximo um ponto, bem dentro do que um leitor tolera.
4. **Não deixa linha fina sumir.** A moldura do Mercado Livre tem menos de um
   ponto do cabeçote e desaparecia no preto e branco; veja
   ``_resgatar_linhas_finas``.

O preview e o papel saem daqui, da mesma função ``desenhar`` — a mesma regra do
``render`` para o ZPL.

Uma ressalva de infraestrutura: o PDFium **não é thread-safe**, e o FastAPI roda
as rotas síncronas em threads. Toda chamada a ele passa pela ``_TRAVA``.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageChops, ImageFilter

PONTOS_PDF_POR_MM = 72 / 25.4
TAMANHO_MAXIMO = 40 * 1024 * 1024
LIMIAR = 128  # o mesmo corte do ``render``: abaixo disso o cabeçote queima

# Moldura: o menor lado que conta como etiqueta, e o maior. Acima disso é
# borda decorativa de página, não etiqueta.
MOLDURA_MINIMA_MM = 25.0
MOLDURA_MAXIMA_MM = 220.0
# O traço da moldura fica metade fora do retângulo; sem a folga, a linha da
# borda sai cortada pela metade.
FOLGA_MOLDURA_MM = 0.5
FOLGA_CONTEUDO_MM = 1.0

# Procurar tinta não precisa da resolução da impressora. Mas, em baixa
# resolução, a linha fina da moldura do Mercado Livre vira um cinza claro
# (188–219 na borda direita, medido) — por isso "tinta" aqui é qualquer coisa
# visivelmente diferente do branco, e não o corte de 128 da impressão.
_PONTOS_POR_MM_ANALISE = 4
_TINTA = 235
_BORDA_COM_TINTA = 0.9  # fração de cada lado da moldura que precisa ter tinta

# Linha fina demais some no papel (veja ``_resgatar_linhas_finas``): ponto com
# pelo menos 20% de tinta que sumiria sozinho é escurecido para passar do corte.
_RESGATE = 204
_RESGATADO = 96

# Só gira se couber claramente maior — girar por 2% confunde mais do que ajuda.
GIRAR_SE_MELHORA = 1.10
ESCALA_PARA_AVISAR = 0.9

_TRAVA = threading.RLock()


class PdfInvalido(ValueError):
    """Arquivo que não é PDF, está protegido ou não tem nada desenhado."""


@dataclass(slots=True, frozen=True)
class Recorte:
    """Um pedaço de uma página que vira uma etiqueta."""

    pagina: int
    caixa: tuple[float, float, float, float]  # esquerda, baixo, direita, cima (pontos PDF)
    origem: str  # 'moldura' ou 'pagina'
    sugerido: bool = True

    @property
    def largura_mm(self) -> float:
        return (self.caixa[2] - self.caixa[0]) / PONTOS_PDF_POR_MM

    @property
    def altura_mm(self) -> float:
        return (self.caixa[3] - self.caixa[1]) / PONTOS_PDF_POR_MM

    def dicionario(self, indice: int) -> dict:
        medida = f"{self.largura_mm:.1f} × {self.altura_mm:.1f} mm".replace(".", ",")
        if self.origem == "moldura":
            descricao = f"Etiqueta da página {self.pagina + 1} · {medida}"
        else:
            descricao = f"Página {self.pagina + 1} inteira · {medida}"
            if not self.sugerido:
                descricao += " · sem moldura de etiqueta (lista de conteúdo?)"
        return {
            "indice": indice,
            "pagina": self.pagina,
            "origem": self.origem,
            "sugerido": self.sugerido,
            "largura_mm": round(self.largura_mm, 1),
            "altura_mm": round(self.altura_mm, 1),
            "descricao": descricao,
        }


@dataclass(slots=True)
class Documento:
    id: str
    nome: str
    dados: bytes
    paginas: int
    recortes: list[Recorte]

    def sugeridos(self) -> list[int]:
        return [i for i, r in enumerate(self.recortes) if r.sugerido]

    def dicionario(self) -> dict:
        return {
            "id": self.id,
            "nome": self.nome,
            "paginas": self.paginas,
            "recortes": [r.dicionario(i) for i, r in enumerate(self.recortes)],
            "sugeridos": self.sugeridos(),
        }


@dataclass(slots=True)
class Resultado:
    imagem: Image.Image
    escala: float  # 1.0 = tamanho original do PDF
    girada: bool  # se foi girada 90° para caber melhor
    avisos: list[str]


# --------------------------------------------------------------------------- #
# Abrir e achar as etiquetas
# --------------------------------------------------------------------------- #


def _carregar(dados: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(dados)
    except pdfium.PdfiumError as exc:
        if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
            raise PdfInvalido(
                "o PDF está protegido por senha — baixe a etiqueta de novo sem senha"
            ) from exc
        raise PdfInvalido(f"não consegui ler o PDF: {exc}") from exc


def abrir(dados: bytes, nome: str = "") -> Documento:
    """Lê o PDF e acha as etiquetas. Não guarda nada em disco."""
    if not dados:
        raise PdfInvalido("o arquivo está vazio")
    if len(dados) > TAMANHO_MAXIMO:
        raise PdfInvalido(
            f"o PDF tem {len(dados) / 1024 / 1024:.0f} MB; o limite é "
            f"{TAMANHO_MAXIMO // 1024 // 1024} MB"
        )
    if b"%PDF" not in dados[:1024]:
        raise PdfInvalido("o arquivo não é um PDF")

    with _TRAVA:
        documento = _carregar(dados)
        try:
            paginas = len(documento)
            recortes: list[Recorte] = []
            for numero in range(paginas):
                pagina = documento[numero]
                try:
                    recortes += _recortes_da_pagina(pagina, numero)
                finally:
                    pagina.close()
        finally:
            documento.close()

    if not recortes:
        raise PdfInvalido("o PDF não tem nada desenhado")

    # Achou moldura em alguma página: as páginas sem moldura são anexo (a lista
    # de conteúdo do pedido), não etiqueta. Ficam disponíveis, mas desmarcadas.
    if any(r.origem == "moldura" for r in recortes):
        recortes = [
            r if r.origem == "moldura" else _com_sugestao(r, False) for r in recortes
        ]

    return Documento(
        id=hashlib.sha256(dados).hexdigest()[:16],
        nome=nome or "etiqueta.pdf",
        dados=dados,
        paginas=paginas,
        recortes=recortes,
    )


def _com_sugestao(recorte: Recorte, sugerido: bool) -> Recorte:
    return Recorte(recorte.pagina, recorte.caixa, recorte.origem, sugerido)


def _recortes_da_pagina(pagina: pdfium.PdfPage, numero: int) -> list[Recorte]:
    # Trabalha no espaço da página sem o /Rotate: as caixas dos objetos estão
    # nele. A rotação da página volta na hora de desenhar.
    if pagina.get_rotation():
        pagina.set_rotation(0)
    limites = pagina.get_cropbox()
    tinta = _mapa_de_tinta(pagina)
    escala = _PONTOS_POR_MM_ANALISE / PONTOS_PDF_POR_MM  # pixels por ponto PDF

    molduras: list[tuple[float, float, float, float]] = []
    for objeto in pagina.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH], max_depth=0):
        caixa = tuple(round(v, 1) for v in objeto.get_bounds())
        if (caixa not in molduras  # a mesma caixa desenhada duas vezes
                and _parece_moldura(caixa, limites)
                and _borda_tem_tinta(tinta, caixa, limites, escala)):
            molduras.append(caixa)
    # Moldura dentro de moldura é enfeite da etiqueta, não outra etiqueta.
    externas = [
        c for c in molduras
        if not any(o != c and _contem(o, c) for o in molduras)
    ]
    if externas:
        folga = FOLGA_MOLDURA_MM * PONTOS_PDF_POR_MM
        # Ordem de leitura: de cima para baixo, da esquerda para a direita.
        externas.sort(key=lambda c: (-round(c[3] / (20 * PONTOS_PDF_POR_MM)), c[0]))
        return [
            Recorte(numero, _limitar(_expandir(c, folga), limites), "moldura")
            for c in externas
        ]

    caixa_tinta = tinta.getbbox()
    if caixa_tinta is None:
        return []  # página em branco
    x0, y0, _x1, y1 = limites
    esquerda, topo, direita, baixo = caixa_tinta
    caixa = (
        x0 + esquerda / escala,
        y1 - baixo / escala,
        x0 + direita / escala,
        y1 - topo / escala,
    )
    folga = FOLGA_CONTEUDO_MM * PONTOS_PDF_POR_MM
    return [Recorte(numero, _limitar(_expandir(caixa, folga), limites), "pagina")]


def _mapa_de_tinta(pagina: pdfium.PdfPage) -> Image.Image:
    """A página em baixa resolução: 255 onde há tinta, 0 no branco."""
    escala = _PONTOS_POR_MM_ANALISE / PONTOS_PDF_POR_MM
    bitmap = pagina.render(scale=escala, grayscale=True, fill_color=(255, 255, 255, 255))
    cinza = bitmap.to_pil().convert("L")
    return cinza.point(lambda v: 255 if v < _TINTA else 0, mode="L")


def _parece_moldura(caixa: tuple, limites: tuple) -> bool:
    largura = (caixa[2] - caixa[0]) / PONTOS_PDF_POR_MM
    altura = (caixa[3] - caixa[1]) / PONTOS_PDF_POR_MM
    if min(largura, altura) < MOLDURA_MINIMA_MM or max(largura, altura) > MOLDURA_MAXIMA_MM:
        return False
    # Do tamanho da página inteira é borda de página, não etiqueta.
    pagina_l = (limites[2] - limites[0]) / PONTOS_PDF_POR_MM
    pagina_a = (limites[3] - limites[1]) / PONTOS_PDF_POR_MM
    return not (largura > pagina_l * 0.9 and altura > pagina_a * 0.9)


def _borda_tem_tinta(tinta: Image.Image, caixa: tuple, limites: tuple, escala: float) -> bool:
    """Os quatro lados da caixa estão desenhados? Fundo branco não está."""
    x0, _y0, _x1, y1 = limites
    esquerda = (caixa[0] - x0) * escala
    direita = (caixa[2] - x0) * escala
    topo = (y1 - caixa[3]) * escala
    baixo = (y1 - caixa[1]) * escala
    amostras = 40

    def tem_tinta_perto(x: float, y: float) -> bool:
        cx, cy = int(round(x)), int(round(y))
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                px, py = cx + dx, cy + dy
                if 0 <= px < tinta.width and 0 <= py < tinta.height and tinta.getpixel((px, py)):
                    return True
        return False

    def lado(pontos: list[tuple[float, float]]) -> bool:
        com_tinta = sum(tem_tinta_perto(x, y) for x, y in pontos)
        return com_tinta >= _BORDA_COM_TINTA * len(pontos)

    passos = [i / (amostras - 1) for i in range(amostras)]
    horizontal = [esquerda + (direita - esquerda) * t for t in passos]
    vertical = [topo + (baixo - topo) * t for t in passos]
    return (
        lado([(x, topo) for x in horizontal])
        and lado([(x, baixo) for x in horizontal])
        and lado([(esquerda, y) for y in vertical])
        and lado([(direita, y) for y in vertical])
    )


def _contem(externa: tuple, interna: tuple, tolerancia: float = 1.0) -> bool:
    return (
        externa[0] - tolerancia <= interna[0]
        and externa[1] - tolerancia <= interna[1]
        and interna[2] <= externa[2] + tolerancia
        and interna[3] <= externa[3] + tolerancia
    )


def _expandir(caixa: tuple, folga: float) -> tuple[float, float, float, float]:
    return (caixa[0] - folga, caixa[1] - folga, caixa[2] + folga, caixa[3] + folga)


def _limitar(caixa: tuple, limites: tuple) -> tuple[float, float, float, float]:
    return (
        max(caixa[0], limites[0]),
        max(caixa[1], limites[1]),
        min(caixa[2], limites[2]),
        min(caixa[3], limites[3]),
    )


# --------------------------------------------------------------------------- #
# Desenhar uma etiqueta no tamanho do formato
# --------------------------------------------------------------------------- #

_GIRO = {
    90: Image.Transpose.ROTATE_270,  # o Pillow gira no sentido anti-horário
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def desenhar(
    documento: Documento,
    indice: int,
    *,
    largura_mm: float,
    altura_mm: float,
    dpmm: int = 8,
    margem_mm: float = 0.0,
    rotacao: int = 0,
    bitonal: bool = False,
) -> Resultado:
    """Um recorte do PDF, encaixado numa etiqueta de ``largura_mm × altura_mm``.

    É esta função que gera a prévia **e** o bitmap que vai para a impressora.
    ``rotacao`` gira o conteúdo dentro da etiqueta (no sentido horário); a
    etiqueta continua do tamanho do formato.
    """
    recorte = documento.recortes[indice]
    largura = max(1, int(round(largura_mm * dpmm)))
    altura = max(1, int(round(altura_mm * dpmm)))
    margem = max(0, int(round(margem_mm * dpmm)))
    util_l = max(1, largura - 2 * margem)
    util_a = max(1, altura - 2 * margem)

    with _TRAVA:
        pdf = _carregar(documento.dados)
        try:
            pagina = pdf[recorte.pagina]
            try:
                giro_da_pagina = pagina.get_rotation()
                if giro_da_pagina:
                    pagina.set_rotation(0)
                giro = (giro_da_pagina + rotacao) % 360

                # Tamanho do conteúdo, em pontos da impressora, já girado.
                conteudo_l = recorte.largura_mm * dpmm
                conteudo_a = recorte.altura_mm * dpmm
                if giro in (90, 270):
                    conteudo_l, conteudo_a = conteudo_a, conteudo_l
                escala = min(util_l / conteudo_l, util_a / conteudo_a)
                deitada = min(util_l / conteudo_a, util_a / conteudo_l)
                girada = deitada > escala * GIRAR_SE_MELHORA
                if girada:
                    giro = (giro + 90) % 360
                    escala = deitada

                x0, y0, x1, y1 = pagina.get_cropbox()
                esquerda, baixo, direita, cima = recorte.caixa
                bitmap = pagina.render(
                    scale=escala * dpmm / PONTOS_PDF_POR_MM,
                    crop=(esquerda - x0, baixo - y0, x1 - direita, y1 - cima),
                    grayscale=True,
                    fill_color=(255, 255, 255, 255),
                )
                desenho = bitmap.to_pil().convert("L").copy()
            finally:
                pagina.close()
        finally:
            pdf.close()

    desenho = _resgatar_linhas_finas(desenho)
    if giro:
        desenho = desenho.transpose(_GIRO[giro])

    etiqueta = Image.new("L", (largura, altura), 255)
    etiqueta.paste(desenho, ((largura - desenho.width) // 2, (altura - desenho.height) // 2))
    if bitonal:
        etiqueta = etiqueta.point(lambda v: 0 if v < LIMIAR else 255, mode="L")

    avisos = []
    if girada:
        avisos.append(
            f"{_nome(recorte)} foi girada 90° para caber melhor em "
            f"{largura_mm:g} × {altura_mm:g} mm"
        )
    if escala < ESCALA_PARA_AVISAR:
        avisos.append(
            f"{_nome(recorte)} tem {recorte.largura_mm:.1f} × {recorte.altura_mm:.1f} mm "
            f"e foi reduzida a {escala:.0%} para caber em {largura_mm:g} × "
            f"{altura_mm:g} mm — confira se o código de barras ainda é lido, ou "
            "escolha um formato do tamanho da etiqueta"
        )
    return Resultado(imagem=etiqueta, escala=escala, girada=girada, avisos=avisos)


def _resgatar_linhas_finas(cinza: Image.Image) -> Image.Image:
    """Ponto com tinta que sumiria sozinho no preto e branco vira preto.

    A moldura da etiqueta do Mercado Livre é um anel preenchido de ~0,3 pt —
    menos de um ponto do cabeçote. Conforme cai entre dois pontos, a
    suavização a espalha num cinza claro (145–155, medido) e o corte de 128 a
    apaga: a linha de cima da moldura sumia. Sem suavização o PDFium também a
    descarta. É o "largura mínima de linha" que todo driver de impressão tem.

    A regra só pega o que sumiria **por inteiro**: ponto com pelo menos 20% de
    tinta, sem nenhum vizinho que já vai sair preto e com branco ao lado. Borda
    de barra e de letra sempre tem vizinho preto — então barras, QR e texto
    saem exatamente como sairiam sem isto.
    """
    vai_sair_preto = cinza.point(lambda v: 255 if v < LIMIAR else 0, mode="L")
    perto_de_preto = vai_sair_preto.filter(ImageFilter.MaxFilter(3))
    com_tinta = cinza.point(lambda v: 255 if v <= _RESGATE else 0, mode="L")
    perto_de_branco = cinza.point(lambda v: 255 if v >= _TINTA else 0, mode="L").filter(
        ImageFilter.MaxFilter(3)
    )
    resgatar = ImageChops.multiply(
        ImageChops.multiply(com_tinta, ImageChops.invert(perto_de_preto)), perto_de_branco
    )
    # Escurece o bastante para cair abaixo do corte — e o preview em cinza
    # mostra o mesmo que o papel vai mostrar.
    return ImageChops.darker(cinza, resgatar.point(lambda v: _RESGATADO if v else 255))


def _nome(recorte: Recorte) -> str:
    if recorte.origem == "moldura":
        return f"a etiqueta da página {recorte.pagina + 1}"
    return f"a página {recorte.pagina + 1}"


__all__ = [
    "PdfInvalido",
    "Documento",
    "Recorte",
    "Resultado",
    "abrir",
    "desenhar",
]
