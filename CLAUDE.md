# Eleva Impressão — contexto para o Claude Code

Aplicativo local que vê e imprime etiqueta ZPL. **É só isso.** Não fala com o
Mercado Livre, não pede login, não guarda credencial, e não tem servidor
exposto — sobe em `127.0.0.1` numa porta sorteada pelo SO.

Leia [`IMPRESSAO.md`](IMPRESSAO.md): é a fonte da verdade sobre o porquê de cada
decisão, o que foi medido contra o Labelary e o que ainda falta.

## Regras que não se negociam

- **O preview e o papel saem da mesma função.** `render.py` gera o bitmap que
  aparece na tela **e** o que vai para a impressora. Não existe o dia em que os
  dois discordam — e é essa propriedade que faz o "ajustar automático" ser
  confiável, porque "cabe em 2 linhas" aqui é o que sai no papel.
- **Nada de rede obrigatória.** O Labelary existe como *segundo* motor, para os
  comandos que ainda não foram implementados em casa. O programa tem de
  funcionar com a internet caída.
- **O ajuste nunca toca em `^FD`.** Mexer no conteúdo do campo é mudar o que a
  etiqueta diz. Há teste comparando os dados antes e depois.
- **Nomes em português** para o domínio (`largura_do_texto`, `folha`,
  `formato`), em inglês para infraestrutura (`client`, `render`).
- **Todo teste roda offline.** Sem impressora de verdade e sem internet — a
  fixture `cliente_impressao` finge as duas coisas.

## Comandos

```powershell
uv sync --extra dev                              # dependências
uv run eleva-impressao                           # abre a janela
uv run pytest -q                                 # testes (offline)
uv run ruff check src tests                      # lint
uv run pyinstaller eleva-impressao.spec          # .exe em pasta
uv run pyinstaller eleva-impressao-unico.spec    # .exe único
```

## Onde mexer

| Preciso de… | Vá para |
|---|---|
| desenhar ZPL → bitmap | `render.py` |
| etiqueta em PDF → bitmap | `pdf.py` |
| código de barras e QR | `barras.py` |
| formatos de etiqueta | `formatos.py` |
| ajuste automático do texto | `ajuste.py` |
| gráficos (`^GF`) | `graficos.py` |
| impressora, TSPL e ZPL cru | `windows.py` |
| preferência por impressora | `preferencias.py` |
| API local e telas | `app.py`, `web/` |
| a janela do programa | `desktop.py` |

Todos em `src/eleva_impressao/`.

## Armadilhas já resolvidas — não reintroduzir

- **O ajuste automático mede, não adivinha — e nunca toca em `^FD`.** Ele usa a
  mesma medida do desenho (`render.largura_do_texto`), então "cabe em 2 linhas"
  aqui é o que sai no papel. Duas armadilhas já pagas: medir texto ignorando o
  `w` do `^A0N,h,w` (que estica o glifo) e agrupar as faixas pela altura
  **recalculada** em vez da original — nesta, um nome que cresceu encostava no
  SKU, os dois viravam uma faixa e o ajuste jogava o SKU para fora da etiqueta.
- **No modo imagem, quem manda no tamanho do papel é o Windows.** Driver em
  100 × 150 mm com etiqueta de 25 mm = a impressora avança os 150 mm e cospe dez
  etiquetas em branco, com o desenho ainda deslocado para a esquerda. O TSPL não
  tem esse problema porque manda `SIZE`/`GAP` no pacote. Por isso o modo imagem
  confere o papel do driver **antes** de mandar, e o modo que funcionou fica
  guardado por impressora em `preferencias.json`.
- **"Duas colunas" são dois casos, não um.** Ou cada `^XA` é uma etiqueta e o
  programa monta o par, ou o `^XA` já traz as duas colunas dentro (é o que o
  Mercado Livre entrega, com a segunda em `^FO360`). Tratar o segundo como o
  primeiro desenha numa tela da largura de uma via só e **a via da direita some**.
  Quem decide é `Formato.colunas_no_zpl`, e `render` guarda a extensão que o ZPL
  pediu para que o aviso diga qual formato usar em vez de só "caiu fora".
- **No `^BC` sem modo, a Zebra não troca de subset.** O padrão é subset B do
  começo ao fim; só o modo `A` liga a troca automática para o C. Medido contra o
  Labelary: `12345678` dá 123 módulos no padrão e 79 no modo `A`. Codificar
  "esperto" por conta própria muda a largura do código e estoura layout alheio.
- **No `BITMAP` do TSPL, bit 0 é que imprime.** É o inverso do `^GF` do ZPL.
  Inverter por engano não dá erro nenhum: sai uma etiqueta inteiramente preta e
  gasta o rolo. Há teste fixando as duas convenções (`test_impressao_app.py`).
- **PDF: escala igual nos dois eixos, e linha fina não pode sumir.** Esticar um
  eixo só muda a largura das barras. E a moldura da etiqueta do Mercado Livre é
  um anel *preenchido* de 0,3 pt — menos de um ponto do cabeçote: conforme a
  escala, vira cinza claro e o corte de 128 a apaga (largura mínima de traço não
  resolve, não é traço). `pdf._resgatar_linhas_finas` escurece só o ponto que
  sumiria por inteiro; borda de barra tem vizinho preto e não muda. O PDFium não
  é thread-safe: toda chamada passa por `pdf._TRAVA`.
- **Programa de janela no Windows não tem `stdout`.** Empacotado sem console,
  `sys.stdout` é `None`: o formatador do uvicorn chama `.isatty()` e o servidor
  morre antes de subir (daí `log_config=None`), e `subprocess.Popen` sem
  `stdin/stdout/stderr=DEVNULL` estoura com "identificador inválido". E o erro
  aparece como a caixa cinza "Unhandled exception in script", que não diz nada —
  por isso `desktop.executar()` embrulha tudo e mostra o traceback de verdade.
