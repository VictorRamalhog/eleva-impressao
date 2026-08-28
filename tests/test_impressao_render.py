"""Motor ZPL → bitmap.

Os números deste arquivo não foram inventados: cada caixa esperada foi medida na
API do Labelary (a referência de como uma Zebra imprime) durante a calibração, e
está fixada aqui para que uma mudança no motor não afaste em silêncio o preview
do que a impressora faria. Os testes rodam **offline** — o Labelary já falou,
estes são os recados que ele deixou.
"""

from __future__ import annotations

import pytest
from PIL import Image

from eleva_impressao import graficos, render


def caixa_de_tinta(imagem: Image.Image) -> tuple[int, int, int, int] | None:
    """Retângulo que contém tudo o que foi desenhado."""
    return imagem.point(lambda v: 255 if v < 128 else 0).getbbox()


def desenhar(zpl: str, **kwargs) -> Image.Image:
    padrao = {"largura_mm": 101.6, "altura_mm": 152.4}
    return render.renderizar(zpl, **{**padrao, **kwargs}).imagem


# --------------------------------------------------------------------------- #
# Leitura do ZPL
# --------------------------------------------------------------------------- #


class TestAnalise:
    def test_separa_comando_e_parametros(self):
        comandos = render.analisar("^FO50,60^A0N,40,0^FDoi^FS")
        assert [c.nome for c in comandos] == ["FO", "A0", "FD", "FS"]
        assert comandos[0].params == "50,60"
        assert comandos[1].params == "N,40,0"
        assert comandos[2].params == "oi"

    def test_dado_do_fd_vai_ate_o_proximo_comando(self):
        comandos = render.analisar("^FDtexto com, vírgula e espaço^FS")
        assert comandos[0].params == "texto com, vírgula e espaço"

    def test_conta_uma_etiqueta_por_xa(self):
        assert render.contar_etiquetas("^XA^FDa^FS^XZ\n^XA^FDb^FS^XZ") == 2

    def test_zpl_sem_xa_ainda_conta_como_uma(self):
        # Vale desenhar o que der: melhor um preview do que uma tela vazia.
        assert render.contar_etiquetas("^FO10,10^FDsolto^FS") == 1

    def test_numero_ausente_cai_no_padrao(self):
        comando = render.analisar("^GB100")[0]
        assert comando.numero(0) == 100
        assert comando.numero(3, 7) == 7


# --------------------------------------------------------------------------- #
# Texto
# --------------------------------------------------------------------------- #


class TestTexto:
    @pytest.mark.parametrize(("altura", "esperada"), [(30, 23), (40, 31), (60, 47), (80, 62)])
    def test_altura_da_maiuscula_bate_com_a_zebra(self, altura, esperada):
        """No ^A0 o ``h`` é a célula do caractere; a maiúscula dá 0,775 dela.

        Valores medidos no Labelary para 8 dpmm.
        """
        caixa = caixa_de_tinta(desenhar(f"^XA^FO100,200^A0N,{altura},0^FDHAMBURGER^FS^XZ"))
        assert abs((caixa[3] - caixa[1]) - esperada) <= 1

    def test_fo_posiciona_pelo_topo_da_celula(self):
        caixa = caixa_de_tinta(desenhar("^XA^FO100,200^A0N,60,0^FDHH^FS^XZ"))
        assert abs(caixa[1] - 200) <= 1
        # A tinta começa alguns pontos depois do x pedido porque o glifo tem
        # folga à esquerda — a Zebra também tem: medido 104 no Labelary para
        # este mesmo ^FO100.
        assert 100 <= caixa[0] <= 106

    def test_ft_posiciona_pela_linha_de_base(self):
        # A linha de base fica embaixo: o desenho sobe em relação ao ^FO.
        com_fo = caixa_de_tinta(desenhar("^XA^FO100,200^A0N,60,0^FDHH^FS^XZ"))
        com_ft = caixa_de_tinta(desenhar("^XA^FT100,200^A0N,60,0^FDHH^FS^XZ"))
        assert com_ft[1] < com_fo[1]
        assert abs((com_fo[1] - com_ft[1]) - 47) <= 2  # 47 = maiúscula de 60

    def test_cf_vale_para_quem_nao_declara_fonte(self):
        com_cf = caixa_de_tinta(desenhar("^XA^CF0,60^FO100,200^FDHH^FS^XZ"))
        explicito = caixa_de_tinta(desenhar("^XA^FO100,200^A0N,60,0^FDHH^FS^XZ"))
        assert com_cf == explicito

    def test_fb_quebra_por_palavra(self):
        largo = desenhar(
            "^XA^FO40,40^A0N,40,0^FB400,4,0,L,0"
            "^FDPerfume Importado Agua de Colonia Masculino^FS^XZ"
        )
        caixa = caixa_de_tinta(largo)
        assert caixa[2] - caixa[0] <= 400  # não passa da caixa
        assert caixa[3] - caixa[1] > 40  # e usou mais de uma linha

    def test_fb_avisa_quando_corta(self):
        resultado = render.renderizar(
            "^XA^FO40,40^A0N,40,0^FB300,1,0,L,0"
            "^FDtexto que nao cabe de jeito nenhum em uma linha so^FS^XZ",
            largura_mm=101.6, altura_mm=152.4,
        )
        assert any("não coube" in aviso for aviso in resultado.avisos)

    def test_fb_centralizado_fica_no_meio_da_caixa(self):
        caixa = caixa_de_tinta(
            desenhar("^XA^FO40,40^A0N,40,0^FB400,1,0,C,0^FDcentro^FS^XZ")
        )
        meio_texto = (caixa[0] + caixa[2]) / 2
        assert abs(meio_texto - (40 + 200)) <= 6

    def test_fb_a_direita_encosta_na_borda_da_caixa(self):
        caixa = caixa_de_tinta(
            desenhar("^XA^FO40,40^A0N,40,0^FB400,1,0,R,0^FDdireita^FS^XZ")
        )
        assert abs(caixa[2] - 440) <= 6

    def test_barra_e_comercial_quebra_a_linha(self):
        uma = caixa_de_tinta(desenhar("^XA^FO40,40^A0N,30,0^FB400,3,0,L,0^FDAA^FS^XZ"))
        duas = caixa_de_tinta(
            desenhar("^XA^FO40,40^A0N,30,0^FB400,3,0,L,0^FDAA\\&BB^FS^XZ")
        )
        assert duas[3] > uma[3]

    def test_fh_decodifica_o_escape_hexadecimal(self):
        # _C3_A9 é 'é' em UTF-8: tem de desenhar algo, e não a sequência crua.
        com_escape = desenhar("^XA^CI28^FH^FO40,40^A0N,40,0^FDcaf_C3_A9^FS^XZ")
        literal = desenhar("^XA^CI28^FO40,40^A0N,40,0^FDcafé^FS^XZ")
        assert caixa_de_tinta(com_escape) == caixa_de_tinta(literal)

    def test_sem_a_fonte_preferida_a_calibracao_se_refaz(self, monkeypatch):
        """A Arial Narrow vem com o Office, não com o Windows.

        Num PC sem ela o motor cai na Arial Bold. Como a calibração guarda
        *proporções* (altura da maiúscula e largura da amostra) em vez de um
        corpo de fonte fixo, ela se recalcula da métrica real — e a quebra de
        linha continua caindo na mesma palavra. Sem isso, a mesma remessa sairia
        com layout diferente em cada máquina.
        """
        amostra = "Brae Divine Absolutely Smooth Mascara 60ml"
        antes = {
            h: (render.largura_do_texto(amostra, "0", h),
                render.linhas_do_texto(amostra, 296, "0", h))
            for h in (18, 19, 20, 30)
        }

        monkeypatch.setattr(render, "_FONTES_ESCALAVEIS", ("arialbd.ttf",))
        render._metrica.cache_clear()
        render._fonte_para.cache_clear()
        try:
            for h, (largura, linhas) in antes.items():
                nova = render.largura_do_texto(amostra, "0", h)
                assert abs(nova - largura) / largura < 0.05, f"largura mudou em h={h}"
                assert render.linhas_do_texto(amostra, 296, "0", h) == linhas
        finally:
            render._metrica.cache_clear()
            render._fonte_para.cache_clear()

    def test_texto_fora_da_etiqueta_vira_aviso(self):
        resultado = render.renderizar(
            "^XA^FO2000,200^A0N,40,0^FDlonge^FS^XZ", largura_mm=101.6, altura_mm=152.4
        )
        assert any("fora da etiqueta" in aviso for aviso in resultado.avisos)


# --------------------------------------------------------------------------- #
# Códigos de barras — geometria conferida contra o Labelary
# --------------------------------------------------------------------------- #


class TestBarrasNaEtiqueta:
    @pytest.mark.parametrize(
        ("zpl", "esperada"),
        [
            # Sem modo, o ^BC fica no subset B do começo ao fim.
            ("^XA^BY3^FO100,300^BCN,120,Y,N,N^FD12345678^FS^XZ", (100, 300, 469, 447)),
            # Modo 'A' liga a troca automática para o subset C: encolhe.
            ("^XA^BY3^FO100,300^BCN,120,Y,N,N,A^FD12345678^FS^XZ", (100, 300, 337, 447)),
            ("^XA^BY2^FO100,300^B3N,N,90,Y,N^FDELEVA-123^FS^XZ", (100, 300, 450, 410)),
            ("^XA^BY2^FO100,300^B2N,80,Y,N,N^FD12345678^FS^XZ", (100, 300, 262, 400)),
            ("^XA^BY2^FO100,300^B8N,80,Y,N^FD1234567^FS^XZ", (100, 300, 234, 398)),
            # O QR sai 10 pontos abaixo do ^FO — é assim na Zebra.
            ("^XA^FO100,300^BQN,2,6^FDQA,ELEVA123^FS^XZ", (100, 310, 226, 436)),
        ],
    )
    def test_caixa_igual_a_do_labelary(self, zpl, esperada):
        assert caixa_de_tinta(desenhar(zpl)) == esperada

    def test_folga_de_seis_pontos_antes_da_legenda(self):
        # Barras de 60 pontos a partir de y=300: a legenda começa em 366.
        imagem = desenhar("^XA^BY2^FO100,300^BCN,60,Y,N,N^FD12345678^FS^XZ")
        pontos = imagem.load()
        vazias = [
            y for y in range(360, 366)
            if all(pontos[x, y] >= 128 for x in range(90, 500))
        ]
        assert vazias == [360, 361, 362, 363, 364, 365]

    def test_legenda_acima_sobe_para_fora_do_campo(self):
        caixa = caixa_de_tinta(desenhar("^XA^BY2^FO100,300^B3N,N,90,Y,Y^FDELEVA^FS^XZ"))
        assert caixa[1] < 300  # o texto ficou acima da origem
        assert caixa[3] == 390  # as barras continuam começando em 300

    def test_ean13_joga_o_primeiro_digito_para_fora(self):
        caixa = caixa_de_tinta(desenhar("^XA^BY2^FO100,300^BEN,80,Y,N^FD789835644012^FS^XZ"))
        assert caixa[0] < 100  # o dígito de fora avança para a esquerda

    def test_dado_invalido_vira_aviso_e_nao_explosao(self):
        resultado = render.renderizar(
            "^XA^BY2^FO100,300^BEN,80,Y,N^FDnao-e-numero^FS^XZ",
            largura_mm=101.6, altura_mm=152.4,
        )
        assert any("código de barras não gerado" in a for a in resultado.avisos)

    def test_simbologia_2d_sem_suporte_avisa_em_vez_de_sumir(self):
        resultado = render.renderizar(
            "^XA^FO100,300^BXN,6,200^FDdados^FS^XZ", largura_mm=101.6, altura_mm=152.4
        )
        assert any("DATAMATRIX" in a for a in resultado.avisos)
        assert caixa_de_tinta(resultado.imagem) is not None  # marcou o lugar


# --------------------------------------------------------------------------- #
# Gráficos
# --------------------------------------------------------------------------- #


class TestGraficos:
    def test_caixa_do_gb_tem_o_tamanho_pedido(self):
        caixa = caixa_de_tinta(desenhar("^XA^FO100,100^GB300,200,4^FS^XZ"))
        assert caixa == (100, 100, 400, 300)

    def test_gb_mais_fino_que_a_espessura_vira_linha_cheia(self):
        caixa = caixa_de_tinta(desenhar("^XA^FO100,100^GB300,2,2^FS^XZ"))
        assert caixa == (100, 100, 400, 102)

    def test_fr_inverte_o_que_esta_embaixo(self):
        # Caixa preta sólida com texto invertido: sobra branco no meio.
        imagem = desenhar(
            "^XA^FO100,100^GB300,100,100^FS^FO120,120^FR^A0N,60,0^FDAB^FS^XZ"
        )
        pontos = imagem.load()
        assert pontos[105, 150] < 128  # a caixa está preta
        brancos = sum(
            1 for x in range(120, 300) for y in range(120, 180) if pontos[x, y] >= 128
        )
        assert brancos > 100  # o texto abriu buraco branco na caixa

    def test_gf_volta_igual_ao_que_entrou(self):
        original = Image.new("L", (48, 24), 255)
        for x in range(0, 48, 4):
            for y in range(24):
                original.putpixel((x, y), 0)
        campo = graficos.campo_de_imagem(original, 40, 40)
        desenhada = desenhar(f"^XA{campo}^XZ").crop((40, 40, 88, 64))
        assert desenhada.tobytes() == original.tobytes()

    def test_gf_sem_compressao_da_o_mesmo_desenho(self):
        original = Image.new("L", (32, 16), 255)
        original.paste(0, (4, 4, 20, 12))
        preparada = graficos.preparar(original)
        com = desenhar(f"^XA^FO10,10{graficos.para_gf(preparada)}^FS^XZ")
        sem = desenhar(f"^XA^FO10,10{graficos.para_gf(preparada, comprimir=False)}^FS^XZ")
        assert com.tobytes() == sem.tobytes()

    def test_png_transparente_nao_vira_retangulo_preto(self):
        transparente = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        transparente.paste((0, 0, 0, 255), (5, 5, 15, 15))
        preparada = graficos.preparar(transparente)
        assert preparada.getpixel((0, 0)) == 255  # fundo continua branco
        assert preparada.getpixel((10, 10)) == 0

    def test_gf_quebrado_vira_aviso(self):
        resultado = render.renderizar(
            "^XA^FO10,10^GFA,100,100,10,:Z64:lixo-que-nao-e-base64:0000^FS^XZ",
            largura_mm=101.6, altura_mm=152.4,
        )
        assert any("^GF" in aviso for aviso in resultado.avisos)


# --------------------------------------------------------------------------- #
# Etiqueta e folha
# --------------------------------------------------------------------------- #


class TestEtiqueta:
    def test_tamanho_em_pontos_vem_do_dpmm(self):
        assert desenhar("^XA^FDx^FS^XZ", largura_mm=100, altura_mm=150).size == (800, 1200)
        assert desenhar(
            "^XA^FDx^FS^XZ", largura_mm=100, altura_mm=150, dpmm=12
        ).size == (1200, 1800)

    def test_indice_escolhe_a_etiqueta(self):
        zpl = "^XA^FO40,40^A0N,60,0^FDAAA^FS^XZ^XA^FO400,600^A0N,60,0^FDBBB^FS^XZ"
        primeira = caixa_de_tinta(desenhar(zpl, indice=0))
        segunda = caixa_de_tinta(desenhar(zpl, indice=1))
        assert 40 <= primeira[0] <= 46
        assert 400 <= segunda[0] <= 406

    def test_indice_fora_da_faixa_cai_na_ultima(self):
        zpl = "^XA^FDa^FS^XZ^XA^FDb^FS^XZ"
        assert render.renderizar(zpl, indice=99).imagem is not None

    def test_rotacao_troca_largura_por_altura(self):
        girada = desenhar("^XA^FDx^FS^XZ", largura_mm=100, altura_mm=150, rotacao=90)
        assert girada.size == (1200, 800)

    def test_bitonal_nao_deixa_cinza(self):
        imagem = desenhar("^XA^FO40,40^A0N,60,0^FDAA^FS^XZ", bitonal=True)
        assert set(imagem.tobytes()) <= {0, 255}

    def test_po_invertido_gira_a_etiqueta_inteira(self):
        normal = caixa_de_tinta(desenhar("^XA^FO40,40^A0N,60,0^FDAA^FS^XZ"))
        invertida = caixa_de_tinta(desenhar("^XA^POI^FO40,40^A0N,60,0^FDAA^FS^XZ"))
        assert invertida[0] > normal[0]  # foi parar do outro lado

    def test_comando_desconhecido_nao_derruba_a_etiqueta(self):
        resultado = render.renderizar(
            "^XA^ZZ123^FO40,40^A0N,40,0^FDsegue^FS^XZ",
            largura_mm=101.6, altura_mm=152.4,
        )
        assert "^ZZ" in resultado.comandos_ignorados
        assert caixa_de_tinta(resultado.imagem) is not None

    def test_zpl_vazio_e_recusado(self):
        with pytest.raises(render.ZplInvalido):
            render.renderizar("   ")


class TestFolha:
    def _etiquetas(self, quantidade: int) -> list[Image.Image]:
        return [Image.new("L", (320, 200), 255) for _ in range(quantidade)]

    def test_uma_coluna_devolve_as_etiquetas_como_estao(self):
        etiquetas = self._etiquetas(3)
        assert render.compor(etiquetas, colunas=1) == etiquetas

    def test_duas_colunas_juntam_duas_por_folha(self):
        folhas = render.compor(self._etiquetas(5), colunas=2, espaco_pontos=16)
        assert len(folhas) == 3
        assert folhas[0].size == (320 * 2 + 16, 200)

    def test_folha_incompleta_mantem_a_largura_cheia(self):
        # A quinta etiqueta fica sozinha, mas o papel continua com dois lugares:
        # sem isso o avanço do rolo sai errado na próxima.
        folhas = render.compor(self._etiquetas(5), colunas=2, espaco_pontos=16)
        assert folhas[-1].size == folhas[0].size

    def test_margem_entra_dos_dois_lados(self):
        folhas = render.compor(self._etiquetas(2), colunas=2, espaco_pontos=10, margem_pontos=8)
        assert folhas[0].size == (320 * 2 + 10 + 16, 200 + 16)

    def test_sem_etiqueta_nao_ha_folha(self):
        assert render.compor([], colunas=2) == []
