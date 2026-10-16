"""A aplicação: FastAPI em 127.0.0.1 servindo a tela e falando com a impressora.

É um servidor local, não um site: escuta só no loopback e não pede login. O
navegador (ou a janela do ``desktop.py``) é apenas a interface — todo o trabalho
pesado (renderizar, compor a folha, mandar para a USB001) acontece aqui, em
Python, no mesmo processo.

As respostas de preview trazem a imagem **e** a geometria (tamanho da etiqueta,
colunas, espaço entre elas). É isso que deixa a tela desenhar a borda do papel e
a divisão das colunas por cima da imagem, sem sujar o bitmap que vai imprimir.
"""

from __future__ import annotations

import base64
import binascii
import io
import sys
import threading
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from eleva_impressao import ajuste, formatos, graficos, pdf, preferencias, render, windows

VERSAO = "1.1"
TITULO = "Eleva Impressão"
LABELARY_URL = "https://api.labelary.com/v1/printers/{dpmm}dpmm/labels/{largura}x{altura}/{indice}/"

ZPL_EXEMPLO = """^XA
^CI28
^FX Exemplo — troque por uma etiqueta de verdade ou abra um arquivo .zpl
^FO40,40^A0N,58,0^FDEleva Impressão^FS
^FO40,110^GB720,4,4^FS

^FO40,140^A0N,44,0^FB720,3,6,L,0^FDPerfume Importado Água de Colônia Masculino 100 ml^FS
^FO40,300^A0N,34,0^FDSKU ELV-0421   ·   2 un.^FS

^FO40,380^BY3,3,140^BCN,140,Y,N,N^FD2000012345678^FS

^FO40,600^A0N,30,0^FDCorredor B · Prateleira 3^FS
^FO520,600^BQN,2,6^FDQA,https://eleva.com.br^FS
^XZ
"""


# --------------------------------------------------------------------------- #
# Corpo das requisições
# --------------------------------------------------------------------------- #


class Desenho(BaseModel):
    zpl: str = ""
    formato_id: str = formatos.PADRAO
    indice: int = 0
    rotacao: int = 0
    qualidade: str = "cinza"  # 'cinza' (com antialias) ou 'bitonal' (o do papel)
    motor: str = "local"  # 'local' ou 'labelary'
    visao: str = "etiqueta"  # 'etiqueta' ou 'folha'
    # Sobrescritas para quem quer testar uma medida antes de salvar o formato:
    largura_mm: float | None = None
    altura_mm: float | None = None
    dpmm: int | None = None
    colunas: int | None = None
    espaco_colunas_mm: float | None = None
    colunas_no_zpl: bool | None = None
    # PDF aberto pela tela (o ``id`` que /api/pdf/abrir devolveu). Com ele, o
    # campo ``zpl`` é ignorado e ``indice`` conta só as etiquetas marcadas.
    pdf_id: str = ""
    # Quais recortes do PDF entram, pelos índices de /api/pdf/abrir. ``None``
    # usa os sugeridos (as etiquetas com moldura, sem a lista de conteúdo).
    pdf_recortes: list[int] | None = None


class PedidoImpressao(Desenho):
    modo: str = windows.Modo.IMAGEM.value
    impressora: str | None = None
    copias: int = Field(default=1, ge=1, le=99)
    apenas_esta: bool = False  # imprime só a etiqueta em foco
    gap_mm: float = 2.0
    escuridao: int | None = None
    # Segue mesmo com o papel do driver diferente do formato. É decisão de quem
    # está olhando a impressora, então mora num campo explícito.
    forcar: bool = False


class FormatoEntrada(BaseModel):
    id: str = ""
    nome: str
    largura_mm: float
    altura_mm: float
    dpmm: int = 8
    colunas: int = 1
    espaco_colunas_mm: float = 2.0
    margem_mm: float = 0.0
    colunas_no_zpl: bool = False
    margem_seguranca_mm: float = 0.0


class PreferenciaEntrada(BaseModel):
    impressora: str | None = None
    modo: str | None = None
    gap_mm: float | None = None
    formato_id: str | None = None
    formato_pdf_id: str | None = None


class PdfEntrada(BaseModel):
    nome: str = ""
    conteudo_base64: str


class ImagemEntrada(BaseModel):
    conteudo_base64: str
    x: int = 40
    y: int = 40
    largura_pontos: int | None = 400
    limiar: int = graficos.LIMIAR_PADRAO
    inverter: bool = False


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #


def recursos() -> Path:
    """A pasta ``web``, esteja o programa no repositório ou dentro do .exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "eleva_impressao_web"
    return Path(__file__).resolve().parent / "web"


def _formato_do_pedido(pedido: Desenho) -> formatos.Formato:
    try:
        formato = formatos.buscar(pedido.formato_id)
    except KeyError:
        formato = formatos.formato_padrao()
    mudancas = {
        campo: valor
        for campo, valor in (
            ("largura_mm", pedido.largura_mm),
            ("altura_mm", pedido.altura_mm),
            ("dpmm", pedido.dpmm),
            ("colunas", pedido.colunas),
            ("espaco_colunas_mm", pedido.espaco_colunas_mm),
            ("colunas_no_zpl", pedido.colunas_no_zpl),
        )
        if valor is not None
    }
    return replace(formato, **mudancas) if mudancas else formato


def _png(imagem: Image.Image) -> bytes:
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _base64_png(imagem: Image.Image) -> str:
    return "data:image/png;base64," + base64.b64encode(_png(imagem)).decode("ascii")


async def _labelary(zpl: str, formato: formatos.Formato, indice: int, rotacao: int,
                    qualidade: str) -> tuple[Image.Image, list[str]]:
    """Segundo motor: a referência online, para conferir fidelidade."""
    largura_pol = round(formato.largura_desenho_mm / 25.4, 4)
    altura_pol = formato.polegadas[1]
    url = LABELARY_URL.format(
        dpmm=formato.dpmm, largura=largura_pol, altura=altura_pol, indice=max(0, indice)
    )
    cabecalhos = {
        "Accept": "image/png",
        "X-Linter": "On",
        "X-Quality": "bitonal" if qualidade == "bitonal" else "grayscale",
    }
    if rotacao:
        cabecalhos["X-Rotation"] = str(rotacao % 360)
    async with httpx.AsyncClient(timeout=30) as cliente:
        try:
            resposta = await cliente.post(url, content=zpl.encode("utf-8"), headers=cabecalhos)
        except httpx.HTTPError as exc:
            raise HTTPException(
                503, f"não consegui falar com o Labelary ({exc}). O motor local funciona offline."
            ) from exc
    if resposta.status_code >= 400:
        raise HTTPException(400, f"o Labelary recusou o ZPL: {resposta.text[:400]}")

    avisos = []
    for linha in (resposta.headers.get("X-Warnings") or "").split("\\"):
        partes = linha.split("|")
        if len(partes) >= 5:
            avisos.append(f"linha {partes[1]}, {partes[2]}: {partes[4]}")
        elif linha.strip():
            avisos.append(linha.strip())
    return Image.open(io.BytesIO(resposta.content)).convert("L"), avisos


def _desenhar_uma(
    zpl: str, formato: formatos.Formato, indice: int, *, rotacao: int = 0,
    bitonal: bool = False,
) -> render.Resultado:
    """Uma etiqueta do arquivo, na largura de desenho que o formato manda."""
    return render.renderizar(
        zpl, largura_mm=formato.largura_desenho_mm, altura_mm=formato.altura_mm,
        dpmm=formato.dpmm, indice=indice, rotacao=rotacao, bitonal=bitonal,
    )


def _avisos_de(resultado: render.Resultado, formato: formatos.Formato) -> list[str]:
    avisos = list(resultado.avisos)
    avisos += [f"comando não desenhado: {c}" for c in resultado.comandos_ignorados]
    conselho = _diagnostico_de_largura(resultado, formato)
    if conselho:
        avisos.insert(0, conselho)
    return avisos


def _diagnostico_de_largura(
    resultado: render.Resultado, formato: formatos.Formato
) -> str | None:
    """Transforma "caiu fora da etiqueta" em uma instrução do que fazer.

    O caso que motivou isto: o ZPL de duas vias do Mercado Livre traz as duas
    colunas dentro de **um** ``^XA``. Desenhado com a largura de uma etiqueta
    só, a coluna da direita some — e o aviso genérico não ajudava ninguém a
    entender por quê.
    """
    usado = resultado.extensao_pontos[0]
    if usado <= resultado.largura_pontos + 2:
        return None

    sobra_mm = round((usado - resultado.largura_pontos) / max(1, formato.dpmm), 1)
    if not formato.colunas_no_zpl and usado <= int(
        round(formato.largura_total_mm * formato.dpmm)
    ) + 8:
        return (
            f"este ZPL desenha até {round(usado / formato.dpmm, 1)} mm, mas o formato "
            f"desenha só {formato.largura_desenho_mm:g} mm: ele já traz as colunas "
            "dentro de uma etiqueta só. Escolha um formato marcado como "
            "'o ZPL já traz as colunas' — o programa não deve montar o par, o "
            "arquivo já vem montado."
        )
    return (
        f"o ZPL desenha {sobra_mm} mm além da largura do formato "
        f"({formato.largura_desenho_mm:g} mm) e esse pedaço não sai no papel"
    )


def _renderizar_todas(
    zpl: str, formato: formatos.Formato, *, rotacao: int = 0, bitonal: bool = False
) -> tuple[list[Image.Image], list[str]]:
    imagens: list[Image.Image] = []
    avisos: list[str] = []
    for i in range(render.contar_etiquetas(zpl)):
        resultado = _desenhar_uma(zpl, formato, i, rotacao=rotacao, bitonal=bitonal)
        imagens.append(resultado.imagem)
        for aviso in _avisos_de(resultado, formato):
            if aviso not in avisos:
                avisos.append(aviso)
    return imagens, avisos


def _folhas(zpl: str, formato: formatos.Formato, *, rotacao: int = 0,
            bitonal: bool = False) -> tuple[list[Image.Image], list[str]]:
    imagens, avisos = _renderizar_todas(zpl, formato, rotacao=rotacao, bitonal=bitonal)
    if formato.colunas_no_zpl:
        # O arquivo já vem com a folha montada: compor de novo duplicaria tudo.
        return imagens, avisos
    return _montar(imagens, formato), avisos


def _montar(imagens: list[Image.Image], formato: formatos.Formato) -> list[Image.Image]:
    return render.compor(
        imagens, colunas=formato.colunas,
        espaco_pontos=formato.espaco_pontos, margem_pontos=formato.margem_pontos,
    )


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

# Os PDFs abertos ficam em memória, nunca em disco: a etiqueta traz nome e
# endereço do comprador. Guarda os últimos, para a tela redesenhar sem mandar o
# arquivo de novo a cada clique.
_PDFS: OrderedDict[str, pdf.Documento] = OrderedDict()
_PDFS_GUARDADOS = 8
_PDFS_TRAVA = threading.Lock()


def _guardar_pdf(documento: pdf.Documento) -> None:
    with _PDFS_TRAVA:
        _PDFS[documento.id] = documento
        _PDFS.move_to_end(documento.id)
        while len(_PDFS) > _PDFS_GUARDADOS:
            _PDFS.popitem(last=False)


def _pdf_do_pedido(pedido: Desenho) -> pdf.Documento:
    with _PDFS_TRAVA:
        documento = _PDFS.get(pedido.pdf_id)
    if documento is None:
        # O programa foi reaberto e a tela ainda lembra do PDF anterior.
        raise HTTPException(410, "o PDF não está mais aberto — abra o arquivo de novo")
    return documento


def _selecao_pdf(documento: pdf.Documento, pedido: Desenho) -> list[int]:
    """Os recortes que entram, na ordem do arquivo."""
    if pedido.pdf_recortes is None:
        selecao = documento.sugeridos()
    else:
        validos = range(len(documento.recortes))
        selecao = sorted({i for i in pedido.pdf_recortes if i in validos})
    if not selecao:
        raise HTTPException(400, "nenhuma etiqueta do PDF está marcada para imprimir")
    return selecao


def _desenhar_pdf(
    documento: pdf.Documento, recorte: int, formato: formatos.Formato, *,
    rotacao: int = 0, bitonal: bool = False,
) -> pdf.Resultado:
    """Uma etiqueta do PDF, na tela de desenho do formato — como o ZPL."""
    return pdf.desenhar(
        documento, recorte,
        largura_mm=formato.largura_desenho_mm, altura_mm=formato.altura_mm,
        dpmm=formato.dpmm, margem_mm=formato.margem_seguranca_mm,
        rotacao=rotacao, bitonal=bitonal,
    )


def _folhas_pdf(
    documento: pdf.Documento, selecao: list[int], formato: formatos.Formato, *,
    rotacao: int = 0, bitonal: bool = False,
) -> tuple[list[Image.Image], list[str]]:
    imagens: list[Image.Image] = []
    avisos: list[str] = []
    for recorte in selecao:
        resultado = _desenhar_pdf(
            documento, recorte, formato, rotacao=rotacao, bitonal=bitonal
        )
        imagens.append(resultado.imagem)
        avisos += [a for a in resultado.avisos if a not in avisos]
    if formato.colunas_no_zpl:
        return imagens, avisos
    return _montar(imagens, formato), avisos


def _zpl_de_folhas(folhas: list[Image.Image], copias: int) -> str:
    """O bitmap de cada folha como ``^GF`` — o PDF no modo "ZPL cru".

    Não é tradução do PDF para comandos: é o mesmo bitmap da prévia, embrulhado
    em ZPL para o firmware que só fala ZPL.
    """
    partes = []
    for folha in folhas:
        preto_e_branco = folha.point(lambda v: 0 if v < 128 else 255, mode="L")
        partes.append(
            f"^XA^PW{folha.width}^LL{folha.height}^LH0,0"
            f"^FO0,0{graficos.para_gf(preto_e_branco)}^FS^PQ{max(1, copias)}^XZ"
        )
    return "\n".join(partes)


# --------------------------------------------------------------------------- #
# Aplicação
# --------------------------------------------------------------------------- #


def criar_app() -> FastAPI:
    app = FastAPI(title=TITULO, version=VERSAO, docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def raiz() -> HTMLResponse:
        return HTMLResponse((recursos() / "index.html").read_text(encoding="utf-8"))

    @app.get("/app.js")
    def script() -> Response:
        return Response(
            (recursos() / "app.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/estilo.css")
    def estilo() -> Response:
        return Response(
            (recursos() / "estilo.css").read_text(encoding="utf-8"), media_type="text/css"
        )

    @app.get("/logo.png")
    def logo() -> Response:
        return Response(_logo_bytes(), media_type="image/png")

    # -- estado geral ----------------------------------------------------- #

    @app.get("/api/estado")
    def estado() -> dict:
        try:
            impressoras = [i.dicionario() for i in windows.listar()]
            erro_impressora = None
        except windows.ImpressoraIndisponivel as exc:
            impressoras, erro_impressora = [], str(exc)
        return {
            "versao": VERSAO,
            "formatos": [f.dicionario() for f in formatos.carregar()],
            "formato_padrao": formatos.PADRAO,
            "formato_pdf_padrao": formatos.PADRAO_PDF,
            "impressoras": impressoras,
            "erro_impressora": erro_impressora,
            "modos": [m.value for m in windows.Modo],
            "densidades": formatos.DPMM_VALIDOS,
            "largura_maxima_mm": formatos.LARGURA_MAXIMA_MM,
            "zpl_exemplo": ZPL_EXEMPLO,
            "arquivo_formatos": str(formatos.caminho()),
            "preferencias": preferencias.carregar().dicionario(),
        }

    @app.get("/api/impressora")
    def impressora(nome: str | None = None) -> dict:
        try:
            return windows.estado(nome)
        except windows.ImpressoraIndisponivel as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - o spooler é imprevisível
            raise HTTPException(500, f"não consegui ler o estado: {exc}") from exc

    @app.post("/api/diagnostico")
    def diagnostico(pedido: PedidoImpressao) -> dict:
        """Compara o papel configurado no driver com o formato escolhido."""
        formato = _formato_do_pedido(pedido)
        try:
            resultado = windows.diagnostico(
                pedido.impressora,
                largura_mm=formato.largura_total_mm,
                altura_mm=formato.altura_mm,
            )
        except windows.ImpressoraIndisponivel as exc:
            raise HTTPException(503, str(exc)) from exc
        resultado["formato"] = formato.descricao
        resultado["esperado_pontos"] = [
            int(round(formato.largura_total_mm * formato.dpmm)), formato.altura_pontos
        ]
        return resultado

    # -- desenho ----------------------------------------------------------- #

    @app.post("/api/pdf/abrir")
    def abrir_pdf(entrada: PdfEntrada) -> dict:
        """Recebe o PDF da tela, acha as etiquetas e guarda em memória."""
        try:
            dados = base64.b64decode(entrada.conteudo_base64.split(",")[-1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(400, "o arquivo chegou corrompido; tente abrir de novo") from exc
        try:
            documento = pdf.abrir(dados, entrada.nome)
        except pdf.PdfInvalido as exc:
            raise HTTPException(400, str(exc)) from exc
        _guardar_pdf(documento)
        return documento.dicionario()

    @app.post("/api/preview")
    async def preview(pedido: Desenho) -> JSONResponse:
        if pedido.pdf_id:
            # Desenhar PDF é trabalho de CPU: fora do laço de eventos.
            return JSONResponse(await run_in_threadpool(_preview_pdf, pedido))
        zpl = pedido.zpl or ""
        if not zpl.strip():
            raise HTTPException(400, "não há ZPL para desenhar")
        formato = _formato_do_pedido(pedido)
        total = render.contar_etiquetas(zpl)
        indice = max(0, min(pedido.indice, total - 1))
        bitonal = pedido.qualidade == "bitonal"

        if pedido.visao == "folha":
            folhas, avisos = _folhas(zpl, formato, rotacao=pedido.rotacao, bitonal=bitonal)
            por_folha = 1 if formato.colunas_no_zpl else max(1, formato.colunas)
            pagina = min(indice // por_folha, max(0, len(folhas) - 1))
            imagem = folhas[pagina] if folhas else Image.new("L", (10, 10), 255)
            return JSONResponse({
                "imagem": _base64_png(imagem),
                "total": total,
                "indice": indice,
                "folhas": len(folhas),
                "folha_atual": pagina,
                "avisos": avisos,
                "motor": "local",
                "geometria": _geometria(formato, imagem),
            })

        if pedido.motor == "labelary":
            imagem, avisos = await _labelary(
                zpl, formato, indice, pedido.rotacao, pedido.qualidade
            )
            motor = "labelary"
        else:
            resultado = _desenhar_uma(
                zpl, formato, indice, rotacao=pedido.rotacao, bitonal=bitonal
            )
            imagem = resultado.imagem
            avisos = _avisos_de(resultado, formato)
            motor = "local"

        return JSONResponse({
            "imagem": _base64_png(imagem),
            "total": total,
            "indice": indice,
            "folhas": 1,
            "folha_atual": 0,
            "avisos": avisos,
            "motor": motor,
            "geometria": _geometria(
                formato, imagem, uma_etiqueta=not formato.colunas_no_zpl
            ),
        })

    @app.post("/api/ajustar")
    def ajustar(pedido: Desenho) -> dict:
        """Reposiciona os campos para caberem na etiqueta, sem tocar nos dados."""
        if pedido.pdf_id:
            raise HTTPException(
                400, "o ajuste automático é para ZPL — o PDF já é encaixado sozinho"
            )
        formato = _formato_do_pedido(pedido)
        try:
            resultado = ajuste.ajustar(pedido.zpl, formato)
        except ajuste.NaoAjustavel as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "zpl": resultado.zpl,
            "mudou": resultado.mudou,
            "mudancas": resultado.mudancas,
            "avisos": resultado.avisos,
        }

    @app.post("/api/png")
    def baixar_png(pedido: Desenho) -> Response:
        formato = _formato_do_pedido(pedido)
        bitonal = pedido.qualidade == "bitonal"
        if pedido.pdf_id:
            documento = _pdf_do_pedido(pedido)
            selecao = _selecao_pdf(documento, pedido)
            recorte = selecao[max(0, min(pedido.indice, len(selecao) - 1))]
            imagem = _desenhar_pdf(
                documento, recorte, formato, rotacao=pedido.rotacao, bitonal=bitonal
            ).imagem
        else:
            imagem = _desenhar_uma(
                pedido.zpl, formato, pedido.indice, rotacao=pedido.rotacao, bitonal=bitonal
            ).imagem
        return Response(
            _png(imagem),
            media_type="image/png",
            headers={"Content-Disposition": 'attachment; filename="etiqueta.png"'},
        )

    @app.post("/api/pdf")
    def baixar_pdf(pedido: Desenho) -> Response:
        formato = _formato_do_pedido(pedido)
        if pedido.pdf_id:
            documento = _pdf_do_pedido(pedido)
            folhas, _ = _folhas_pdf(
                documento, _selecao_pdf(documento, pedido), formato,
                rotacao=pedido.rotacao, bitonal=True,
            )
        else:
            folhas, _ = _folhas(pedido.zpl, formato, rotacao=pedido.rotacao, bitonal=True)
        if not folhas:
            raise HTTPException(400, "não há etiqueta para gerar o PDF")
        buffer = io.BytesIO()
        dpi = formato.dpmm * 25.4
        folhas[0].convert("RGB").save(
            buffer, format="PDF", resolution=dpi, save_all=True,
            append_images=[f.convert("RGB") for f in folhas[1:]],
        )
        return Response(
            buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="etiquetas.pdf"'},
        )

    @app.post("/api/imagem")
    def inserir_imagem(entrada: ImagemEntrada) -> dict:
        try:
            bruto = base64.b64decode(entrada.conteudo_base64.split(",")[-1])
            imagem = Image.open(io.BytesIO(bruto))
            imagem.load()
        except Exception as exc:  # noqa: BLE001 - qualquer arquivo pode chegar aqui
            raise HTTPException(400, f"não consegui ler a imagem: {exc}") from exc
        campo = graficos.campo_de_imagem(
            imagem, entrada.x, entrada.y,
            largura_pontos=entrada.largura_pontos, limiar=entrada.limiar,
            inverter=entrada.inverter,
        )
        return {"zpl": campo, "bytes": len(campo)}

    # -- formatos ---------------------------------------------------------- #

    @app.post("/api/formatos")
    def salvar_formato(entrada: FormatoEntrada) -> dict:
        try:
            salvo = formatos.salvar(formatos.Formato(**entrada.model_dump()))
        except formatos.FormatoInvalido as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"formato": salvo.dicionario(),
                "formatos": [f.dicionario() for f in formatos.carregar()]}

    @app.post("/api/formatos/{formato_id}/duplicar")
    def duplicar_formato(formato_id: str) -> dict:
        try:
            copia = formatos.duplicar(formato_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"formato": copia.dicionario(),
                "formatos": [f.dicionario() for f in formatos.carregar()]}

    @app.delete("/api/formatos/{formato_id}")
    def apagar_formato(formato_id: str) -> dict:
        try:
            formatos.apagar(formato_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except formatos.FormatoInvalido as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"formatos": [f.dicionario() for f in formatos.carregar()]}

    @app.post("/api/preferencias")
    def salvar_preferencias(entrada: PreferenciaEntrada) -> dict:
        """A tela avisa o que a operadora escolheu; aqui só se guarda."""
        return preferencias.lembrar(
            impressora=entrada.impressora or "",
            modo=entrada.modo or "",
            gap_mm=entrada.gap_mm,
            formato_id=entrada.formato_id or "",
            formato_pdf_id=entrada.formato_pdf_id or "",
        ).dicionario()

    # -- impressão --------------------------------------------------------- #

    @app.post("/api/imprimir")
    def imprimir(pedido: PedidoImpressao) -> dict:
        if not pedido.pdf_id and not (pedido.zpl or "").strip():
            raise HTTPException(400, "não há ZPL para imprimir")
        formato = _formato_do_pedido(pedido)
        try:
            return _executar_impressao(pedido, formato)
        except windows.ImpressoraIndisponivel as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/teste")
    def teste(pedido: PedidoImpressao) -> dict:
        """Etiqueta de teste no formato escolhido — o jeito de achar o modo certo."""
        formato = _formato_do_pedido(pedido)
        copia = pedido.model_copy(update={
            "zpl": zpl_de_teste(formato, pedido.modo),
            "pdf_id": "",
            "apenas_esta": False,
            "copias": 1,
        })
        try:
            return _executar_impressao(copia, formato)
        except windows.ImpressoraIndisponivel as exc:
            raise HTTPException(503, str(exc)) from exc

    return app


def _conferir_papel(formato: formatos.Formato, impressora: str | None) -> str | None:
    """O papel do driver bate com o formato? Só vale para o modo imagem.

    No modo imagem quem manda no tamanho da página é o Windows. Se o driver
    estiver em 100 × 150 mm e o formato for de 25 mm de altura, a impressora
    avança os 150 mm inteiros: sai uma etiqueta impressa e cinco em branco, e a
    tira vai para o lixo. Foi o que aconteceu no primeiro teste de duas colunas.

    O TSPL não passa por aqui porque manda ``SIZE`` e ``GAP`` no próprio pacote
    — ele não depende de como o driver está configurado.
    """
    try:
        info = windows.diagnostico(
            impressora, largura_mm=formato.largura_total_mm, altura_mm=formato.altura_mm
        )
    except (windows.ImpressoraIndisponivel, OSError):
        return None  # sem diagnóstico, não é hora de barrar a impressão
    if not info["avisos"]:
        return None

    papel = info["papel_mm"]
    return (
        f"o papel configurado no Windows para a impressora {info['impressora']} é de "
        f"{papel[0]:g} × {papel[1]:g} mm, e este formato precisa de "
        f"{formato.largura_total_mm:g} × {formato.altura_mm:g} mm. "
        "Assim a impressora avança o papel do tamanho errado e desperdiça etiqueta. "
        "Duas saídas: mude o tamanho do papel nas Preferências de Impressão do "
        "Windows, ou use o modo TSPL, que manda a medida junto e não depende do driver."
    )


def _executar_impressao(pedido: PedidoImpressao, formato: formatos.Formato) -> dict:
    modo = windows.Modo(pedido.modo)

    if modo is windows.Modo.IMAGEM and not pedido.forcar:
        impedimento = _conferir_papel(formato, pedido.impressora)
        if impedimento:
            return {
                "ok": False,
                "modo": modo.value,
                "impressora": pedido.impressora or "",
                "detalhe": impedimento,
                "bytes": 0,
                "paginas": 0,
                "avisos": [],
                "folhas": None,
                "precisa_confirmar": True,
            }

    if modo is windows.Modo.ZPL and not pedido.pdf_id:
        # Único caminho em que o ZPL vai inteiro, sem virar imagem.
        envio = windows.imprimir_zpl(
            pedido.zpl, impressora=pedido.impressora, copias=pedido.copias
        )
        if envio.ok:
            preferencias.lembrar(impressora=envio.impressora, modo=modo.value)
        return {**envio.dicionario(), "avisos": [], "folhas": None}

    # Bitonal sempre: o cabeçote térmico só sabe queimar ou não queimar.
    if pedido.pdf_id:
        documento = _pdf_do_pedido(pedido)
        selecao = _selecao_pdf(documento, pedido)
        if pedido.apenas_esta:
            selecao = [selecao[max(0, min(pedido.indice, len(selecao) - 1))]]
        folhas, avisos = _folhas_pdf(
            documento, selecao, formato, rotacao=pedido.rotacao, bitonal=True
        )
    else:
        zpl = pedido.zpl
        if pedido.apenas_esta:
            etiquetas = render.separar_etiquetas(zpl)
            indice = max(0, min(pedido.indice, len(etiquetas) - 1))
            zpl = etiquetas[indice]
        folhas, avisos = _folhas(zpl, formato, rotacao=pedido.rotacao, bitonal=True)
    if not folhas:
        raise HTTPException(400, "não há etiqueta para imprimir")

    if modo is windows.Modo.ZPL:
        # PDF em impressora que só fala ZPL: o mesmo bitmap, dentro de ^GF.
        envio = windows.imprimir_zpl(
            _zpl_de_folhas(folhas, pedido.copias), impressora=pedido.impressora
        )
    elif modo is windows.Modo.TSPL:
        envio = windows.imprimir_tspl(
            folhas, impressora=pedido.impressora,
            largura_mm=formato.largura_total_mm, altura_mm=formato.altura_mm,
            dpmm=formato.dpmm, gap_mm=pedido.gap_mm, copias=pedido.copias,
            escuridao=pedido.escuridao,
        )
    else:
        envio = windows.imprimir_imagem(
            folhas, impressora=pedido.impressora, dpmm=formato.dpmm, copias=pedido.copias
        )
    if envio.ok:
        # O modo que deu certo é o que a próxima abertura vai encontrar.
        preferencias.lembrar(
            impressora=envio.impressora, modo=modo.value, gap_mm=pedido.gap_mm
        )
    return {**envio.dicionario(), "avisos": avisos, "folhas": len(folhas)}


def _preview_pdf(pedido: Desenho) -> dict:
    """A prévia do PDF — o mesmo ``pdf.desenhar`` que gera o papel."""
    formato = _formato_do_pedido(pedido)
    documento = _pdf_do_pedido(pedido)
    selecao = _selecao_pdf(documento, pedido)
    total = len(selecao)
    indice = max(0, min(pedido.indice, total - 1))
    bitonal = pedido.qualidade == "bitonal"
    resultado = _desenhar_pdf(
        documento, selecao[indice], formato, rotacao=pedido.rotacao, bitonal=bitonal
    )
    info = {
        "escala": round(resultado.escala, 4),
        "girada": resultado.girada,
        "recorte": documento.recortes[selecao[indice]].dicionario(selecao[indice]),
    }

    if pedido.visao == "folha":
        folhas, avisos = _folhas_pdf(
            documento, selecao, formato, rotacao=pedido.rotacao, bitonal=bitonal
        )
        por_folha = 1 if formato.colunas_no_zpl else max(1, formato.colunas)
        pagina = min(indice // por_folha, max(0, len(folhas) - 1))
        imagem = folhas[pagina]
        return {
            "imagem": _base64_png(imagem), "total": total, "indice": indice,
            "folhas": len(folhas), "folha_atual": pagina, "avisos": avisos,
            "motor": "local", "geometria": _geometria(formato, imagem), "pdf": info,
        }

    return {
        "imagem": _base64_png(resultado.imagem), "total": total, "indice": indice,
        "folhas": 1, "folha_atual": 0, "avisos": resultado.avisos, "motor": "local",
        "geometria": _geometria(
            formato, resultado.imagem, uma_etiqueta=not formato.colunas_no_zpl
        ),
        "pdf": info,
    }


def _geometria(formato: formatos.Formato, imagem: Image.Image, *,
               uma_etiqueta: bool = False) -> dict:
    """Medidas para a tela desenhar a borda do papel por cima do preview."""
    return {
        "largura_pontos": imagem.width,
        "altura_pontos": imagem.height,
        "etiqueta_pontos": [formato.largura_pontos, formato.altura_pontos],
        "colunas": 1 if uma_etiqueta else formato.colunas,
        "espaco_pontos": 0 if uma_etiqueta else formato.espaco_pontos,
        "margem_pontos": 0 if uma_etiqueta else formato.margem_pontos,
        "dpmm": formato.dpmm,
        "largura_mm": round(imagem.width / formato.dpmm, 2),
        "altura_mm": round(imagem.height / formato.dpmm, 2),
    }


def zpl_de_teste(formato: formatos.Formato, modo: str) -> str:
    """Etiqueta de conferência: diz o formato, o modo e mostra régua e barras.

    A régua é o que denuncia escala errada: se os traços não caírem de 10 em
    10 mm na etiqueta impressa, a densidade configurada não é a da impressora.
    """
    largura = formato.largura_pontos
    altura = formato.altura_pontos
    margem = max(8, formato.dpmm * 2)
    dpmm = formato.dpmm
    corpo = max(14, min(int(altura * 0.10), int(largura * 0.11)))
    pequeno = max(10, int(corpo * 0.6))

    partes = [
        "^XA^CI28",
        f"^PW{largura}^LL{altura}^LH0,0",
        f"^FO{margem},{margem}^A0N,{corpo},0^FDEleva Impressao^FS",
        f"^FO{margem},{margem + corpo + 4}^A0N,{pequeno},0"
        f"^FD{formato.largura_mm:g}x{formato.altura_mm:g}mm · {dpmm}dpmm · {modo}^FS",
        # Moldura: se algum lado sumir, a etiqueta esta deslocada no papel.
        f"^FO2,2^GB{largura - 4},{altura - 4},2^FS",
    ]

    # Régua de 10 em 10 mm na borda de baixo.
    base = altura - margem - dpmm * 6
    for mm in range(0, int(formato.largura_mm) + 1, 10):
        x = mm * dpmm
        if x > largura - 4:
            break
        comprimento = dpmm * 5 if mm % 50 == 0 else dpmm * 3
        partes.append(f"^FO{max(2, x)},{base + (dpmm * 5 - comprimento)}^GB2,{comprimento},2^FS")

    espaco = altura - (margem + corpo + pequeno + 20) - (dpmm * 8)
    if espaco > dpmm * 12:
        altura_barra = min(int(espaco * 0.55), dpmm * 15)
        y = margem + corpo + pequeno + 16
        partes.append(
            f"^FO{margem},{y}^BY2,3,{altura_barra}^BCN,{altura_barra},Y,N,N^FDELEVA-TESTE^FS"
        )
    partes.append("^XZ")
    return "".join(partes)


def _logo_bytes() -> bytes:
    """A logo pronta para o cabeçalho: recortada e com o fundo transparente.

    O arquivo original é 821 × 683 quase todo branco, com a palavra ELEVA
    pequena no meio — reduzido a 26 px de altura, sumiria. E o fundo é branco
    **opaco**, então recortar pelo canal alfa não recorta nada: o recorte tem de
    ser pela tinta. A opacidade sai do escuro de cada ponto, o que de quebra
    apaga a franja colorida do texto (o arquivo veio de uma captura de tela com
    suavização de subpixel).
    """
    global _LOGO_CACHE
    if _LOGO_CACHE is None:
        # A logo mora em `web/`, junto do resto do que a tela usa — e é de lá
        # que `recursos()` a resolve, esteja o programa no repositório ou dentro
        # do .exe. Havia aqui um segundo caminho apontando para uma cópia na
        # raiz do projeto: cópia byte a byte da mesma imagem, e ramo que nunca
        # executava.
        original = Image.open(recursos() / "logo-eleva.png").convert("RGBA")
        sobre_branco = Image.alpha_composite(
            Image.new("RGBA", original.size, (255, 255, 255, 255)), original
        )
        tinta = sobre_branco.convert("L").point(lambda v: 255 - v)  # escuro = opaco
        limites = tinta.point(lambda v: 255 if v > 40 else 0).getbbox()
        if limites:
            tinta = tinta.crop(limites)
        marca = Image.new("RGBA", tinta.size, (22, 25, 29, 255))
        marca.putalpha(tinta)

        buffer = io.BytesIO()
        marca.save(buffer, format="PNG")
        _LOGO_CACHE = buffer.getvalue()
    return _LOGO_CACHE


_LOGO_CACHE: bytes | None = None

app = criar_app()

__all__ = ["TITULO", "VERSAO", "ZPL_EXEMPLO", "app", "criar_app", "zpl_de_teste"]
