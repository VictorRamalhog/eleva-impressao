"""Imagem → ``^GF``. É o caminho de mão dupla do gráfico dentro do ZPL.

Serve para duas coisas:

1. o botão "adicionar imagem" da tela, que põe uma logo no ZPL sem ninguém
   precisar entender ``^GFA``;
2. conferir o decodificador do ``render`` — se uma imagem vai e volta igual, o
   ``^GF`` da etiqueta do Mercado Livre também será lido direito.

A saída usa ``:Z64:`` (deflate + base64), que é o que encolhe de verdade: uma
logo de 300×100 sai com ~1/10 do tamanho do hexadecimal cru.
"""

from __future__ import annotations

import base64
import zlib

from PIL import Image

LIMIAR_PADRAO = 128


def preparar(
    imagem: Image.Image,
    *,
    largura_pontos: int | None = None,
    altura_pontos: int | None = None,
    limiar: int = LIMIAR_PADRAO,
    inverter: bool = False,
) -> Image.Image:
    """Redimensiona e reduz a preto e branco puro — a térmica não tem cinza.

    Sem alfa branco por baixo, um PNG com fundo transparente vira um retângulo
    preto sólido na etiqueta.
    """
    if imagem.mode in ("RGBA", "LA", "P"):
        convertida = imagem.convert("RGBA")
        fundo = Image.new("RGBA", convertida.size, (255, 255, 255, 255))
        imagem = Image.alpha_composite(fundo, convertida)
    cinza = imagem.convert("L")

    if largura_pontos or altura_pontos:
        largura = largura_pontos or max(
            1, round(cinza.width * (altura_pontos / cinza.height))
        )
        altura = altura_pontos or max(1, round(cinza.height * (largura / cinza.width)))
        cinza = cinza.resize((max(1, largura), max(1, altura)), Image.LANCZOS)

    preto_e_branco = cinza.point(lambda v: 0 if v < limiar else 255, mode="L")
    if inverter:
        preto_e_branco = preto_e_branco.point(lambda v: 255 - v, mode="L")
    return preto_e_branco


def para_bytes(imagem: Image.Image) -> tuple[bytes, int, int]:
    """(bytes da imagem, bytes por linha, altura). Bit 1 = ponto preto."""
    largura_bytes = (imagem.width + 7) // 8
    # O '1' do Pillow guarda 255 para branco; o ZPL quer 1 para tinta.
    invertida = imagem.point(lambda v: 0 if v >= 128 else 255, mode="L").convert("1")
    cru = invertida.tobytes()
    # ``tobytes`` já alinha cada linha em byte, que é o que o ^GF espera.
    return cru, largura_bytes, imagem.height


def para_gf(imagem: Image.Image, *, comprimir: bool = True) -> str:
    """Monta o comando ``^GFA`` completo, pronto para colar no ZPL."""
    dados, largura_bytes, altura = para_bytes(imagem)
    total = largura_bytes * altura
    if comprimir:
        payload = base64.b64encode(zlib.compress(dados, 9)).decode("ascii")
        crc = f"{zlib.crc32(payload.encode('ascii')) & 0xFFFF:04X}"
        corpo = f":Z64:{payload}:{crc}"
    else:
        corpo = dados.hex().upper()
    return f"^GFA,{total},{total},{largura_bytes},{corpo}"


def campo_de_imagem(
    imagem: Image.Image,
    x: int,
    y: int,
    *,
    largura_pontos: int | None = None,
    altura_pontos: int | None = None,
    limiar: int = LIMIAR_PADRAO,
    inverter: bool = False,
) -> str:
    """``^FO`` + ``^GF`` + ``^FS`` — o bloco que a tela insere no editor."""
    preparada = preparar(
        imagem,
        largura_pontos=largura_pontos,
        altura_pontos=altura_pontos,
        limiar=limiar,
        inverter=inverter,
    )
    return f"^FO{x},{y}{para_gf(preparada)}^FS"


__all__ = ["LIMIAR_PADRAO", "campo_de_imagem", "para_bytes", "para_gf", "preparar"]
