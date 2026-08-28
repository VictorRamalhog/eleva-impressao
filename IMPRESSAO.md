# Eleva Impressão

Aplicativo **local** para ver e imprimir etiquetas ZPL. Faz o que o
[visualizador do Labelary](https://labelary.com/viewer.html) faz — editor de ZPL
à esquerda, prévia à direita, tamanho de etiqueta, densidade, rotação,
navegação entre etiquetas, download em PNG e PDF — e mais três coisas que o
Labelary não faz: funciona **sem internet**, entende de **formatos com duas
colunas** e **imprime** na térmica ligada à USB.

```powershell
uv run eleva impressao          # abre a janela
```

Empacotado, é `dist/Eleva Impressao/Eleva Impressao.exe` — clique duplo, sem
Python instalado.

---

## A decisão que sustenta o resto

**O preview e o papel saem da mesma função.**

`impressao/render.py` transforma ZPL em bitmap. Esse bitmap é o que aparece na
tela *e* é o que vai para a impressora. Não existe um caminho para visualizar e
outro para imprimir, então não existe o dia em que os dois discordam.

Foi por isso que o motor de renderização foi escrito em casa em vez de só
chamar a API do Labelary. O efeito colateral bom: a tela responde na hora, sem
rede e sem limite de requisições. O Labelary continua disponível na tela, como
**segundo motor**, para conferir fidelidade quando bater dúvida.

### Fidelidade conferida, não presumida

A calibração foi feita medindo, uma a uma, as saídas do Labelary (que é a
referência de como uma Zebra imprime) e ajustando o motor até bater:

| O que | Resultado |
|---|---|
| Altura da maiúscula no `^A0N,h` | 0,775 × h — dentro de 1 ponto |
| Largura do texto | dentro de 0,5% |
| `^GB`, `^GD`, `^GC`, `^GE` | geometria idêntica |
| `^GF` (gráfico embutido) | **zero pixels de diferença** |
| Code 128, Code 39, 2 de 5, EAN-8, QR | caixa idêntica ao ponto |
| EAN-13 / UPC-A | dentro de 2 pontos (0,25 mm) |
| Quebra de linha do `^FB` | quebra nas mesmas palavras |

Os números viraram teste em `tests/test_impressao_render.py`, que roda
**offline**: o Labelary já falou, os testes só guardam o que ele disse. Se
alguém mexer no motor e o preview se afastar do que a impressora faria, o teste
cai.

O que **ainda não é desenhado**: `^BX` (DataMatrix), `^B7` (PDF417) e `^B0`
(Aztec). Em vez de sumir em silêncio, o motor marca o lugar com um X e põe um
aviso na tela — e o motor Labelary desenha, se for preciso conferir.

---

## Formatos de etiqueta

Formato é **dado**, não código: fica num JSON que a própria tela edita
(`%LOCALAPPDATA%\Eleva Impressao\formatos-etiqueta.json` no `.exe`, ou
`data/formatos-etiqueta.json` rodando do repositório).

Vêm prontos:

| Formato | Etiqueta | Colunas | Papel |
|---|---|---|---|
| **Mercado Livre 100 × 150 mm** *(padrão)* | 100 × 150 mm | 1 | 100 mm |
| Mercado Livre 2 colunas | 41,9 × 25,4 mm | 2, **já no ZPL** | 83,8 mm |
| Duas colunas 40 × 25 mm | 40 × 25 mm | 2, montadas aqui | 82 mm |
| 100 × 50 mm | 100 × 50 mm | 1 | 100 mm |
| 60 × 40 mm | 60 × 40 mm | 1 | 60 mm |
| 40 × 25 mm (uma coluna) | 40 × 25 mm | 1 | 40 mm |

Os de fábrica não se apagam — **duplica-se** e edita-se a cópia, para o original
continuar lá quando a cópia der errado. Um de fábrica que for editado continua
marcado como de fábrica e a edição persiste.

**Coluna é propriedade do formato, não da hora de imprimir.** Uma coluna e duas
colunas não são dois botões: são papéis diferentes, com larguras diferentes.
Amarrar a coluna ao formato é o que impede mandar um 100 × 150 de uma coluna
para um rolo de duas.

### Duas colunas: há dois casos, e são diferentes

Confundi-los foi o primeiro defeito achado no papel, e a distinção agora é um
campo do formato — **"o próprio ZPL já traz as colunas"**:

| | Quem monta o par | Tela de desenho |
|---|---|---|
| **Desmarcado** (o normal) | o programa: cada `^XA` é uma etiqueta e duas viram uma folha | uma etiqueta |
| **Marcado** | o arquivo: um `^XA` já tem as duas colunas, a segunda em `^FO` distante | a folha inteira |

O segundo é o caso do **Mercado Livre**: a etiqueta de duas vias vem num `^XA`
só, de 3,3 × 1 polegada, com a segunda coluna desenhada a partir de `^FO360`.
Desenhado com a régua do primeiro caso, tudo depois de 40 mm cai fora e a via da
direita simplesmente some.

Por isso o motor guarda **até onde o ZPL quis desenhar**, mesmo fora da tela: se
o arquivo pede mais largura do que o formato oferece e essa largura bate com a
folha, o aviso deixa de ser "um campo caiu fora" e passa a ser *"este ZPL já traz
as colunas dentro de uma etiqueta só; escolha um formato marcado como tal"*.
Classificar sem agir sobre a classificação seria decoração.

O cabeçote da KP-IM604 alcança **110 mm**. Qualquer formato cuja soma
(colunas × largura + espaços + margens) passe disso é recusado na hora de
salvar, com a conta explicada.

Na aba **"Como vai imprimir"** a tela mostra a folha montada — as duas colunas
lado a lado, a linha de corte entre elas e a última folha com o lugar vazio
preservado (sem isso o rolo avança errado e a próxima etiqueta nasce torta).

---

## Ajustar automático

O botão que resolve a dor de todo dia: nome de produto muda de tamanho a cada
SKU, e com ele o layout inteiro sai do lugar — o nome passa por cima do SKU, o
código de barras estoura a largura, e alguém acaba mexendo em `^FO` na mão,
etiqueta por etiqueta.

**A regra é que ele nunca toca no conteúdo.** Nome, SKU e código de barras são
dados do produto; o ajustador só reescreve parâmetros numéricos — `^FO`/`^FT`
(posição), `^A` (corpo da fonte), `^FB` (caixa do texto), `^BY` (largura do
módulo) e a altura das barras. Um teste compara os `^FD` antes e depois, um a
um, e a própria função se recusa a devolver o resultado se algum tiver mudado.

O que ele faz, por coluna:

1. separa os campos em faixas, **pela geometria original** — quem estava lado a
   lado continua lado a lado;
2. quebra o texto na largura útil e descobre de quantas linhas ele precisa;
3. encolhe fonte e altura de barras, juntas, até o conjunto caber na altura;
4. estreita o módulo do código de barras se ele passar da largura;
5. distribui a sobra em espaços iguais e centraliza tudo.

Ele também recupera informação perdida: no molde do Mercado Livre o `^FB`
declara **2 linhas**, então um nome que precise de 3 tem o fim cortado — e a
etiqueta sai bonita, sem indício de que faltou algo. O ajuste dá ao bloco as
linhas necessárias e o texto volta inteiro.

O que ele **não** faz, de propósito: se o código de barras for mais largo que a
etiqueta mesmo no módulo 1, ele avisa em vez de espremer. Barra espremida além
disso não é lida pelo coletor, e encurtar o dado é decisão de quem opera.

O resultado é sugestão, não sentença: **Desfazer ajuste** devolve o ZPL anterior.

### Duas armadilhas que este código já pagou

- **Medir texto ignorando o `w` do `^A0N,h,w`.** Um `^A0N,20,25` estica o glifo
  em 25%; medindo proporcional, a conta dizia que cabia e o papel dizia que não.
- **Agrupar as faixas pela altura recalculada.** Quando o nome passava de 2 para
  3 linhas, ele encostava no SKU, os dois viravam uma faixa só e o ajuste os
  espalhava lado a lado — jogando o SKU para fora da etiqueta. O tamanho novo é
  **consequência** do ajuste; não pode ser entrada dele.

---

## Impressão: três caminhos, porque a impressora é um clone

A KP-IM604 vem com um driver chamado `LABEL`, que é **GDI** — espera receber uma
página desenhada, não comando de impressora. Por baixo, o firmware fala TSPL, e
alguns lotes falam ZPL. Qual funciona depende do modelo e da versão, e não dá
para descobrir lendo a caixa. Então o programa oferece os três e deixa você ver
qual sai no papel:

| Modo | O que envia | Quando usar |
|---|---|---|
| **Imagem pelo driver** *(padrão)* | o bitmap, pela API gráfica do Windows | comece por aqui: funciona sempre que a impressora imprime a página de teste do Windows |
| **TSPL — bitmap direto** | `SIZE`/`GAP`/`BITMAP`/`PRINT` crus na porta | se o modo imagem sair em branco ou embolado |
| **ZPL cru** | o ZPL sem tradução | só se o firmware falar ZPL |

Os dois últimos vão pelo spooler com tipo de dados `RAW`, que entrega os bytes à
`USB001` sem o driver reinterpretar.

Na KP-IM604 desta operação, **o que funciona é o TSPL** — conferido no papel, no
formato de uma coluna e no de duas. O modo imagem só acerta quando o papel
configurado no Windows é exatamente o da etiqueta, e foi o que fez a primeira
tira de 40 × 25 sair com dez etiquetas em branco.

**O modo escolhido fica guardado, por impressora** (`preferencias.json`, ao lado
do `formatos-etiqueta.json`). Não é conveniência: descobrir o modo certo custou
duas tiras de etiqueta, e uma escolha que se perde ao fechar o programa cobraria
esse preço de novo, sempre no dia corrido. Toda impressão bem-sucedida confirma
o modo; as que falham não guardam nada.

### A trava que existe por causa de uma tira perdida

No modo imagem, quem manda no tamanho da página é o Windows. Com o driver em
100 × 150 mm e um formato de 82 × 25 mm, a impressora avança os 150 mm da página
inteira: **uma etiqueta impressa e dez em branco**, e o desenho ainda sai
deslocado para a esquerda, porque é posicionado a partir de uma página mais
larga que o papel.

O botão "Conferir driver" já diagnosticava isso — mas depender de alguém lembrar
de clicar num botão para não desperdiçar etiqueta é o mesmo erro de classificar
sem agir. Agora o modo imagem **confere sozinho antes de mandar** e para,
explicando as duas saídas (mudar o papel no Windows ou usar TSPL). Ainda dá para
imprimir assim mesmo — mas por decisão, não por descuido. O TSPL não passa por
essa trava porque manda `SIZE` e `GAP` no próprio pacote.

### Antes de gastar etiqueta

**Conferir driver** abre o contexto gráfico, lê as medidas e fecha — nenhuma
folha anda. Responde a pergunta que sempre aparece quando a etiqueta sai
cortada: *o papel configurado no Windows é o mesmo tamanho do formato?*

Nesta máquina, hoje:

```
LABEL (USB001) · driver LABEL
papel do driver: 100,1 × 150,1 mm a 203 dpi · área útil 800 × 1200 pontos
formato 100 × 150 mm · 1 coluna · precisa de 800 × 1200 pontos
```

Bate ponto a ponto — o bitmap vai 1:1 para o cabeçote, sem reamostragem.

**Etiqueta de teste** imprime uma etiqueta de conferência no formato escolhido,
com o nome do formato, o modo usado, uma moldura (se um lado sumir, a etiqueta
está deslocada no papel) e uma régua de 10 em 10 mm (se os traços não caírem
certo, a densidade configurada não é a da impressora).

---

## Mapa dos arquivos

| Preciso de… | Vá para |
|---|---|
| ZPL → bitmap, comandos, fontes | `impressao/render.py` |
| Code 128 / 39 / 2 de 5 / EAN / QR | `impressao/barras.py` |
| imagem → `^GF` e de volta | `impressao/graficos.py` |
| tamanhos de etiqueta e colunas | `impressao/formatos.py` |
| ajuste automático de posição | `impressao/ajuste.py` |
| o que o programa lembra | `impressao/preferencias.py` |
| falar com a impressora | `impressao/windows.py` |
| a API local | `impressao/app.py` |
| a tela | `impressao/web/` |
| a janela e o `.exe` | `impressao/desktop.py`, `eleva-impressao.spec` |

## Gerar o executável

```powershell
uv run pyinstaller eleva-impressao.spec --noconfirm
```

Sai em `dist/Eleva Impressao/` (~57 MB, pasta inteira — o `.exe` sozinho não
roda). A pasta `web` entra como dado com o nome `eleva_impressao_web`, que é o
que `app.recursos()` procura quando o programa está congelado.
