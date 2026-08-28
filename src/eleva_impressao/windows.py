"""As três formas de falar com a impressora térmica no Windows.

Por que três, e não uma: a KP-IM604 é um clone de fábrica chinesa. O driver que
veio na caixa se chama "LABEL" e é um driver **GDI** — ele espera receber uma
página desenhada, não comando de impressora. Mas o firmware por baixo entende
TSPL, e alguns lotes entendem ZPL. Qual dos três funciona depende do modelo e da
versão do firmware, e não dá para descobrir isso lendo a caixa.

Então em vez de adivinhar, o programa oferece os três e deixa a operadora ver
qual sai no papel:

``IMAGEM``  desenha o bitmap pela API gráfica do Windows, usando o driver
            instalado. É o que funciona sempre que a impressora imprime uma
            página de teste do Windows — por isso é o padrão.
``TSPL``    manda o bitmap dentro de um ``BITMAP`` do TSPL, direto para a porta,
            sem passar pelo driver. É o dialeto nativo desta família de
            impressora.
``ZPL``     manda o ZPL cru. Só funciona se o firmware falar ZPL de verdade.

Os dois últimos vão pelo spooler com tipo de dados ``RAW``, que é o que entrega
os bytes à porta USB001 sem o driver reinterpretar nada.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image

WINDOWS = sys.platform == "win32"


class ImpressoraIndisponivel(RuntimeError):
    """Não dá para imprimir daqui (não é Windows, pywin32 ausente, fila sumiu…)."""


class Modo(StrEnum):
    IMAGEM = "imagem"
    TSPL = "tspl"
    ZPL = "zpl"


@dataclass(slots=True)
class Impressora:
    nome: str
    porta: str
    driver: str
    padrao: bool = False

    def dicionario(self) -> dict:
        return {
            "nome": self.nome,
            "porta": self.porta,
            "driver": self.driver,
            "padrao": self.padrao,
        }


@dataclass(slots=True)
class Envio:
    ok: bool
    modo: Modo
    impressora: str
    detalhe: str
    bytes_enviados: int = 0
    paginas: int = 0

    def dicionario(self) -> dict:
        return {
            "ok": self.ok,
            "modo": self.modo.value,
            "impressora": self.impressora,
            "detalhe": self.detalhe,
            "bytes": self.bytes_enviados,
            "paginas": self.paginas,
        }


def _win32print():
    if not WINDOWS:
        raise ImpressoraIndisponivel("impressão direta só existe no Windows")
    try:
        import win32print
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise ImpressoraIndisponivel(
            "o pywin32 não está instalado; rode 'uv sync' de novo"
        ) from exc
    return win32print


# --------------------------------------------------------------------------- #
# Descoberta
# --------------------------------------------------------------------------- #


def listar() -> list[Impressora]:
    """Todas as filas locais, com a porta — é onde se confere o USB001."""
    win32print = _win32print()
    try:
        padrao = win32print.GetDefaultPrinter()
    except Exception:  # noqa: BLE001 - sem impressora padrão configurada
        padrao = ""

    achadas: list[Impressora] = []
    sinalizadores = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for info in win32print.EnumPrinters(sinalizadores, None, 2):
        nome = info["pPrinterName"]
        achadas.append(
            Impressora(
                nome=nome,
                porta=info.get("pPortName", "") or "",
                driver=info.get("pDriverName", "") or "",
                padrao=nome == padrao,
            )
        )
    achadas.sort(key=lambda i: (not i.padrao, i.nome.lower()))
    return achadas


def padrao() -> Impressora | None:
    for impressora in listar():
        if impressora.padrao:
            return impressora
    return None


def escolher(nome: str | None = None) -> Impressora:
    """Resolve o nome pedido; sem nome, cai na padrão do Windows."""
    disponiveis = listar()
    if not disponiveis:
        raise ImpressoraIndisponivel("nenhuma impressora instalada neste computador")
    if nome:
        for impressora in disponiveis:
            if impressora.nome == nome:
                return impressora
        raise ImpressoraIndisponivel(
            f"a impressora {nome!r} não está mais na lista; "
            f"disponíveis: {', '.join(i.nome for i in disponiveis)}"
        )
    return next((i for i in disponiveis if i.padrao), disponiveis[0])


# --------------------------------------------------------------------------- #
# Caminho 1 — bitmap pelo driver (GDI)
# --------------------------------------------------------------------------- #


def imprimir_imagem(
    folhas: list[Image.Image],
    *,
    impressora: str | None = None,
    dpmm: int = 8,
    copias: int = 1,
    titulo: str = "Eleva Impressão",
) -> Envio:
    """Desenha cada folha numa página, ponto a ponto quando a densidade bate.

    Escala pela densidade real do driver em vez de esticar para a página: se o
    papel configurado no driver for maior que a etiqueta, a etiqueta sai no
    tamanho certo, encostada no canto — e não inflada até a borda.
    """
    alvo = escolher(impressora)
    if not folhas:
        return Envio(False, Modo.IMAGEM, alvo.nome, "nada para imprimir")

    try:
        import win32con
        import win32ui
        from PIL import ImageWin
    except ImportError as exc:  # pragma: no cover
        raise ImpressoraIndisponivel("pywin32 não está instalado") from exc

    dc = win32ui.CreateDC()
    try:
        dc.CreatePrinterDC(alvo.nome)
    except Exception as exc:  # noqa: BLE001 - a fila pode ter sumido no meio
        raise ImpressoraIndisponivel(
            f"não consegui abrir a impressora {alvo.nome!r}: {exc}"
        ) from exc

    paginas = 0
    try:
        dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX) or 203
        dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY) or 203
        util_x = dc.GetDeviceCaps(win32con.HORZRES)
        util_y = dc.GetDeviceCaps(win32con.VERTRES)
        # O bitmap está em dpmm pontos/mm; o driver trabalha em dpi.
        escala_x = dpi_x / (dpmm * 25.4)
        escala_y = dpi_y / (dpmm * 25.4)

        dc.StartDoc(titulo)
        for _ in range(max(1, copias)):
            for folha in folhas:
                largura = max(1, int(round(folha.width * escala_x)))
                altura = max(1, int(round(folha.height * escala_y)))
                if util_x > 0 and util_y > 0 and (largura > util_x or altura > util_y):
                    # Não cabe: encolhe proporcional, para não cortar a etiqueta.
                    fator = min(util_x / largura, util_y / altura)
                    largura = max(1, int(largura * fator))
                    altura = max(1, int(altura * fator))
                dc.StartPage()
                ImageWin.Dib(folha.convert("RGB")).draw(
                    dc.GetHandleOutput(), (0, 0, largura, altura)
                )
                dc.EndPage()
                paginas += 1
        dc.EndDoc()
    except Exception as exc:  # noqa: BLE001 - qualquer falha do driver
        with suppress(Exception):  # já estamos tratando o erro principal
            dc.AbortDoc()
        return Envio(False, Modo.IMAGEM, alvo.nome, f"o driver recusou: {exc}")
    finally:
        dc.DeleteDC()

    return Envio(
        True, Modo.IMAGEM, alvo.nome,
        f"{paginas} página(s) enviadas pelo driver para {alvo.porta or 'a porta do driver'}",
        paginas=paginas,
    )


# --------------------------------------------------------------------------- #
# Caminho 2 — bytes crus pelo spooler
# --------------------------------------------------------------------------- #


def enviar_bruto(
    dados: bytes,
    *,
    impressora: str | None = None,
    modo: Modo = Modo.ZPL,
    titulo: str = "Eleva Impressão",
) -> Envio:
    """``RAW``: o spooler entrega os bytes à porta sem o driver reinterpretar."""
    alvo = escolher(impressora)
    if not dados:
        return Envio(False, modo, alvo.nome, "nada para enviar")

    win32print = _win32print()
    handle = win32print.OpenPrinter(alvo.nome)
    try:
        trabalho = win32print.StartDocPrinter(handle, 1, (titulo, None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, dados)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
        del trabalho
    except Exception as exc:  # noqa: BLE001 - spooler pode recusar por vários motivos
        return Envio(False, modo, alvo.nome, f"o spooler recusou: {exc}")
    finally:
        win32print.ClosePrinter(handle)

    return Envio(
        True, modo, alvo.nome,
        f"{len(dados)} bytes enviados em modo RAW para {alvo.porta or 'a porta do driver'}",
        bytes_enviados=len(dados),
    )


def imprimir_zpl(zpl: str, *, impressora: str | None = None, copias: int = 1) -> Envio:
    """ZPL cru. Só sai se o firmware falar ZPL — por isso não é o padrão."""
    corpo = (zpl or "").strip()
    if copias > 1 and "^PQ" not in corpo.upper():
        corpo = corpo.replace("^XZ", f"^PQ{copias}^XZ", 1)
    return enviar_bruto(corpo.encode("utf-8"), impressora=impressora, modo=Modo.ZPL)


# --------------------------------------------------------------------------- #
# Caminho 3 — TSPL (o dialeto nativo desta família de impressora)
# --------------------------------------------------------------------------- #


def tspl_de_imagem(
    folha: Image.Image,
    *,
    largura_mm: float,
    altura_mm: float,
    dpmm: int = 8,
    gap_mm: float = 2.0,
    copias: int = 1,
    escuridao: int | None = None,
) -> bytes:
    """Monta o pacote TSPL de uma folha.

    Atenção à convenção de bit, que é o contrário do ZPL: no ``BITMAP`` do TSPL
    o bit **0** imprime e o bit 1 deixa em branco. Foi exatamente essa inversão
    que já fez sair etiqueta toda preta.
    """
    preto_e_branco = folha.convert("L").point(lambda v: 0 if v < 128 else 255, mode="L")
    largura_bytes = (preto_e_branco.width + 7) // 8
    # Em '1' do Pillow, 255 (branco) vira bit 1 — que é justamente o "não
    # imprime" do TSPL. A conversão direta já sai na convenção certa.
    dados = preto_e_branco.convert("1").tobytes()

    cabecalho = [
        f"SIZE {largura_mm:g} mm,{altura_mm:g} mm",
        f"GAP {gap_mm:g} mm,0 mm",
        "DIRECTION 1",
        "REFERENCE 0,0",
        "CLS",
    ]
    if escuridao is not None:
        cabecalho.insert(0, f"DENSITY {max(0, min(15, escuridao))}")

    pacote = bytearray()
    for linha in cabecalho:
        pacote += linha.encode("ascii") + b"\r\n"
    pacote += f"BITMAP 0,0,{largura_bytes},{preto_e_branco.height},0,".encode("ascii")
    pacote += dados
    pacote += b"\r\n"
    pacote += f"PRINT 1,{max(1, copias)}\r\n".encode("ascii")
    return bytes(pacote)


def imprimir_tspl(
    folhas: list[Image.Image],
    *,
    impressora: str | None = None,
    largura_mm: float,
    altura_mm: float,
    dpmm: int = 8,
    gap_mm: float = 2.0,
    copias: int = 1,
    escuridao: int | None = None,
) -> Envio:
    if not folhas:
        return Envio(False, Modo.TSPL, impressora or "", "nada para imprimir")
    pacote = bytearray()
    for folha in folhas:
        pacote += tspl_de_imagem(
            folha, largura_mm=largura_mm, altura_mm=altura_mm, dpmm=dpmm,
            gap_mm=gap_mm, copias=copias, escuridao=escuridao,
        )
    envio = enviar_bruto(bytes(pacote), impressora=impressora, modo=Modo.TSPL)
    envio.paginas = len(folhas) * max(1, copias)
    return envio


# --------------------------------------------------------------------------- #
# Diagnóstico
# --------------------------------------------------------------------------- #


def estado(nome: str | None = None) -> dict:
    """O que a fila diz de si mesma — trabalhos parados, erro, offline."""
    alvo = escolher(nome)
    win32print = _win32print()
    handle = win32print.OpenPrinter(alvo.nome)
    try:
        info = win32print.GetPrinter(handle, 2)
    finally:
        win32print.ClosePrinter(handle)

    codigo = info.get("Status", 0)
    problemas = [
        texto
        for bit, texto in _ESTADOS.items()
        if codigo & bit
    ]
    return {
        "impressora": alvo.nome,
        "porta": alvo.porta,
        "driver": alvo.driver,
        "na_fila": info.get("cJobs", 0),
        "codigo": codigo,
        "problemas": problemas,
        "pronta": not problemas,
    }


def diagnostico(nome: str | None = None, *, largura_mm: float = 0, altura_mm: float = 0) -> dict:
    """O que o driver diz do papel — sem imprimir nada.

    Serve para responder a pergunta que sempre aparece quando a etiqueta sai
    cortada ou minúscula: *o papel configurado no Windows é o mesmo tamanho da
    etiqueta que estou mandando?* Abre o contexto gráfico, lê as medidas e
    fecha; nenhuma folha anda.
    """
    alvo = escolher(nome)
    try:
        import win32con
        import win32ui
    except ImportError as exc:  # pragma: no cover
        raise ImpressoraIndisponivel("pywin32 não está instalado") from exc

    dc = win32ui.CreateDC()
    try:
        dc.CreatePrinterDC(alvo.nome)
    except Exception as exc:  # noqa: BLE001
        raise ImpressoraIndisponivel(
            f"não consegui abrir a impressora {alvo.nome!r}: {exc}"
        ) from exc
    try:
        dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX) or 0
        dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY) or 0
        util = (dc.GetDeviceCaps(win32con.HORZRES), dc.GetDeviceCaps(win32con.VERTRES))
        fisico = (
            dc.GetDeviceCaps(_PHYSICALWIDTH),
            dc.GetDeviceCaps(_PHYSICALHEIGHT),
        )
    finally:
        dc.DeleteDC()

    papel_mm = (
        round(fisico[0] / dpi_x * 25.4, 1) if dpi_x else 0.0,
        round(fisico[1] / dpi_y * 25.4, 1) if dpi_y else 0.0,
    )
    avisos: list[str] = []
    if largura_mm and papel_mm[0] and abs(papel_mm[0] - largura_mm) > 2:
        avisos.append(
            f"o papel do driver tem {papel_mm[0]:g} mm de largura e o formato pede "
            f"{largura_mm:g} mm — ajuste o tamanho na Preferências de Impressão do Windows"
        )
    if altura_mm and papel_mm[1] and abs(papel_mm[1] - altura_mm) > 3:
        avisos.append(
            f"o papel do driver tem {papel_mm[1]:g} mm de altura e o formato pede "
            f"{altura_mm:g} mm"
        )
    if dpi_x and abs(dpi_x - 203) > 20 and abs(dpi_x - 300) > 20:
        avisos.append(f"densidade incomum no driver: {dpi_x} dpi")

    return {
        "impressora": alvo.nome,
        "porta": alvo.porta,
        "driver": alvo.driver,
        "dpi": [dpi_x, dpi_y],
        "papel_mm": list(papel_mm),
        "area_util_pontos": list(util),
        "avisos": avisos,
    }


# GetDeviceCaps não expõe estes dois em win32con em toda versão do pywin32.
_PHYSICALWIDTH = 110
_PHYSICALHEIGHT = 111

_ESTADOS = {
    0x00000001: "pausada",
    0x00000002: "erro",
    0x00000008: "sem papel ou papel travado",
    0x00000010: "papel travado",
    0x00000080: "offline",
    0x00000400: "sem papel",
    0x00000800: "aguardando papel manual",
    0x00200000: "porta aberta",
    0x00000200: "ocupada",
}


__all__ = [
    "WINDOWS",
    "Envio",
    "Impressora",
    "ImpressoraIndisponivel",
    "Modo",
    "enviar_bruto",
    "escolher",
    "diagnostico",
    "estado",
    "imprimir_imagem",
    "imprimir_tspl",
    "imprimir_zpl",
    "listar",
    "padrao",
    "tspl_de_imagem",
]
