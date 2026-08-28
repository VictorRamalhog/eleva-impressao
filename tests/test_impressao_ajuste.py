"""Ajuste automático de posição.

O primeiro teste é o mais importante do arquivo: **nada do que é dado do produto
pode mudar**. Nome, SKU e código de barras vêm do cadastro; um ajuste que
"melhore" a etiqueta alterando o que está escrito nela imprime mentira, e uma
etiqueta errada só é descoberta no cliente.

O resto verifica o que o ajuste tem de resolver de verdade: nome comprido que
passava por cima do SKU, código de barras mais largo que a etiqueta, e texto que
o ``^FB`` cortava por declarar menos linhas do que o necessário.
"""

from __future__ import annotations

import pytest

from eleva_impressao import ajuste, formatos, render


@pytest.fixture
def duas_vias():
    return formatos.buscar("ml-2col")


def etiqueta_ml(nome: str, sku: str, codigo: str) -> str:
    """O molde que o Mercado Livre entrega, trocando só o produto."""
    def via(dx: int, lh: int) -> str:
        return (
            f"^LH{lh},0\n"
            f"^FO{40 + dx},15^BY2,,0^BCN,54,N,N^FD{codigo}^FS\n"
            f"^FO{105 + dx},75^A0N,20,25^FD{codigo}^FS\n"
            f"^FO{20 + dx},115^A0N,19,19^FB300,2,2,C^FD{nome}^FS\n"
            f"^FO{120 + dx},162^A0N,18,18^FDSKU: {sku}^FS\n"
        )
    return "^XA^CI28\n" + via(0, 10) + "^CI28\n" + via(320, 0) + "^XZ"


NOME_COMPRIDO = (
    "Shampoo Reconstrutor Profissional Absolutely Smooth "
    "Nutricao Intensa 1000ml Kit Completo"
)


def desenho(zpl: str, formato) -> render.Resultado:
    return render.renderizar(
        zpl, largura_mm=formato.largura_desenho_mm, altura_mm=formato.altura_mm,
        dpmm=formato.dpmm,
    )


def cabe(zpl: str, formato) -> bool:
    resultado = desenho(zpl, formato)
    largura = int(round(formato.largura_desenho_mm * formato.dpmm))
    return (
        resultado.extensao_pontos[0] <= largura
        and resultado.extensao_pontos[1] <= formato.altura_pontos
    )


# --------------------------------------------------------------------------- #
# A regra que não se negocia
# --------------------------------------------------------------------------- #


class TestNaoMexeNoConteudo:
    @pytest.mark.parametrize(
        ("nome", "sku", "codigo"),
        [
            ("Mascara 60ml", "888218", "WZHY05395"),
            (NOME_COMPRIDO, "888218", "WZHY05395"),
            ("Gel", "7", "AB12"),
            ("Mascara 60ml", "9912", "WZHY05395-LOTE-2026-0042"),
        ],
    )
    def test_os_dados_saem_identicos(self, nome, sku, codigo, duas_vias):
        original = etiqueta_ml(nome, sku, codigo)
        ajustado = ajuste.ajustar(original, duas_vias).zpl
        assert ajuste._dados_de(ajustado) == ajuste._dados_de(original)

    def test_acento_escapado_continua_escapado(self, duas_vias):
        original = (
            "^XA^CI28^FO20,20^A0N,19,19^FB300,2,2,C^FH"
            "^FDBra_C3_A9 Divine M_C3_A1scara^FS^XZ"
        )
        ajustado = ajuste.ajustar(original, duas_vias).zpl
        assert "Bra_C3_A9 Divine M_C3_A1scara" in ajustado

    def test_a_quantidade_de_campos_nao_muda(self, duas_vias):
        original = etiqueta_ml(NOME_COMPRIDO, "888218", "WZHY05395")
        ajustado = ajuste.ajustar(original, duas_vias).zpl
        assert ajustado.count("^FS") == original.count("^FS")
        assert ajustado.count("^FD") == original.count("^FD")


# --------------------------------------------------------------------------- #
# O que ele tem de resolver
# --------------------------------------------------------------------------- #


class TestEncaixe:
    def test_nome_comprido_passa_a_caber(self, duas_vias):
        original = etiqueta_ml(NOME_COMPRIDO, "888218", "WZHY05395")
        ajustado = ajuste.ajustar(original, duas_vias).zpl
        assert cabe(ajustado, duas_vias)

    def test_o_fb_ganha_as_linhas_que_o_texto_precisa(self, duas_vias):
        """Sem isso, o ^FB corta o texto e a informação some da etiqueta.

        No molde do ML o bloco declara 2 linhas. Um nome que precisa de 3 perde
        o fim — e ninguém percebe, porque a etiqueta sai bonita.
        """
        original = etiqueta_ml(NOME_COMPRIDO, "888218", "WZHY05395")
        assert any("não coube" in a for a in desenho(original, duas_vias).avisos)

        resultado = ajuste.ajustar(original, duas_vias)
        assert not [a for a in desenho(resultado.zpl, duas_vias).avisos if "não coube" in a]
        assert any("3 linha" in m for m in resultado.mudancas)

    def test_nome_curto_reaproveita_a_linha_sobrando(self, duas_vias):
        resultado = ajuste.ajustar(etiqueta_ml("Gel 30ml", "77", "AB12"), duas_vias)
        assert any("1 linha" in m for m in resultado.mudancas)
        assert cabe(resultado.zpl, duas_vias)

    def test_codigo_largo_estreita_o_modulo(self, duas_vias):
        original = etiqueta_ml("Mascara 60ml", "9912", "WZHY05395-LOTE-2026-0042")
        assert not cabe(original, duas_vias)

        resultado = ajuste.ajustar(original, duas_vias)
        assert cabe(resultado.zpl, duas_vias)
        assert any("módulo" in m for m in resultado.mudancas)

    def test_codigo_impossivel_avisa_em_vez_de_espremer(self, duas_vias):
        """Barra espremida além do módulo 1 não é lida pelo coletor.

        Encurtar o dado resolveria, mas o dado é do produto — a decisão é de
        quem opera, não do programa.
        """
        resultado = ajuste.ajustar(
            etiqueta_ml("Mascara", "1", "WZHY05395-LOTE-2026-0042"), duas_vias
        )
        assert any("mais largo que a etiqueta" in a for a in resultado.avisos)

    def test_nada_fica_sobreposto(self, duas_vias):
        """Os campos de uma coluna não podem se pisar depois do ajuste."""
        ajustado = ajuste.ajustar(
            etiqueta_ml(NOME_COMPRIDO, "888218", "WZHY05395"), duas_vias
        ).zpl
        pecas = [
            p for p in ajuste._pecas_da_etiqueta(render.separar_etiquetas(ajustado)[0])
            if p.tipo != "vazio" and p.x + p.lh[0] < 336
        ]
        faixas = sorted(
            (p.y + p.lh[1], p.y + p.lh[1] + p.altura_calculada) for p in pecas
        )
        for (_, fim), (inicio_seguinte, _) in zip(faixas, faixas[1:], strict=False):
            assert inicio_seguinte >= fim, "dois campos ocupam a mesma altura"

    def test_as_duas_colunas_ficam_simetricas(self, duas_vias):
        """A via da direita é cópia da esquerda; o ajuste não pode desalinhá-las."""
        ajustado = ajuste.ajustar(
            etiqueta_ml("Mascara 60ml", "888218", "WZHY05395"), duas_vias
        ).zpl
        pecas = ajuste._pecas_da_etiqueta(render.separar_etiquetas(ajustado)[0])
        passo = duas_vias.largura_pontos + duas_vias.espaco_pontos
        esquerda = [p for p in pecas if p.tipo != "vazio" and p.x + p.lh[0] < passo]
        direita = [p for p in pecas if p.tipo != "vazio" and p.x + p.lh[0] >= passo]
        assert len(esquerda) == len(direita)
        for a, b in zip(esquerda, direita, strict=True):
            assert (b.x + b.lh[0]) - (a.x + a.lh[0]) == passo
            assert a.y + a.lh[1] == b.y + b.lh[1]

    def test_uma_coluna_tambem_e_ajustada(self):
        """O ajuste não é exclusivo de duas colunas — vale para qualquer formato."""
        cem = formatos.buscar("ml-100x150")
        original = (
            "^XA^CI28^FO600,1100^A0N,80,0^FDProduto de teste^FS"
            "^FO600,1180^BY4^BCN,200,Y,N,N^FD2000012345678^FS^XZ"
        )
        assert not cabe(original, cem)
        assert cabe(ajuste.ajustar(original, cem).zpl, cem)


# --------------------------------------------------------------------------- #
# Comportamento geral
# --------------------------------------------------------------------------- #


class TestComportamento:
    def test_zpl_vazio_e_recusado(self, duas_vias):
        with pytest.raises(ajuste.NaoAjustavel):
            ajuste.ajustar("   ", duas_vias)

    def test_ajustar_duas_vezes_nao_muda_mais(self, duas_vias):
        """Idempotência: a segunda passada não deve ficar mexendo à toa."""
        uma = ajuste.ajustar(etiqueta_ml(NOME_COMPRIDO, "8", "WZHY05395"), duas_vias)
        duas = ajuste.ajustar(uma.zpl, duas_vias)
        assert not duas.mudou

    def test_varias_etiquetas_no_arquivo_sao_todas_ajustadas(self, duas_vias):
        duas = etiqueta_ml("Gel", "1", "AB12") + etiqueta_ml(NOME_COMPRIDO, "2", "AB13")
        resultado = ajuste.ajustar(duas, duas_vias)
        assert render.contar_etiquetas(resultado.zpl) == 2
        for i in range(2):
            imagem = render.renderizar(
                resultado.zpl, largura_mm=duas_vias.largura_desenho_mm,
                altura_mm=duas_vias.altura_mm, indice=i,
            )
            assert imagem.extensao_pontos[0] <= 656

    def test_grafico_embutido_nao_e_perdido(self, duas_vias):
        """``^GF`` não se redimensiona por parâmetro, mas não pode sumir."""
        com_imagem = (
            "^XA^CI28^FO10,10^GFA,8,8,1,FFFFFFFFFFFFFFFF^FS"
            "^FO10,60^A0N,19,19^FDProduto^FS^XZ"
        )
        ajustado = ajuste.ajustar(com_imagem, duas_vias).zpl
        assert "^GFA,8,8,1,FFFFFFFFFFFFFFFF" in ajustado


class TestPelaApi:
    def test_o_botao_devolve_o_zpl_ajustado(self, cliente_impressao):
        original = etiqueta_ml(NOME_COMPRIDO, "888218", "WZHY05395")
        dados = cliente_impressao.post("/api/ajustar", json={
            "zpl": original, "formato_id": "ml-2col",
        }).json()
        assert dados["mudou"] is True
        assert dados["mudancas"]
        assert ajuste._dados_de(dados["zpl"]) == ajuste._dados_de(original)

    def test_zpl_vazio_da_400(self, cliente_impressao):
        resposta = cliente_impressao.post("/api/ajustar", json={"zpl": ""})
        assert resposta.status_code == 400
