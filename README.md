# Eleva Impressão

Aplicativo local para **ver e imprimir etiqueta ZPL**. Faz o que o visualizador
do Labelary faz, mas **sem internet**, entendendo etiqueta de **duas colunas** e
imprimindo direto na térmica USB.

Não pede login, não fala com o Mercado Livre e não guarda credencial nenhuma.

---

## 📥 Baixar o programa

**Não precisa instalar nada, nem saber programar. É um arquivo só.**

### 👉 [Clique aqui para baixar](../../releases/latest)

Na página que abrir, procure a palavra **Assets** lá embaixo e clique em
**`Eleva Impressao 1.0.exe`**. O download tem uns 30 MB.

Depois:

1. Salve o arquivo na **Área de Trabalho**.
2. Dê **dois cliques** nele.
3. O programa demora uns **8 segundos** para abrir e mostra a marca ELEVA
   enquanto carrega. **Não clique duas vezes: espere.**

### O Windows mostrou uma tela azul dizendo "O Windows protegeu o seu PC"?

**Isso não é vírus.** Acontece porque o programa é novo e ainda não tem
assinatura digital paga. Para continuar:

- clique em **"Mais informações"**
- depois clique em **"Executar assim mesmo"**

Isso acontece só na primeira vez.

O manual completo de uso — como imprimir, o que faz o botão laranja "Ajustar
automático", e o que fazer se a impressão sair errada — está em
[`LEIA-ME.txt`](LEIA-ME.txt).

---

## Para desenvolver

```powershell
uv sync --extra dev          # dependências
uv run eleva-impressao       # abre a janela
```

```powershell
uv run pytest -q             # testes (offline)
uv run ruff check src tests  # lint
```

### Gerar o `.exe`

```powershell
uv run pyinstaller eleva-impressao.spec          # pasta com o programa
uv run pyinstaller eleva-impressao-unico.spec    # arquivo único
```

O passo a passo para publicar uma versão nova no GitHub está em
[`PUBLICAR.md`](PUBLICAR.md).

### O que tem dentro

| Preciso de… | Vá para |
|---|---|
| desenhar ZPL na tela | `src/eleva_impressao/render.py` |
| código de barras e QR | `src/eleva_impressao/barras.py` |
| formatos de etiqueta | `src/eleva_impressao/formatos.py` |
| ajuste automático do texto | `src/eleva_impressao/ajuste.py` |
| falar com a impressora | `src/eleva_impressao/windows.py` |
| a API local e as telas | `src/eleva_impressao/app.py`, `eleva_impressao/web/` |
| a janela do programa | `src/eleva_impressao/desktop.py` |

O contexto completo — por que cada decisão foi tomada, o que foi medido contra o
Labelary e o que ainda falta — está em [`IMPRESSAO.md`](IMPRESSAO.md).

### A decisão que sustenta o resto

**O preview e o papel saem da mesma função.** `render.py` gera o bitmap que
aparece na tela *e* o que vai para a impressora. Não existe o dia em que os dois
discordam.
