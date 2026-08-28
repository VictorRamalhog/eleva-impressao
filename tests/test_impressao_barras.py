"""Códigos de barras: o que um leitor precisa enxergar.

Os números conferidos aqui (quantidade de módulos, dígito verificador) não são
gosto pessoal — são a especificação. Se um deles mudar, a etiqueta continua
*parecendo* certa na tela e para de ler no coletor, que é o pior tipo de defeito
numa operação de expedição.
"""

from __future__ import annotations

import pytest

from eleva_impressao import barras


def modulos_por_simbolo(quantidade: int) -> int:
    """Code 128: 11 módulos por símbolo, mais 2 na barra final de parada."""
    return quantidade * 11 + 2


class TestCode128:
    def test_subset_c_encolhe_o_par_de_digitos(self):
        # 8 dígitos = início C + 4 pares + verificador + parada = 7 símbolos.
        simbolo = barras.code128("12345678", subset="C")
        assert simbolo.modulos == modulos_por_simbolo(7)

    def test_subset_b_gasta_um_simbolo_por_caractere(self):
        simbolo = barras.code128("12345678", subset="B")
        assert simbolo.modulos == modulos_por_simbolo(11)

    def test_automatico_escolhe_o_subset_c_para_digitos(self):
        assert barras.code128("12345678").modulos == barras.code128(
            "12345678", subset="C"
        ).modulos

    def test_automatico_volta_para_b_quando_aparece_letra(self):
        simbolo = barras.code128("AB12345678")
        # início B + A + B + troca para C + 4 pares + verificador + parada.
        assert simbolo.modulos == modulos_por_simbolo(10)

    def test_corrida_curta_de_digitos_no_meio_nao_paga_a_troca(self):
        # 4 dígitos no meio: trocar de subset custaria mais do que economiza.
        simbolo = barras.code128("A1234B")
        assert simbolo.modulos == modulos_por_simbolo(9)

    def test_verificador_entra_no_lugar_certo(self):
        # Exemplo canônico: início B (104) + '1'..'3' = soma 104+16*1+17*2+18*3.
        simbolo = barras.code128("123", subset="B")
        assert simbolo.modulos == modulos_por_simbolo(6)
        # Começa com barra e termina com barra (a parada fecha em barra).
        assert simbolo.elementos[0][1] is True
        assert simbolo.elementos[-1][1] is True

    def test_dado_vazio_e_recusado(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.code128("")

    def test_subset_c_recusa_quantidade_impar(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.code128("123", subset="C")

    def test_acento_nao_cabe_no_subset_b(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.code128("café", subset="B")


class TestCode39:
    def test_legenda_sai_com_os_asteriscos(self):
        # A Zebra imprime *TEXTO* na linha de interpretação; conferido no Labelary.
        assert barras.code39("ELEVA").texto_legivel == "*ELEVA*"

    def test_largura_com_a_razao_do_by(self):
        # 7 caracteres (5 + os dois asteriscos): 6 estreitos + 3 largos cada,
        # mais o espaço de separação entre eles.
        simbolo = barras.code39("ELEVA", razao=3.0)
        assert simbolo.modulos == 7 * (6 + 3 * 3) + 6

    def test_razao_menor_encurta_o_codigo(self):
        assert barras.code39("ELEVA", razao=2.0).modulos < barras.code39("ELEVA").modulos

    def test_caractere_fora_do_alfabeto(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.code39("eleva@casa")

    def test_verificador_opcional_acrescenta_um_caractere(self):
        com = barras.code39("ELEVA", verificador=True)
        sem = barras.code39("ELEVA")
        assert len(com.texto_legivel) == len(sem.texto_legivel) + 1


class TestEan:
    @pytest.mark.parametrize(
        ("sem_digito", "esperado"),
        [("789835644012", "7898356440129"), ("400638133393", "4006381333931")],
    )
    def test_calcula_o_digito_verificador(self, sem_digito, esperado):
        assert barras.ean13(sem_digito).texto_legivel == esperado

    def test_ean13_tem_95_modulos(self):
        assert barras.ean13("789835644012").modulos == 95

    def test_ean8_tem_67_modulos(self):
        assert barras.ean8("1234567").modulos == 67

    def test_upca_guarda_os_12_digitos_na_legenda(self):
        simbolo = barras.upca("12345678901")
        assert len(simbolo.texto_legivel) == 12
        assert simbolo.modulos == 95  # por dentro é um EAN-13 com zero à frente

    def test_letra_em_ean_e_recusada(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.ean13("78983564401A")

    def test_tamanho_errado_e_recusado(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.ean13("7898")


class TestIntercalado25:
    def test_completa_com_zero_a_esquerda_quando_impar(self):
        assert barras.intercalado25("12345").texto_legivel == "012345"

    def test_so_aceita_digitos(self):
        with pytest.raises(barras.CodigoInvalido):
            barras.intercalado25("12A45")


class TestQr:
    def test_matriz_e_quadrada(self):
        matriz = barras.qrcode("https://eleva.com.br")
        assert len({len(linha) for linha in matriz}) == 1
        assert len(matriz) == len(matriz[0])

    def test_correcao_maior_gera_matriz_maior_ou_igual(self):
        baixa = len(barras.qrcode("x" * 40, correcao="L"))
        alta = len(barras.qrcode("x" * 40, correcao="H"))
        assert alta >= baixa
