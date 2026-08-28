"""Formatos, impressão e a API local do Eleva Impressão.

Nada aqui toca a impressora de verdade: o que se testa é o que é montado
*antes* de sair pelo cabo — o pacote TSPL, a folha com duas colunas, as regras
do formato. A impressora física é a única parte que precisa de gente olhando o
papel, e é justamente por isso que existe o botão "etiqueta de teste".
"""

from __future__ import annotations

import base64
import codecs
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from eleva_impressao import app as aplicacao
from eleva_impressao import formatos, preferencias, render, windows


@pytest.fixture(autouse=True)
def dados_isolados(tmp_path, monkeypatch):
    """Cada teste com o seu próprio JSON — sem sujar os formatos de quem roda."""
    monkeypatch.setattr(formatos, "dir_dados", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def impressoras_falsas(monkeypatch):
    fila = [
        windows.Impressora("LABEL", "USB001", "LABEL", padrao=True),
        windows.Impressora("PDF", "PORTPROMPT:", "Microsoft Print To PDF"),
    ]
    monkeypatch.setattr(windows, "listar", lambda: fila)
    return fila


@pytest.fixture
def cliente(impressoras_falsas):
    return TestClient(aplicacao.criar_app())


ZPL = "^XA^CI28^FO40,40^A0N,40,0^FDEleva^FS^XZ"
ZPL_TRES = ZPL + ZPL + ZPL

# Como o Mercado Livre entrega a etiqueta de duas vias: **um** ^XA com as duas
# colunas dentro, a segunda desenhada lá na frente (^FO360…).
ZPL_ML_DUAS_VIAS = (
    "^XA^CI28^LH10,0"
    "^FO40,15^BY2,,0^BCN,54,N,N^FDWZHY05395^FS"
    "^FO20,115^A0N,19,19^FB300,2,2,C^FH^FDBra_C3_A9 Divine 60ml^FS"
    "^CI28^LH0,0"
    "^FO360,15^BY2,,0^BCN,54,N,N^FDWZHY05395^FS"
    "^FO357,115^A0N,19,19^FB300,2,2,C^FH^FDBra_C3_A9 Divine 60ml^FS"
    "^XZ"
)


# --------------------------------------------------------------------------- #
# Formatos
# --------------------------------------------------------------------------- #


class TestFormatos:
    def test_os_embutidos_aparecem_sem_arquivo_nenhum(self):
        lista = formatos.carregar()
        assert [f.id for f in lista][: len(formatos.EMBUTIDOS)] == [
            f.id for f in formatos.EMBUTIDOS
        ]

    def test_o_padrao_e_o_do_mercado_livre_em_uma_coluna(self):
        padrao = formatos.formato_padrao()
        assert padrao.largura_mm == 100
        assert padrao.altura_mm == 150
        assert padrao.colunas == 1

    def test_duas_colunas_de_40x25_cabem_no_cabecote(self):
        duas = formatos.buscar("duas-40x25")
        assert duas.colunas == 2
        assert duas.largura_total_mm == 82  # 40 + 40 + 2 de espaço
        assert duas.largura_total_mm <= formatos.LARGURA_MAXIMA_MM

    def test_papel_mais_largo_que_o_cabecote_e_recusado(self):
        with pytest.raises(formatos.FormatoInvalido, match="110"):
            formatos.salvar(
                formatos.Formato("", "largo demais", 60, 40, colunas=2, espaco_colunas_mm=2)
            )

    def test_salvar_gera_o_id_a_partir_do_nome(self):
        salvo = formatos.salvar(formatos.Formato("", "Produto 40 × 25", 40, 25))
        assert salvo.id
        assert salvo.id in {f.id for f in formatos.carregar()}

    def test_dois_nomes_iguais_nao_se_atropelam(self):
        primeiro = formatos.salvar(formatos.Formato("", "Caixa", 40, 25))
        segundo = formatos.salvar(formatos.Formato("", "Caixa", 50, 30))
        assert primeiro.id != segundo.id

    def test_embutido_nao_se_apaga(self):
        with pytest.raises(formatos.FormatoInvalido, match="fábrica"):
            formatos.apagar(formatos.PADRAO)

    def test_duplicar_e_o_caminho_para_editar_um_embutido(self):
        copia = formatos.duplicar(formatos.PADRAO)
        assert not copia.embutido
        formatos.apagar(copia.id)  # a cópia, essa sim, se apaga

    def test_embutido_editado_continua_embutido_e_persiste(self):
        editado = formatos.salvar(
            formatos.Formato(formatos.PADRAO, "ML ajustado", 100, 152)
        )
        assert editado.embutido
        assert formatos.buscar(formatos.PADRAO).altura_mm == 152

    def test_restaurar_padroes_devolve_o_de_fabrica(self):
        formatos.salvar(formatos.Formato(formatos.PADRAO, "ML ajustado", 100, 152))
        formatos.restaurar_padroes()
        assert formatos.buscar(formatos.PADRAO).altura_mm == 150

    def test_arquivo_corrompido_nao_impede_de_abrir(self, dados_isolados):
        formatos.caminho().write_text("{isso não é json", encoding="utf-8")
        assert len(formatos.carregar()) == len(formatos.EMBUTIDOS)

    def test_densidade_invalida_e_recusada(self):
        with pytest.raises(formatos.FormatoInvalido, match="densidade"):
            formatos.salvar(formatos.Formato("", "estranho", 40, 25, dpmm=9))

    def test_polegadas_e_o_que_o_labelary_pede(self):
        assert formatos.formato_padrao().polegadas == (
            pytest.approx(3.937, abs=0.001),
            pytest.approx(5.9055, abs=0.001),
        )


class TestColunasNoZpl:
    """Quando o próprio arquivo já traz as duas colunas.

    É o caso da etiqueta de duas vias do Mercado Livre. Confundir os dois casos
    corta a coluna da direita fora e a operadora não descobre o motivo — foi o
    que aconteceu no primeiro teste no papel.
    """

    def test_o_embutido_do_ml_desenha_a_folha_inteira(self):
        ml = formatos.buscar("ml-2col")
        assert ml.colunas_no_zpl
        assert ml.colunas == 2
        assert ml.largura_mm == 40  # a etiqueta do rolo
        assert ml.altura_mm == 25
        assert ml.largura_desenho_mm == 82  # as duas mais o espaço: a folha

    def test_formato_comum_desenha_so_uma_etiqueta(self):
        assert formatos.buscar("duas-40x25").largura_desenho_mm == 40

    def test_marcar_colunas_no_zpl_com_uma_coluna_e_recusado(self):
        with pytest.raises(formatos.FormatoInvalido, match="mais de uma coluna"):
            formatos.salvar(
                formatos.Formato("", "sem sentido", 40, 25, colunas=1, colunas_no_zpl=True)
            )

    def test_a_folha_nao_e_montada_de_novo(self):
        """Uma etiqueta do arquivo já é uma folha — compor duplicaria tudo."""
        ml = formatos.buscar("ml-2col")
        folhas, _ = aplicacao._folhas(ZPL_ML_DUAS_VIAS * 3, ml)
        assert len(folhas) == 3
        assert folhas[0].width == 82 * 8

    def test_a_coluna_da_direita_aparece(self):
        ml = formatos.buscar("ml-2col")
        imagem = aplicacao._desenhar_uma(ZPL_ML_DUAS_VIAS, ml, 0).imagem
        metade = imagem.width // 2
        direita = imagem.crop((metade, 0, imagem.width, imagem.height))
        assert direita.point(lambda v: 255 if v < 128 else 0).getbbox() is not None

    def test_no_formato_errado_o_aviso_diz_o_que_fazer(self):
        """Não basta avisar que caiu fora: tem de dizer qual formato usar."""
        errado = formatos.buscar("duas-40x25")
        resultado = aplicacao._desenhar_uma(ZPL_ML_DUAS_VIAS, errado, 0)
        avisos = aplicacao._avisos_de(resultado, errado)
        assert any("já traz as colunas" in a for a in avisos)

    def test_no_formato_certo_nao_sobra_aviso(self):
        ml = formatos.buscar("ml-2col")
        resultado = aplicacao._desenhar_uma(ZPL_ML_DUAS_VIAS, ml, 0)
        assert aplicacao._avisos_de(resultado, ml) == []

    def test_o_motor_conta_ate_onde_o_zpl_desenhou(self):
        errado = formatos.buscar("duas-40x25")
        resultado = aplicacao._desenhar_uma(ZPL_ML_DUAS_VIAS, errado, 0)
        # A tela tem 320 pontos, mas o arquivo pede ~657.
        assert resultado.extensao_pontos[0] > 600

    def test_pela_api_a_folha_do_ml_e_uma_so(self, cliente):
        dados = cliente.post("/api/preview", json={
            "zpl": ZPL_ML_DUAS_VIAS, "formato_id": "ml-2col", "visao": "folha",
        }).json()
        assert dados["folhas"] == 1
        assert dados["geometria"]["largura_pontos"] == 82 * 8
        assert dados["avisos"] == []


class TestPreferencias:
    """O modo que funcionou tem de sobreviver ao fechar do programa.

    Descobrir que esta impressora precisa de TSPL custou duas tiras de etiqueta.
    Se a escolha se perdesse a cada abertura, o custo voltaria — e sempre no dia
    corrido, quando ninguém confere o seletor antes de clicar em Imprimir.
    """

    def test_sem_arquivo_vale_o_padrao(self):
        assert preferencias.carregar().da_impressora("LABEL").modo == "imagem"

    def test_lembra_o_modo_por_impressora(self):
        preferencias.lembrar(impressora="LABEL", modo="tspl")
        preferencias.lembrar(impressora="PDF", modo="imagem")
        guardado = preferencias.carregar()
        assert guardado.da_impressora("LABEL").modo == "tspl"
        assert guardado.da_impressora("PDF").modo == "imagem"

    def test_o_que_nao_veio_fica_como_estava(self):
        preferencias.lembrar(impressora="LABEL", modo="tspl", gap_mm=3)
        preferencias.lembrar(impressora="LABEL", modo="zpl")
        assert preferencias.carregar().da_impressora("LABEL").gap_mm == 3

    def test_arquivo_corrompido_nao_impede_de_abrir(self):
        preferencias.caminho().write_text("{quebrado", encoding="utf-8")
        assert preferencias.carregar().da_impressora("LABEL").modo == "imagem"

    def test_bom_do_windows_nao_apaga_a_preferencia(self):
        """O Bloco de Notas e o PowerShell põem um BOM na frente do arquivo.

        Lido como utf-8 puro, esse byte invisível quebra o JSON e a preferência
        voltava ao padrão sem avisar — "mudou sozinho".
        """
        preferencias.lembrar(impressora="LABEL", modo="tspl")
        cru = preferencias.caminho().read_bytes()
        preferencias.caminho().write_bytes(codecs.BOM_UTF8 + cru)
        assert preferencias.carregar().da_impressora("LABEL").modo == "tspl"

    def test_a_tela_recebe_as_preferencias_ao_abrir(self, cliente):
        preferencias.lembrar(impressora="LABEL", modo="tspl", formato_id="ml-2col")
        dados = cliente.get("/api/estado").json()
        assert dados["preferencias"]["impressoras"]["LABEL"]["modo"] == "tspl"
        assert dados["preferencias"]["formato_id"] == "ml-2col"

    def test_impressao_que_deu_certo_fixa_o_modo(self, cliente, monkeypatch):
        monkeypatch.setattr(windows, "enviar_bruto", lambda dados, **k:
                            windows.Envio(True, windows.Modo.TSPL, "LABEL", "ok"))
        cliente.post("/api/imprimir", json={
            "zpl": ZPL, "formato_id": "ml-2col", "modo": "tspl", "impressora": "LABEL",
        })
        assert preferencias.carregar().da_impressora("LABEL").modo == "tspl"

    def test_impressao_que_falhou_nao_fixa_nada(self, cliente, monkeypatch):
        monkeypatch.setattr(windows, "enviar_bruto", lambda dados, **k:
                            windows.Envio(False, windows.Modo.TSPL, "LABEL", "recusou"))
        cliente.post("/api/imprimir", json={
            "zpl": ZPL, "formato_id": "ml-2col", "modo": "tspl", "impressora": "LABEL",
        })
        assert preferencias.carregar().da_impressora("LABEL").modo == "imagem"


class TestTravaDoPapel:
    """O modo imagem não imprime com o papel do driver do tamanho errado.

    O que motivou a trava: o driver estava em 100 × 150 mm e o formato pedia
    82 × 25 mm. Saiu uma etiqueta impressa e cinco em branco — a impressora
    avançou os 150 mm da página que o Windows mandou.
    """

    def _driver_de(self, monkeypatch, largura_mm, altura_mm, avisos):
        monkeypatch.setattr(windows, "diagnostico", lambda *a, **k: {
            "impressora": "LABEL", "porta": "USB001", "driver": "LABEL",
            "dpi": [203, 203], "papel_mm": [largura_mm, altura_mm],
            "area_util_pontos": [800, 1200], "avisos": avisos,
        })

    def test_barra_antes_de_gastar_etiqueta(self, cliente, monkeypatch):
        self._driver_de(monkeypatch, 100.1, 150.1, ["o papel do driver não bate"])
        resposta = cliente.post("/api/imprimir", json={
            "zpl": ZPL_ML_DUAS_VIAS, "formato_id": "ml-2col", "modo": "imagem",
        }).json()
        assert resposta["precisa_confirmar"] is True
        assert resposta["ok"] is False
        assert "82" in resposta["detalhe"] and "TSPL" in resposta["detalhe"]

    def test_com_o_papel_certo_nao_atrapalha(self, cliente, monkeypatch):
        self._driver_de(monkeypatch, 82, 25, [])
        enviados = []
        monkeypatch.setattr(windows, "imprimir_imagem", lambda folhas, **k: (
            enviados.append(folhas) or windows.Envio(True, windows.Modo.IMAGEM, "LABEL", "ok")
        ))
        resposta = cliente.post("/api/imprimir", json={
            "zpl": ZPL_ML_DUAS_VIAS, "formato_id": "ml-2col", "modo": "imagem",
        }).json()
        assert resposta["ok"] is True
        assert not resposta.get("precisa_confirmar")
        assert enviados

    def test_forcar_passa_por_cima(self, cliente, monkeypatch):
        self._driver_de(monkeypatch, 100.1, 150.1, ["o papel do driver não bate"])
        monkeypatch.setattr(windows, "imprimir_imagem", lambda folhas, **k:
                            windows.Envio(True, windows.Modo.IMAGEM, "LABEL", "ok"))
        resposta = cliente.post("/api/imprimir", json={
            "zpl": ZPL_ML_DUAS_VIAS, "formato_id": "ml-2col", "modo": "imagem",
            "forcar": True,
        }).json()
        assert resposta["ok"] is True

    def test_tspl_nao_e_barrado_porque_manda_a_medida(self, cliente, monkeypatch):
        self._driver_de(monkeypatch, 100.1, 150.1, ["o papel do driver não bate"])
        pacotes = []
        monkeypatch.setattr(windows, "enviar_bruto", lambda dados, **k: (
            pacotes.append(dados) or windows.Envio(True, windows.Modo.TSPL, "LABEL", "ok")
        ))
        resposta = cliente.post("/api/imprimir", json={
            "zpl": ZPL_ML_DUAS_VIAS, "formato_id": "ml-2col", "modo": "tspl",
        }).json()
        assert resposta["ok"] is True
        assert b"SIZE 82 mm,25 mm" in pacotes[0]


# --------------------------------------------------------------------------- #
# TSPL — o pacote que sai pelo cabo
# --------------------------------------------------------------------------- #


class TestTspl:
    def _folha(self) -> Image.Image:
        imagem = Image.new("L", (320, 200), 255)
        imagem.paste(0, (10, 10, 100, 60))
        return imagem

    def test_cabecalho_leva_tamanho_e_gap_em_milimetros(self):
        pacote = windows.tspl_de_imagem(self._folha(), largura_mm=82, altura_mm=25, gap_mm=2)
        texto = pacote[:200].decode("ascii", errors="replace")
        assert "SIZE 82 mm,25 mm" in texto
        assert "GAP 2 mm,0 mm" in texto
        assert "CLS" in texto

    def test_termina_mandando_imprimir(self):
        pacote = windows.tspl_de_imagem(self._folha(), largura_mm=82, altura_mm=25, copias=3)
        assert pacote.rstrip().endswith(b"PRINT 1,3")

    def test_bit_zero_e_que_imprime(self):
        """A convenção do TSPL é o inverso da do ZPL.

        Já custou uma etiqueta inteiramente preta: no ``BITMAP`` o bit 0 queima
        o ponto e o bit 1 deixa em branco.
        """
        branca = Image.new("L", (16, 8), 255)
        preta = Image.new("L", (16, 8), 0)
        inicio = b"BITMAP 0,0,2,8,0,"

        dados_branca = windows.tspl_de_imagem(branca, largura_mm=10, altura_mm=10)
        dados_preta = windows.tspl_de_imagem(preta, largura_mm=10, altura_mm=10)
        corpo_branca = dados_branca.split(inicio)[1][:16]
        corpo_preta = dados_preta.split(inicio)[1][:16]

        assert corpo_branca == b"\xff" * 16  # tudo branco = bits 1
        assert corpo_preta == b"\x00" * 16  # tudo preto = bits 0

    def test_escuridao_entra_so_quando_pedida(self):
        sem = windows.tspl_de_imagem(self._folha(), largura_mm=82, altura_mm=25)
        com = windows.tspl_de_imagem(
            self._folha(), largura_mm=82, altura_mm=25, escuridao=12
        )
        assert b"DENSITY" not in sem[:100]
        assert b"DENSITY 12" in com[:100]

    def test_escuridao_fica_dentro_da_faixa_do_firmware(self):
        pacote = windows.tspl_de_imagem(
            self._folha(), largura_mm=82, altura_mm=25, escuridao=99
        )
        assert b"DENSITY 15" in pacote[:100]

    def test_sem_folha_nao_envia_nada(self):
        envio = windows.imprimir_tspl([], largura_mm=82, altura_mm=25)
        assert not envio.ok


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


class TestApi:
    def test_a_tela_abre(self, cliente):
        resposta = cliente.get("/")
        assert resposta.status_code == 200
        assert "Eleva" in resposta.text

    def test_estado_traz_formatos_e_impressoras(self, cliente):
        dados = cliente.get("/api/estado").json()
        assert dados["formato_padrao"] == formatos.PADRAO
        assert [i["nome"] for i in dados["impressoras"]] == ["LABEL", "PDF"]
        assert dados["modos"] == ["imagem", "tspl", "zpl"]

    def test_preview_devolve_imagem_e_geometria(self, cliente):
        dados = cliente.post("/api/preview", json={"zpl": ZPL}).json()
        assert dados["imagem"].startswith("data:image/png;base64,")
        assert dados["total"] == 1
        assert dados["geometria"]["etiqueta_pontos"] == [800, 1200]

    def test_preview_sem_zpl_reclama(self, cliente):
        assert cliente.post("/api/preview", json={"zpl": "  "}).status_code == 400

    def test_preview_da_folha_junta_as_colunas(self, cliente):
        dados = cliente.post("/api/preview", json={
            "zpl": ZPL_TRES, "formato_id": "duas-40x25", "visao": "folha",
        }).json()
        assert dados["total"] == 3
        assert dados["folhas"] == 2  # 3 etiquetas em 2 colunas = 2 folhas
        assert dados["geometria"]["largura_mm"] == 82

    def test_indice_alto_cai_na_ultima_etiqueta(self, cliente):
        dados = cliente.post("/api/preview", json={"zpl": ZPL_TRES, "indice": 40}).json()
        assert dados["indice"] == 2

    def test_medida_avulsa_sobrepoe_o_formato(self, cliente):
        dados = cliente.post("/api/preview", json={
            "zpl": ZPL, "largura_mm": 40, "altura_mm": 25,
        }).json()
        assert dados["geometria"]["etiqueta_pontos"] == [320, 200]

    def test_png_sai_no_tamanho_da_etiqueta(self, cliente):
        resposta = cliente.post("/api/png", json={"zpl": ZPL})
        assert resposta.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(resposta.content)).size == (800, 1200)

    def test_pdf_tem_uma_pagina_por_folha(self, cliente):
        resposta = cliente.post("/api/pdf", json={
            "zpl": ZPL_TRES, "formato_id": "duas-40x25",
        })
        assert resposta.content.startswith(b"%PDF")
        assert resposta.content.count(b"/Type /Page\n") == 2

    def test_imagem_vira_comando_gf(self, cliente):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 20), "black").save(buffer, format="PNG")
        conteudo = base64.b64encode(buffer.getvalue()).decode()
        dados = cliente.post("/api/imagem", json={
            "conteudo_base64": conteudo, "x": 10, "y": 20, "largura_pontos": 80,
        }).json()
        assert dados["zpl"].startswith("^FO10,20^GFA,")
        assert dados["zpl"].endswith("^FS")

    def test_arquivo_que_nao_e_imagem_reclama(self, cliente):
        resposta = cliente.post("/api/imagem", json={
            "conteudo_base64": base64.b64encode(b"isso nao e imagem").decode(),
        })
        assert resposta.status_code == 400

    def test_criar_e_apagar_formato_pela_api(self, cliente):
        criado = cliente.post("/api/formatos", json={
            "nome": "Teste 30x20", "largura_mm": 30, "altura_mm": 20,
        }).json()
        novo_id = criado["formato"]["id"]
        assert any(f["id"] == novo_id for f in criado["formatos"])

        restante = cliente.delete(f"/api/formatos/{novo_id}").json()
        assert all(f["id"] != novo_id for f in restante["formatos"])

    def test_api_recusa_formato_largo_demais(self, cliente):
        resposta = cliente.post("/api/formatos", json={
            "nome": "largo", "largura_mm": 60, "altura_mm": 40, "colunas": 2,
        })
        assert resposta.status_code == 400
        assert "110" in resposta.json()["detail"]

    def test_apagar_formato_inexistente_da_404(self, cliente):
        assert cliente.delete("/api/formatos/nao-existe").status_code == 404

    def test_imprimir_sem_zpl_reclama_antes_de_chamar_a_impressora(self, cliente):
        assert cliente.post("/api/imprimir", json={"zpl": ""}).status_code == 400


# --------------------------------------------------------------------------- #
# Etiqueta de teste
# --------------------------------------------------------------------------- #


class TestEtiquetaDeTeste:
    def test_cabe_no_formato_pequeno_sem_estourar(self):
        pequeno = formatos.buscar("etq-40x25")
        zpl = aplicacao.zpl_de_teste(pequeno, "imagem")
        resultado = render.renderizar(zpl, largura_mm=40, altura_mm=25)
        assert not [a for a in resultado.avisos if "fora da etiqueta" in a]

    def test_diz_o_formato_e_o_modo_na_propria_etiqueta(self):
        zpl = aplicacao.zpl_de_teste(formatos.formato_padrao(), "tspl")
        assert "100x150mm" in zpl
        assert "tspl" in zpl

    def test_tem_moldura_para_denunciar_deslocamento(self):
        zpl = aplicacao.zpl_de_teste(formatos.formato_padrao(), "imagem")
        assert "^FO2,2^GB" in zpl
