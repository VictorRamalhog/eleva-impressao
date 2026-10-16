"""Etiqueta em PDF: achar, encaixar e imprimir sem estragar o código de barras.

O Mercado Livre passou a entregar a etiqueta de uma coluna em PDF — uma A4
deitada com a etiqueta (≈ 90 × 149 mm, com moldura) no canto, mais uma página
com a lista de conteúdo. Os PDFs daqui são montados à mão, na mesma geometria
da etiqueta real (moldura no mesmo lugar, anel de 0,3 pt, barras de 0,53 mm),
porque a etiqueta de verdade traz nome e endereço de comprador e não entra no
repositório.

O que se cobra:

- a etiqueta é achada pela moldura e a lista de conteúdo fica de fora;
- o encaixe não distorce e o código de barras **continua lendo** (lido de
  volta por ``leitor_code128``, como um coletor leria);
- a linha fina da moldura não some no preto e branco;
- a prévia e o que vai pelo cabo são o mesmo bitmap, nos três modos.
"""

from __future__ import annotations

import base64
import io
from collections import Counter

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from eleva_impressao import app as aplicacao
from eleva_impressao import barras, formatos, pdf, preferencias, render, windows
from leitor_code128 import corridas, ler

A4_DEITADA = (841.89, 595.28)
A4_EM_PE = (595.28, 841.89)
CODIGO = "48265574814"

# Onde a etiqueta real do Mercado Livre desenha a moldura, em pontos PDF.
MOLDURA_ML = (31.2, 145.4, 286.9, 566.9)


# --------------------------------------------------------------------------- #
# PDF montado à mão
# --------------------------------------------------------------------------- #


def montar_pdf(paginas: list[tuple[tuple[float, float], str]]) -> bytes:
    """Um PDF mínimo e válido: cada página é ((largura, altura), conteúdo)."""
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,  # a árvore de páginas, preenchida abaixo
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    filhas = []
    for (largura, altura), conteudo in paginas:
        corpo = conteudo.encode("latin-1")
        numero_conteudo = len(objetos) + 2
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {largura} {altura}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {numero_conteudo} 0 R >>"
            .encode("ascii")
        )
        filhas.append(f"{len(objetos)} 0 R")
        objetos.append(
            f"<< /Length {len(corpo)} >>\nstream\n".encode("ascii") + corpo + b"\nendstream"
        )
    objetos[1] = f"<< /Type /Pages /Kids [{' '.join(filhas)}] /Count {len(filhas)} >>".encode()

    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for numero, objeto in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f"{numero} 0 obj\n".encode() + objeto + b"\nendobj\n"
    xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for posicao in posicoes:
        saida += f"{posicao:010d} 00000 n \n".encode()
    saida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(saida)


def anel(caixa: tuple[float, float, float, float], espessura: float = 0.3) -> str:
    """Moldura como o Mercado Livre desenha: um anel *preenchido*, não um traço."""
    e, b, d, c = caixa
    return (
        f"0 g {e} {b} {d - e} {c - b} re "
        f"{e + espessura} {b + espessura} {d - e - 2 * espessura} {c - b - 2 * espessura} re f*\n"
    )


def codigo_de_barras(x: float, y: float, dados: str, modulo: float, altura: float) -> str:
    """Code 128 em retângulos preenchidos, com o codificador da casa."""
    partes = ["0 g"]
    for largura, e_barra in barras.code128(dados).elementos:
        if e_barra:
            partes.append(f"{x:.3f} {y} {largura * modulo:.3f} {altura} re")
        x += largura * modulo
    return " ".join(partes) + " f\n"


def texto(x: float, y: float, corpo: float, conteudo: str) -> str:
    return f"BT /F1 {corpo} Tf {x} {y} Td ({conteudo}) Tj ET\n"


def pagina_de_etiqueta(caixa=MOLDURA_ML, *, com_moldura: bool = True) -> str:
    """A etiqueta na geometria da real: barras de 60 mm em 0,53 mm de módulo."""
    e, b, _d, c = caixa
    conteudo = anel(caixa) if com_moldura else ""
    conteudo += texto(e + 43, c - 14, 9, "ELEVA STORE PRIME LTDA")
    conteudo += f"0 g {e + 11.6} {c - 88} 65.8 19.8 re f\n"  # a caixa preta do XSP10
    conteudo += codigo_de_barras(e + 28.6, c - 153.3, CODIGO, 1.511, 56.7)
    conteudo += texto(e + 12, c - 300, 8, "CEP: 03066020")
    return conteudo


def pagina_de_lista() -> str:
    """A lista de conteúdo: cabeçalho cinza largo e texto, sem moldura."""
    return (
        "0.62 g 28.3 735.6 538.6 22.4 re f\n"
        + texto(34, 742, 11, "Identificacao")
        + texto(250, 742, 11, "Produto")
        + texto(31, 723, 9, "Venda: 2000018995479320")
        + texto(261, 723, 9, "Camiseta Baby Look Basica 100% Algodao T-shirt")
    )


def pdf_do_mercado_livre() -> bytes:
    return montar_pdf([(A4_DEITADA, pagina_de_etiqueta()), (A4_EM_PE, pagina_de_lista())])


def pontos_pretos(imagem: Image.Image) -> Image.Image:
    """255 onde o cabeçote queima — para ``getbbox`` achar o desenho."""
    return imagem.point(lambda v: 255 if v < 128 else 0)


def confere_que_le(imagem: Image.Image) -> None:
    """Passa o "coletor" em várias alturas: tem de ler o código, e só ele.

    Lê por dentro da moldura — senão a linha da borda entra como barra. Linha de
    texto não decodifica e é ignorada; o que não pode é decodificar **outro**
    número, ou ler em poucas passadas.
    """
    esquerda, _topo, direita, _baixo = pontos_pretos(imagem).getbbox()
    lidas = Counter()
    for y in range(0, imagem.height, 4):
        linha = [imagem.getpixel((x, y)) for x in range(esquerda + 8, direita - 8)]
        if len(corridas(linha)) >= 55 and (lido := ler(linha)):
            lidas[lido] += 1
    assert set(lidas) == {CODIGO}
    assert lidas[CODIGO] >= 20  # o código tem 20 mm de altura: ~40 passadas


@pytest.fixture(autouse=True)
def dados_isolados(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "dir_dados", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def documento():
    return pdf.abrir(pdf_do_mercado_livre(), "etiqueta.pdf")


# --------------------------------------------------------------------------- #
# Achar a etiqueta na folha
# --------------------------------------------------------------------------- #


class TestAcharEtiqueta:
    def test_acha_pela_moldura_e_deixa_a_lista_de_conteudo_de_fora(self, documento):
        etiqueta, lista = documento.recortes
        assert (etiqueta.pagina, etiqueta.origem, etiqueta.sugerido) == (0, "moldura", True)
        # 90,2 × 148,7 mm de moldura, mais meio milímetro de folga de cada lado.
        assert etiqueta.largura_mm == pytest.approx(91.2, abs=0.2)
        assert etiqueta.altura_mm == pytest.approx(149.7, abs=0.2)
        assert (lista.pagina, lista.origem, lista.sugerido) == (1, "pagina", False)
        assert documento.sugeridos() == [0]

    def test_sem_moldura_nenhuma_a_pagina_desenhada_e_a_etiqueta(self):
        dados = montar_pdf([(A4_DEITADA, pagina_de_etiqueta(com_moldura=False))])
        (recorte,) = pdf.abrir(dados).recortes
        assert recorte.origem == "pagina"
        assert recorte.sugerido is True
        # Só o que tem tinta, não a A4 inteira.
        assert recorte.largura_mm < 100 and recorte.altura_mm < 120

    def test_retangulo_branco_nao_e_moldura(self):
        """Fundo branco também é um retângulo — mas não tem borda desenhada."""
        fundo = "1 g 20 20 400 500 re f\n"
        dados = montar_pdf([(A4_DEITADA, fundo + pagina_de_etiqueta(com_moldura=False))])
        (recorte,) = pdf.abrir(dados).recortes
        assert recorte.origem == "pagina"

    def test_duas_etiquetas_na_mesma_folha_saem_na_ordem_de_leitura(self):
        direita = (431.2, 145.4, 686.9, 566.9)
        dados = montar_pdf([(A4_DEITADA, pagina_de_etiqueta(direita) + pagina_de_etiqueta())])
        recortes = pdf.abrir(dados).recortes
        assert [r.origem for r in recortes] == ["moldura", "moldura"]
        assert recortes[0].caixa[0] < recortes[1].caixa[0]

    def test_quadro_dentro_da_etiqueta_nao_vira_outra_etiqueta(self):
        quadro = anel((60, 300, 200, 400))  # 49 × 35 mm, dentro da moldura
        dados = montar_pdf([(A4_DEITADA, pagina_de_etiqueta() + quadro)])
        assert len(pdf.abrir(dados).recortes) == 1

    @pytest.mark.parametrize("dados, mensagem", [
        (b"", "vazio"),
        (b"^XA^FDisto e ZPL^FS^XZ", "não é um PDF"),
        (b"%PDF-1.4 quebrado", "não consegui ler"),
    ])
    def test_arquivo_ruim_explica_o_que_houve(self, dados, mensagem):
        with pytest.raises(pdf.PdfInvalido, match=mensagem):
            pdf.abrir(dados)

    def test_pdf_em_branco_nao_tem_etiqueta(self):
        with pytest.raises(pdf.PdfInvalido, match="nada desenhado"):
            pdf.abrir(montar_pdf([(A4_DEITADA, "")]))


# --------------------------------------------------------------------------- #
# Encaixar no formato
# --------------------------------------------------------------------------- #


class TestEncaixe:
    def _desenhar(self, documento, **extra):
        opcoes = {"largura_mm": 100, "altura_mm": 150, "dpmm": 8, "bitonal": True}
        return pdf.desenhar(documento, 0, **{**opcoes, **extra})

    def test_sai_do_tamanho_exato_do_formato(self, documento):
        resultado = self._desenhar(documento)
        assert resultado.imagem.size == (800, 1200)
        assert set(resultado.imagem.tobytes()) <= {0, 255}

    def test_nao_distorce_e_centraliza(self, documento):
        resultado = self._desenhar(documento)
        esquerda, topo, direita, baixo = pontos_pretos(resultado.imagem).getbbox()
        largura, altura = direita - esquerda, baixo - topo
        # A proporção da moldura (255,7 × 421,5 pt) não muda — no ponto.
        assert largura / altura == pytest.approx(255.7 / 421.5, abs=0.003)
        assert abs(esquerda - (800 - direita)) <= 2
        assert abs(topo - (1200 - baixo)) <= 2

    def test_a_altura_manda_e_a_etiqueta_quase_nao_muda_de_tamanho(self, documento):
        resultado = self._desenhar(documento)
        assert resultado.escala == pytest.approx(150 / 149.7, abs=0.005)
        assert not resultado.avisos

    def test_margem_de_seguranca_deixa_a_beira_em_branco(self, documento):
        resultado = self._desenhar(documento, margem_mm=2)
        esquerda, topo, direita, baixo = pontos_pretos(resultado.imagem).getbbox()
        assert min(esquerda, topo, 800 - direita, 1200 - baixo) >= 16 - 1

    def test_codigo_de_barras_continua_lendo(self, documento):
        confere_que_le(self._desenhar(documento).imagem)

    @pytest.mark.parametrize("margem", [0, 0.5, 1, 1.5, 2, 3, 4])
    def test_codigo_de_barras_le_em_qualquer_escala_proxima(self, documento, margem):
        """Cada margem muda a escala — e o módulo fracionário cai noutro lugar."""
        imagem = self._desenhar(documento, margem_mm=margem).imagem
        confere_que_le(imagem)

    @pytest.mark.parametrize("margem", [0, 0.5, 1, 1.5, 2, 3, 4])
    def test_linha_fina_da_moldura_nao_some(self, documento, margem):
        """O anel de 0,3 pt tem menos de um ponto do cabeçote — e sumia."""
        imagem = self._desenhar(documento, margem_mm=margem).imagem
        esquerda, topo, direita, baixo = pontos_pretos(imagem).getbbox()
        meio_x, meio_y = (esquerda + direita) // 2, (topo + baixo) // 2

        def tem_linha(pontos):
            return all(imagem.getpixel(p) == 0 for p in pontos)

        faixa_h = range(meio_x - 150, meio_x + 150, 10)
        faixa_v = range(meio_y - 300, meio_y + 300, 10)
        assert any(tem_linha([(x, y) for x in faixa_h]) for y in range(topo, topo + 3))
        assert any(tem_linha([(x, y) for x in faixa_h]) for y in range(baixo - 3, baixo))
        assert any(tem_linha([(x, y) for y in faixa_v]) for x in range(esquerda, esquerda + 3))
        assert any(tem_linha([(x, y) for y in faixa_v]) for x in range(direita - 3, direita))

    def test_gira_quando_cabe_bem_melhor_deitada(self):
        deitada = (100, 100, 525, 383.5)  # 150 × 100 mm
        documento = pdf.abrir(montar_pdf([(A4_DEITADA, anel(deitada))]))
        resultado = pdf.desenhar(documento, 0, largura_mm=100, altura_mm=150, dpmm=8)
        assert resultado.girada is True
        assert resultado.imagem.size == (800, 1200)
        assert resultado.escala == pytest.approx(150 / 151, abs=0.01)
        assert any("girada 90°" in a for a in resultado.avisos)

    def test_rotacao_pedida_gira_o_conteudo_e_mantem_o_papel(self, documento):
        resultado = self._desenhar(documento, rotacao=180)
        assert resultado.imagem.size == (800, 1200)
        confere_que_le(resultado.imagem.rotate(180))

    def test_avisa_quando_encolhe_demais(self, documento):
        resultado = pdf.desenhar(documento, 0, largura_mm=60, altura_mm=40, dpmm=8)
        assert resultado.escala < 0.5
        assert any("reduzida" in a and "código de barras" in a for a in resultado.avisos)

    def test_cinza_e_papel_sao_a_mesma_imagem_antes_do_corte(self, documento):
        cinza = self._desenhar(documento, bitonal=False).imagem
        papel = self._desenhar(documento).imagem
        assert cinza.point(lambda v: 0 if v < 128 else 255).tobytes() == papel.tobytes()


class TestResgateDeLinhaFina:
    def test_linha_que_sumiria_vira_preta(self):
        imagem = Image.new("L", (20, 20), 255)
        for x in range(20):
            imagem.putpixel((x, 10), 150)  # 40% de tinta: some no corte de 128
        resgatada = pdf._resgatar_linhas_finas(imagem)
        assert all(resgatada.getpixel((x, 10)) < 128 for x in range(20))

    def test_borda_de_barra_nao_engorda(self):
        """O meio-tom na borda de uma barra é decisão do corte, não resgate."""
        imagem = Image.new("L", (20, 20), 255)
        for y in range(20):
            for x in range(5, 9):
                imagem.putpixel((x, y), 0)
            imagem.putpixel((9, y), 150)
        resgatada = pdf._resgatar_linhas_finas(imagem)
        assert all(resgatada.getpixel((9, y)) == 150 for y in range(20))

    def test_area_cinza_nao_vira_borrao_preto(self):
        imagem = Image.new("L", (30, 30), 255)
        for x in range(5, 25):
            for y in range(5, 25):
                imagem.putpixel((x, y), 170)
        resgatada = pdf._resgatar_linhas_finas(imagem)
        assert resgatada.getpixel((15, 15)) == 170


# --------------------------------------------------------------------------- #
# Formato de fábrica
# --------------------------------------------------------------------------- #


class TestFormatoPdf:
    def test_o_formato_do_pdf_vem_de_fabrica(self):
        formato = formatos.buscar(formatos.PADRAO_PDF)
        assert (formato.largura_mm, formato.altura_mm, formato.colunas) == (100, 150, 1)
        assert formato.margem_seguranca_mm == 1.5
        assert formato.embutido
        assert "margem de 1.5 mm no PDF" in formato.descricao

    def test_margem_de_seguranca_sobrevive_ao_salvar(self):
        salvo = formatos.salvar(formatos.Formato("", "Rolo 100x150 PDF", 100, 150,
                                                 margem_seguranca_mm=2.5))
        assert formatos.buscar(salvo.id).margem_seguranca_mm == 2.5

    @pytest.mark.parametrize("margem", [-1, 26])
    def test_margem_de_seguranca_absurda_e_recusada(self, margem):
        with pytest.raises(formatos.FormatoInvalido):
            formatos.Formato("x", "x", 100, 150, margem_seguranca_mm=margem).validar()


# --------------------------------------------------------------------------- #
# API: da tela ao cabo
# --------------------------------------------------------------------------- #


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(windows, "listar", lambda: [
        windows.Impressora("LABEL", "USB001", "LABEL", padrao=True),
    ])
    return TestClient(aplicacao.criar_app())


def abrir_pela_api(cliente, dados: bytes | None = None) -> dict:
    conteudo = base64.b64encode(dados or pdf_do_mercado_livre()).decode()
    resposta = cliente.post("/api/pdf/abrir", json={
        "nome": "etiqueta.pdf",
        "conteudo_base64": f"data:application/pdf;base64,{conteudo}",
    })
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def imagem_da_previa(resposta: dict) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(resposta["imagem"].split(",")[1]))).convert("L")


class TestApiPdf:
    def test_abrir_devolve_o_que_achou(self, cliente):
        dados = abrir_pela_api(cliente)
        assert dados["paginas"] == 2
        assert dados["sugeridos"] == [0]
        assert [r["origem"] for r in dados["recortes"]] == ["moldura", "pagina"]
        assert "lista de conteúdo" in dados["recortes"][1]["descricao"]

    def test_previa_usa_o_formato_e_conta_so_as_marcadas(self, cliente):
        aberto = abrir_pela_api(cliente)
        resposta = cliente.post("/api/preview", json={
            "pdf_id": aberto["id"], "formato_id": formatos.PADRAO_PDF, "qualidade": "bitonal",
        }).json()
        assert resposta["total"] == 1
        assert resposta["geometria"]["largura_pontos"] == 800
        assert resposta["geometria"]["altura_pontos"] == 1200
        assert resposta["pdf"]["escala"] == pytest.approx(147 / 149.7, abs=0.005)
        confere_que_le(imagem_da_previa(resposta))

    def test_marcar_a_lista_de_conteudo_tambem_imprime_ela(self, cliente, monkeypatch):
        aberto = abrir_pela_api(cliente)
        enviados = []
        monkeypatch.setattr(windows, "imprimir_tspl", lambda folhas, **k: (
            enviados.append(folhas) or windows.Envio(True, windows.Modo.TSPL, "LABEL", "ok")
        ))
        resposta = cliente.post("/api/imprimir", json={
            "pdf_id": aberto["id"], "pdf_recortes": [0, 1],
            "formato_id": formatos.PADRAO_PDF, "modo": "tspl",
        }).json()
        assert resposta["ok"] is True
        assert resposta["folhas"] == 2
        assert any("girada" in a for a in resposta["avisos"])

    def test_previa_e_papel_tspl_sao_o_mesmo_bitmap(self, cliente, monkeypatch):
        """A regra do programa inteiro, agora para PDF."""
        aberto = abrir_pela_api(cliente)
        pedido = {"pdf_id": aberto["id"], "formato_id": formatos.PADRAO_PDF}
        previa = imagem_da_previa(cliente.post("/api/preview", json={
            **pedido, "qualidade": "bitonal",
        }).json())

        enviados = []
        monkeypatch.setattr(windows, "imprimir_tspl", lambda folhas, **k: (
            enviados.append((folhas, k)) or windows.Envio(True, windows.Modo.TSPL, "LABEL", "ok")
        ))
        cliente.post("/api/imprimir", json={**pedido, "modo": "tspl"})
        (folha,), opcoes = enviados[0]
        assert folha.tobytes() == previa.tobytes()
        assert (opcoes["largura_mm"], opcoes["altura_mm"]) == (100, 150)

    def test_modo_imagem_manda_o_mesmo_bitmap(self, cliente, monkeypatch):
        aberto = abrir_pela_api(cliente)
        pedido = {"pdf_id": aberto["id"], "formato_id": formatos.PADRAO_PDF}
        previa = imagem_da_previa(cliente.post("/api/preview", json={
            **pedido, "qualidade": "bitonal",
        }).json())
        monkeypatch.setattr(aplicacao, "_conferir_papel", lambda *a: None)
        enviados = []
        monkeypatch.setattr(windows, "imprimir_imagem", lambda folhas, **k: (
            enviados.append(folhas) or windows.Envio(True, windows.Modo.IMAGEM, "LABEL", "ok")
        ))
        assert cliente.post("/api/imprimir", json={**pedido, "modo": "imagem"}).json()["ok"]
        assert enviados[0][0].tobytes() == previa.tobytes()

    def test_zpl_cru_leva_o_mesmo_bitmap_dentro_de_gf(self, cliente, monkeypatch):
        """Firmware que só fala ZPL também recebe o PDF — como gráfico."""
        aberto = abrir_pela_api(cliente)
        pedido = {"pdf_id": aberto["id"], "formato_id": formatos.PADRAO_PDF}
        previa = imagem_da_previa(cliente.post("/api/preview", json={
            **pedido, "qualidade": "bitonal",
        }).json())
        enviados = []
        monkeypatch.setattr(windows, "enviar_bruto", lambda dados, **k: (
            enviados.append(dados) or windows.Envio(True, windows.Modo.ZPL, "LABEL", "ok")
        ))
        cliente.post("/api/imprimir", json={**pedido, "modo": "zpl", "copias": 3})
        zpl = enviados[0].decode()
        assert "^GFA" in zpl and "^PQ3^XZ" in zpl
        devolta = render.renderizar(zpl, largura_mm=100, altura_mm=150, bitonal=True).imagem
        assert devolta.tobytes() == previa.tobytes()

    def test_so_a_etiqueta_em_foco(self, cliente, monkeypatch):
        aberto = abrir_pela_api(cliente)
        enviados = []
        monkeypatch.setattr(windows, "imprimir_tspl", lambda folhas, **k: (
            enviados.append(folhas) or windows.Envio(True, windows.Modo.TSPL, "LABEL", "ok")
        ))
        cliente.post("/api/imprimir", json={
            "pdf_id": aberto["id"], "pdf_recortes": [0, 1], "indice": 1,
            "apenas_esta": True, "formato_id": formatos.PADRAO_PDF, "modo": "tspl",
        })
        assert len(enviados[0]) == 1

    def test_duas_colunas_poe_duas_etiquetas_do_pdf_lado_a_lado(self, cliente):
        aberto = abrir_pela_api(cliente)
        resposta = cliente.post("/api/preview", json={
            "pdf_id": aberto["id"], "pdf_recortes": [0, 1],
            "formato_id": "duas-40x25", "visao": "folha",
        }).json()
        assert resposta["folhas"] == 1
        assert resposta["geometria"]["largura_pontos"] == 82 * 8

    def test_pdf_esquecido_pede_para_abrir_de_novo(self, cliente):
        resposta = cliente.post("/api/preview", json={"pdf_id": "nao-existe"})
        assert resposta.status_code == 410
        assert "abra o arquivo de novo" in resposta.json()["detail"]

    def test_nada_marcado_nao_imprime(self, cliente):
        aberto = abrir_pela_api(cliente)
        resposta = cliente.post("/api/imprimir", json={
            "pdf_id": aberto["id"], "pdf_recortes": [], "modo": "tspl",
        })
        assert resposta.status_code == 400
        assert "nenhuma etiqueta" in resposta.json()["detail"]

    def test_ajuste_automatico_e_so_para_zpl(self, cliente):
        aberto = abrir_pela_api(cliente)
        resposta = cliente.post("/api/ajustar", json={"pdf_id": aberto["id"]})
        assert resposta.status_code == 400

    @pytest.mark.parametrize("conteudo", ["isto não é base64!!", base64.b64encode(b"oi").decode()])
    def test_arquivo_ruim_vira_mensagem_e_nao_erro_500(self, cliente, conteudo):
        resposta = cliente.post("/api/pdf/abrir", json={"conteudo_base64": conteudo})
        assert resposta.status_code == 400

    def test_png_e_pdf_de_saida_tambem_saem_do_pdf(self, cliente):
        aberto = abrir_pela_api(cliente)
        pedido = {"pdf_id": aberto["id"], "formato_id": formatos.PADRAO_PDF}
        png = cliente.post("/api/png", json=pedido)
        assert Image.open(io.BytesIO(png.content)).size == (800, 1200)
        saida = cliente.post("/api/pdf", json=pedido)
        assert saida.content.startswith(b"%PDF")

    def test_etiqueta_de_teste_ignora_o_pdf_aberto(self, cliente, monkeypatch):
        aberto = abrir_pela_api(cliente)
        pacotes = []
        monkeypatch.setattr(windows, "enviar_bruto", lambda dados, **k: (
            pacotes.append(dados) or windows.Envio(True, windows.Modo.ZPL, "LABEL", "ok")
        ))
        cliente.post("/api/teste", json={"pdf_id": aberto["id"], "modo": "zpl"})
        assert b"Eleva Impressao" in pacotes[0]

    def test_formato_do_pdf_e_lembrado_a_parte(self, cliente):
        cliente.post("/api/preferencias", json={"formato_id": "etq-100x50"})
        cliente.post("/api/preferencias", json={"formato_pdf_id": "ml-pdf-100x150"})
        guardado = preferencias.carregar()
        assert (guardado.formato_id, guardado.formato_pdf_id) == ("etq-100x50", "ml-pdf-100x150")
        estado = cliente.get("/api/estado").json()
        assert estado["formato_pdf_padrao"] == "ml-pdf-100x150"
