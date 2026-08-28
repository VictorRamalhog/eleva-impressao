/* Eleva Impressão — a lógica da tela.

   Regras que guiaram este arquivo:

   1. O preview nunca é desenhado aqui. Ele vem pronto do Python, do mesmo
      código que gera o bitmap da impressão. O que a tela desenha por cima são
      só as *guias* (borda do papel, divisão das colunas) — assim a imagem
      continua sendo exatamente o que a impressora recebe.
   2. Coluna é propriedade do formato. Trocar de 1 para 2 colunas é trocar de
      formato, porque é trocar de papel.
   3. Nada de estado escondido: o que está na tela é o que vai ser impresso. */

const $ = (id) => document.getElementById(id);
const estado = {
  formatos: [],
  impressoras: [],
  visao: 'etiqueta',
  indice: 0,
  total: 1,
  ultimaGeometria: null,
  editandoFormato: null,
  imagemPendente: null,
  zplAntesDoAjuste: null,
  preferencias: { impressoras: {}, formato_id: '', impressora: '' },
};

/* ------------------------------------------------------------------ rede */

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    headers: { 'Content-Type': 'application/json' },
    ...opcoes,
  });
  if (!resposta.ok) {
    let detalhe = `erro ${resposta.status}`;
    try {
      const corpo = await resposta.json();
      detalhe = corpo.detail || detalhe;
    } catch { /* resposta sem JSON — fica o código mesmo */ }
    throw new Error(detalhe);
  }
  return resposta;
}

const apiJson = async (caminho, corpo, metodo = 'POST') =>
  (await api(caminho, { method: metodo, body: corpo ? JSON.stringify(corpo) : undefined })).json();

function recado(texto, tipo = '') {
  const caixa = $('recado');
  caixa.textContent = texto;
  caixa.className = 'recado' + (tipo ? ` ${tipo}-recado` : '');
  caixa.hidden = false;
  clearTimeout(recado._t);
  recado._t = setTimeout(() => { caixa.hidden = true; }, tipo === 'erro' ? 9000 : 4500);
}

/* --------------------------------------------------------------- formato */

const formatoAtual = () =>
  estado.formatos.find((f) => f.id === $('formato').value) || estado.formatos[0];

/** O que a tela manda ao servidor: o formato escolhido, mais os ajustes
 *  soltos (densidade) que valem só para esta visualização. */
function pedidoBase() {
  const formato = formatoAtual();
  return {
    zpl: $('zpl').value,
    formato_id: formato ? formato.id : '',
    indice: estado.indice,
    rotacao: Number($('rotacao').value),
    qualidade: $('qualidade').value,
    motor: $('motor').value,
    visao: estado.visao,
    dpmm: Number($('densidade').value),
  };
}

function mostrarResumoFormato() {
  const f = formatoAtual();
  if (!f) return;
  const colunas = f.colunas === 1 ? '1 coluna' : `${f.colunas} colunas`;
  const origem = f.colunas_no_zpl
    ? ' · <b>as colunas vêm no próprio ZPL</b>'
    : (f.colunas > 1 ? ` · ${f.espaco_colunas_mm} mm entre colunas` : '');
  $('resumoFormato').innerHTML =
    `Etiqueta <b>${f.largura_mm} × ${f.altura_mm} mm</b> · ` +
    `<span class="colunas">${colunas}</span> · ` +
    `papel de <b>${f.largura_total_mm} mm</b> de largura` + origem;
  $('densidade').value = String(f.dpmm);
  $('btApagarFormato').disabled = !!f.embutido;
  $('btApagarFormato').title = f.embutido
    ? 'Formato de fábrica: duplique para poder editar e apagar a cópia'
    : '';
}

function preencherFormatos(lista, selecionar) {
  estado.formatos = lista;
  const select = $('formato');
  const anterior = selecionar || select.value;
  select.innerHTML = '';
  for (const f of lista) {
    const opcao = document.createElement('option');
    opcao.value = f.id;
    // Só o nome: emendar a descrição fazia a parte que distingue um formato do
    // outro cair fora da largura do seletor. O detalhe está no resumo abaixo.
    opcao.textContent = f.nome;
    opcao.title = f.descricao;
    select.appendChild(opcao);
  }
  if (lista.some((f) => f.id === anterior)) select.value = anterior;
  mostrarResumoFormato();
}

/* -------------------------------------------------------------- preview */

let temporizador = null;
function agendarDesenho(atraso = 500) {
  clearTimeout(temporizador);
  temporizador = setTimeout(desenhar, atraso);
}

async function desenhar() {
  const zpl = $('zpl').value;
  if (!zpl.trim()) {
    $('preview').removeAttribute('src');
    $('posicao').textContent = '—';
    $('contagem').textContent = '';
    $('medida').textContent = '';
    return;
  }
  try {
    const dados = await apiJson('/api/preview', pedidoBase());
    estado.total = dados.total;
    estado.indice = dados.indice;
    estado.ultimaGeometria = dados.geometria;
    $('preview').src = dados.imagem;
    $('contagem').textContent =
      `${dados.total} etiqueta${dados.total === 1 ? '' : 's'} no arquivo`;
    $('posicao').textContent = estado.visao === 'folha'
      ? `folha ${dados.folha_atual + 1} de ${dados.folhas}`
      : `etiqueta ${dados.indice + 1} de ${dados.total}`;
    const g = dados.geometria;
    $('medida').textContent =
      `${g.largura_mm} × ${g.altura_mm} mm · ${g.largura_pontos} × ${g.altura_pontos} pontos` +
      ` · ${g.dpmm} dpmm` + (dados.motor === 'labelary' ? ' · desenhado pelo Labelary' : '');
    mostrarAvisos(dados.avisos);
    aplicarZoom();
  } catch (erro) {
    recado(erro.message, 'erro');
  }
}

function mostrarAvisos(lista) {
  const caixa = $('avisos');
  if (!lista || !lista.length) { caixa.hidden = true; caixa.innerHTML = ''; return; }
  caixa.hidden = false;
  caixa.innerHTML = '<b>Avisos</b><ul>' +
    lista.map((a) => `<li>${a.replace(/[<>]/g, '')}</li>`).join('') + '</ul>';
}

/** Ajusta a escala e redesenha as guias do papel por cima da imagem.
 *
 *  Mede na hora e repete uma vez no próximo tique. A repetição existe porque a
 *  barra de rolagem do palco só aparece depois do primeiro ajuste e muda a
 *  largura disponível. É `setTimeout`, e não `requestAnimationFrame`, porque o
 *  quadro não chega quando a janela está atrás de outra — e aí o preview ficava
 *  sem tamanho nenhum. */
function aplicarZoom() {
  medirEDesenhar();
  clearTimeout(aplicarZoom._t);
  aplicarZoom._t = setTimeout(medirEDesenhar, 30);
}

function medirEDesenhar() {
  const imagem = $('preview');
  const g = estado.ultimaGeometria;
  if (!imagem.getAttribute('src') || !g) return;

  const escolha = $('zoom').value;
  let escala;
  if (escolha === 'auto') {
    const palco = $('palco');
    const disponivelX = palco.clientWidth - 40;
    const disponivelY = palco.clientHeight - 44;
    escala = Math.min(disponivelX / g.largura_pontos, disponivelY / g.altura_pontos, 1.6);
  } else {
    escala = Number(escolha);
  }
  escala = Math.max(0.05, escala);
  imagem.style.width = `${Math.round(g.largura_pontos * escala)}px`;
  desenharGuias(escala);
}

function desenharGuias(escala) {
  const g = estado.ultimaGeometria;
  const guias = $('guias');
  guias.innerHTML = '';
  if (!g || g.colunas < 2) return;

  const [largura] = g.etiqueta_pontos;
  for (let coluna = 0; coluna < g.colunas; coluna += 1) {
    const x = g.margem_pontos + coluna * (largura + g.espaco_pontos);
    const marca = document.createElement('div');
    marca.className = 'etiqueta';
    marca.style.left = `${x * escala}px`;
    marca.style.top = `${g.margem_pontos * escala}px`;
    marca.style.width = `${largura * escala}px`;
    marca.style.height = `${g.etiqueta_pontos[1] * escala}px`;
    guias.appendChild(marca);

    if (coluna > 0) {
      const corte = document.createElement('div');
      corte.className = 'corte';
      corte.style.left = `${(x - g.espaco_pontos / 2) * escala}px`;
      guias.appendChild(corte);
    }
  }
}

/** Quantas etiquetas do arquivo cabem numa folha. Quando as colunas já vêm no
 *  ZPL, cada etiqueta do arquivo *é* uma folha — o passo continua sendo 1. */
function passoDaFolha() {
  const f = formatoAtual();
  if (estado.visao !== 'folha' || !f || f.colunas_no_zpl) return 1;
  return Math.max(1, f.colunas);
}

/* --------------------------------------------------------------- ajuste */

/** Encaixa os campos na etiqueta sem mexer no conteúdo.
 *
 *  Guarda o ZPL anterior para o "Desfazer": ajuste automático é sugestão, e
 *  quem decide se ficou bom é quem olha a etiqueta. */
async function ajustarAutomatico() {
  const antes = $('zpl').value;
  if (!antes.trim()) { recado('Não há ZPL para ajustar.', 'erro'); return; }
  try {
    const dados = await apiJson('/api/ajustar', pedidoBase());
    if (!dados.mudou) {
      recado('Já estava encaixado: nada a mudar.', 'ok');
      mostrarAvisos(dados.avisos);
      return;
    }
    estado.zplAntesDoAjuste = antes;
    $('btDesfazer').hidden = false;
    $('zpl').value = dados.zpl;
    localStorage.setItem('eleva.zpl', dados.zpl);
    await desenhar();
    mostrarAvisos([...dados.mudancas, ...dados.avisos]);
    recado(`Ajustado: ${dados.mudancas.length} mudança(s) de posição e tamanho. `
           + 'Nome, SKU e código de barras não foram tocados.', 'ok');
  } catch (erro) {
    recado(erro.message, 'erro');
  }
}

function desfazerAjuste() {
  if (estado.zplAntesDoAjuste === null) return;
  $('zpl').value = estado.zplAntesDoAjuste;
  localStorage.setItem('eleva.zpl', estado.zplAntesDoAjuste);
  estado.zplAntesDoAjuste = null;
  $('btDesfazer').hidden = true;
  desenhar();
  recado('Ajuste desfeito — o ZPL voltou ao que era.', '');
}

/* ------------------------------------------------------------ impressão */

async function imprimir(rota = '/api/imprimir', forcar = false) {
  const botoes = [$('btImprimir'), $('btTeste')];
  botoes.forEach((b) => { b.disabled = true; });
  try {
    const corpo = {
      ...pedidoBase(),
      modo: $('modo').value,
      impressora: $('impressora').value || null,
      copias: Number($('copias').value) || 1,
      gap_mm: Number($('gap').value) || 0,
      apenas_esta: $('apenasEsta').checked,
      forcar,
    };
    const resposta = await apiJson(rota, corpo);

    // O papel do driver não bate com o formato: nada foi impresso ainda. A
    // decisão de gastar etiqueta assim mesmo é de quem está olhando a
    // impressora, então pergunta em vez de decidir sozinho.
    if (resposta.precisa_confirmar) {
      const seguir = confirm(
        `${resposta.detalhe}

Imprimir assim mesmo? A etiqueta pode sair `
        + 'cortada e a impressora pode avançar papel demais.'
      );
      if (seguir) {
        botoes.forEach((b) => { b.disabled = false; });
        return imprimir(rota, true);
      }
      recado('Impressão cancelada — nenhuma etiqueta foi gasta.', '');
      return undefined;
    }

    recado(resposta.ok ? `Enviado: ${resposta.detalhe}` : `Não saiu: ${resposta.detalhe}`,
           resposta.ok ? 'ok' : 'erro');
    atualizarEstadoImpressora();
  } catch (erro) {
    recado(erro.message, 'erro');
  } finally {
    botoes.forEach((b) => { b.disabled = false; });
  }
  return undefined;
}

async function baixar(rota, nome) {
  try {
    const resposta = await api(rota, { method: 'POST', body: JSON.stringify(pedidoBase()) });
    const blob = await resposta.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = nome;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  } catch (erro) {
    recado(erro.message, 'erro');
  }
}

async function atualizarEstadoImpressora() {
  const ponto = $('pontoEstado');
  const texto = $('textoEstado');
  const nome = $('impressora').value;
  try {
    const dados = await apiJson(`/api/impressora${nome ? `?nome=${encodeURIComponent(nome)}` : ''}`,
                                null, 'GET');
    const fila = dados.na_fila ? ` · ${dados.na_fila} na fila` : '';
    ponto.className = `ponto ${dados.pronta ? 'pronta' : 'problema'}`;
    texto.textContent = dados.pronta
      ? `${dados.impressora} · ${dados.porta}${fila}`
      : `${dados.impressora} · ${dados.problemas.join(', ')}`;
  } catch (erro) {
    ponto.className = 'ponto ausente';
    texto.textContent = erro.message;
  }
}

/** Diagnóstico do driver: o papel que o Windows acha que está na impressora.
 *  É a resposta para "por que a etiqueta saiu cortada" sem gastar etiqueta. */
async function conferirDriver() {
  try {
    const d = await apiJson('/api/diagnostico', {
      ...pedidoBase(), modo: $('modo').value, impressora: $('impressora').value || null,
    });
    const papel = `${d.papel_mm[0]} × ${d.papel_mm[1]} mm`;
    const util = `${d.area_util_pontos[0]} × ${d.area_util_pontos[1]} pontos`;
    const esperado = `${d.esperado_pontos[0]} × ${d.esperado_pontos[1]} pontos`;
    const linhas = [
      `${d.impressora} (${d.porta}) · driver ${d.driver}`,
      `Papel no driver: ${papel} a ${d.dpi[0]} dpi · área útil ${util}`,
      `Formato escolhido: ${d.formato} · precisa de ${esperado}`,
    ];
    if (d.avisos.length) linhas.push(...d.avisos);
    else linhas.push('O papel do driver bate com o formato.');
    recado(linhas.join('  |  '), d.avisos.length ? 'erro' : 'ok');
  } catch (erro) {
    recado(erro.message, 'erro');
  }
}

/** Repõe o que funcionou da última vez.
 *
 *  O modo é por impressora: a térmica quer TSPL, uma impressora comum de
 *  escritório na mesma máquina só entende o driver. Descobrir o modo certo
 *  custou etiqueta; esquecer dele custaria de novo. */
function aplicarPreferencias(prefs) {
  if (!prefs) return;
  estado.preferencias = prefs;

  if (prefs.formato_id && estado.formatos.some((f) => f.id === prefs.formato_id)) {
    $('formato').value = prefs.formato_id;
    mostrarResumoFormato();
  }
  const select = $('impressora');
  if (prefs.impressora && [...select.options].some((o) => o.value === prefs.impressora)) {
    select.value = prefs.impressora;
  }
  aplicarPreferenciaDaImpressora();
}

function aplicarPreferenciaDaImpressora() {
  const daImpressora = (estado.preferencias.impressoras || {})[$('impressora').value];
  if (!daImpressora) return;
  if (daImpressora.modo) $('modo').value = daImpressora.modo;
  if (daImpressora.gap_mm !== undefined && daImpressora.gap_mm !== null) {
    $('gap').value = daImpressora.gap_mm;
  }
  explicarModo();
}

/** Avisa o servidor do que foi escolhido, para a próxima abertura já vir assim. */
async function lembrarEscolha() {
  try {
    estado.preferencias = await apiJson('/api/preferencias', {
      impressora: $('impressora').value || null,
      modo: $('modo').value,
      gap_mm: Number($('gap').value) || 0,
      formato_id: formatoAtual() ? formatoAtual().id : null,
    });
  } catch { /* preferência é conveniência: falhar aqui não atrapalha imprimir */ }
}

function explicarModo() {
  const explicacoes = {
    imagem: 'Modo imagem: o desenho vai pelo driver instalado do Windows. ' +
            'É o que costuma funcionar de primeira — comece por aqui.',
    tspl: 'Modo TSPL: o bitmap vai direto para a porta, no dialeto nativo desta ' +
          'família de impressora. Use se o modo imagem sair em branco ou embolado.',
    zpl: 'Modo ZPL cru: os comandos vão sem tradução. Só sai se o firmware da ' +
         'impressora falar ZPL — em muitos clones ele não fala, e nada é impresso.',
  };
  $('avisoModo').textContent = explicacoes[$('modo').value] || '';
}

/* -------------------------------------------------- diálogo de formato */

function abrirDialogoFormato(modo) {
  const dialogo = $('dialogoFormato');
  const base = formatoAtual();
  estado.editandoFormato = modo === 'editar' ? base : null;

  $('tituloDialogo').textContent = modo === 'editar'
    ? `Editar ${base.nome}` : 'Novo formato';
  $('fNome').value = modo === 'editar' ? base.nome : '';
  $('fLargura').value = modo === 'editar' ? base.largura_mm : 100;
  $('fAltura').value = modo === 'editar' ? base.altura_mm : 150;
  $('fColunas').value = String(modo === 'editar' ? base.colunas : 1);
  $('fEspaco').value = modo === 'editar' ? base.espaco_colunas_mm : 2;
  $('fDpmm').value = String(modo === 'editar' ? base.dpmm : 8);
  $('fMargem').value = modo === 'editar' ? base.margem_mm : 0;
  $('fColunasNoZpl').checked = modo === 'editar' ? !!base.colunas_no_zpl : false;
  $('erroFormato').hidden = true;
  atualizarNotaFormato();
  dialogo.showModal();
}

function atualizarNotaFormato() {
  const largura = Number($('fLargura').value) || 0;
  const colunas = Number($('fColunas').value) || 1;
  const espaco = Number($('fEspaco').value) || 0;
  const margem = Number($('fMargem').value) || 0;
  const total = largura * colunas + espaco * (colunas - 1) + margem * 2;
  const cabe = total <= 110.001;
  $('fEspaco').disabled = colunas < 2;
  const duas = colunas >= 2;
  $('fColunasNoZpl').disabled = !duas;
  if (!duas) $('fColunasNoZpl').checked = false;
  $('linhaColunasNoZpl').style.opacity = duas ? '1' : '.45';
  $('notaColunasNoZpl').hidden = !duas;
  $('notaFormato').innerHTML =
    `Papel necessário: <b>${total.toFixed(1)} mm</b> de largura. ` +
    (cabe ? 'Cabe no cabeçote de 110 mm.'
          : '<b style="color:#b91c1c">Passa dos 110 mm do cabeçote.</b>');
}

async function salvarFormato() {
  const corpo = {
    id: estado.editandoFormato ? estado.editandoFormato.id : '',
    nome: $('fNome').value.trim(),
    largura_mm: Number($('fLargura').value),
    altura_mm: Number($('fAltura').value),
    dpmm: Number($('fDpmm').value),
    colunas: Number($('fColunas').value),
    espaco_colunas_mm: Number($('fEspaco').value) || 0,
    margem_mm: Number($('fMargem').value) || 0,
    colunas_no_zpl: $('fColunasNoZpl').checked,
  };
  try {
    const dados = await apiJson('/api/formatos', corpo);
    preencherFormatos(dados.formatos, dados.formato.id);
    recado(`Formato "${dados.formato.nome}" salvo.`, 'ok');
    desenhar();
    return true;
  } catch (erro) {
    const caixa = $('erroFormato');
    caixa.textContent = erro.message;
    caixa.hidden = false;
    return false;
  }
}

/* --------------------------------------------------- diálogo de imagem */

function abrirDialogoImagem(arquivo) {
  const leitor = new FileReader();
  leitor.onload = () => {
    estado.imagemPendente = leitor.result;
    $('previaImagem').src = leitor.result;
    $('dialogoImagem').showModal();
  };
  leitor.readAsDataURL(arquivo);
}

async function inserirImagem() {
  try {
    const dados = await apiJson('/api/imagem', {
      conteudo_base64: estado.imagemPendente,
      x: Number($('iX').value) || 0,
      y: Number($('iY').value) || 0,
      largura_pontos: Number($('iLargura').value) || null,
      limiar: Number($('iLimiar').value),
      inverter: $('iInverter').checked,
    });
    const area = $('zpl');
    const texto = area.value;
    const fim = texto.lastIndexOf('^XZ');
    area.value = fim >= 0
      ? `${texto.slice(0, fim)}${dados.zpl}\n${texto.slice(fim)}`
      : `${texto}\n${dados.zpl}`;
    recado(`Imagem inserida (${dados.bytes} caracteres de ZPL).`, 'ok');
    desenhar();
  } catch (erro) {
    recado(erro.message, 'erro');
  }
}

/* ------------------------------------------------------------- arranque */

async function iniciar() {
  let dados;
  try {
    dados = await apiJson('/api/estado', null, 'GET');
  } catch (erro) {
    recado(`Não consegui falar com o serviço: ${erro.message}`, 'erro');
    return;
  }

  preencherFormatos(dados.formatos, dados.formato_padrao);

  const select = $('impressora');
  select.innerHTML = '';
  for (const impressora of dados.impressoras) {
    const opcao = document.createElement('option');
    opcao.value = impressora.nome;
    opcao.textContent = `${impressora.nome} (${impressora.porta})`;
    if (impressora.padrao) opcao.selected = true;
    select.appendChild(opcao);
  }
  if (!dados.impressoras.length) {
    const opcao = document.createElement('option');
    opcao.textContent = dados.erro_impressora || 'nenhuma impressora encontrada';
    select.appendChild(opcao);
    select.disabled = true;
  }

  aplicarPreferencias(dados.preferencias);

  const guardado = localStorage.getItem('eleva.zpl');
  $('zpl').value = guardado && guardado.trim() ? guardado : dados.zpl_exemplo;
  $('zpl').dataset.exemplo = dados.zpl_exemplo;

  explicarModo();
  atualizarEstadoImpressora();
  desenhar();
}

function ligarEventos() {
  $('zpl').addEventListener('input', () => {
    localStorage.setItem('eleva.zpl', $('zpl').value);
    // Editado à mão, o "antes" do ajuste deixa de fazer sentido.
    estado.zplAntesDoAjuste = null;
    $('btDesfazer').hidden = true;
    agendarDesenho();
  });
  $('zpl').addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter' && (evento.ctrlKey || evento.metaKey)) {
      evento.preventDefault();
      desenhar();
    }
  });

  $('btRedesenhar').addEventListener('click', () => desenhar());
  $('btAjustar').addEventListener('click', ajustarAutomatico);
  $('btDesfazer').addEventListener('click', desfazerAjuste);
  $('btExemplo').addEventListener('click', () => {
    $('zpl').value = $('zpl').dataset.exemplo || '';
    localStorage.setItem('eleva.zpl', $('zpl').value);
    estado.indice = 0;
    desenhar();
  });
  $('btLimpar').addEventListener('click', () => {
    $('zpl').value = '';
    localStorage.removeItem('eleva.zpl');
    desenhar();
  });

  $('btAbrir').addEventListener('click', () => $('arquivoZpl').click());
  $('arquivoZpl').addEventListener('change', (evento) => {
    const arquivo = evento.target.files[0];
    if (!arquivo) return;
    const leitor = new FileReader();
    leitor.onload = () => {
      $('zpl').value = String(leitor.result);
      localStorage.setItem('eleva.zpl', $('zpl').value);
      estado.indice = 0;
      desenhar();
      recado(`${arquivo.name} carregado.`, 'ok');
    };
    leitor.readAsText(arquivo, 'utf-8');
    evento.target.value = '';
  });

  $('btImagem').addEventListener('click', () => $('arquivoImagem').click());
  $('arquivoImagem').addEventListener('change', (evento) => {
    const arquivo = evento.target.files[0];
    if (arquivo) abrirDialogoImagem(arquivo);
    evento.target.value = '';
  });

  for (const id of ['formato', 'densidade', 'qualidade', 'rotacao', 'motor']) {
    $(id).addEventListener('change', () => {
      if (id === 'formato') { estado.indice = 0; mostrarResumoFormato(); lembrarEscolha(); }
      desenhar();
    });
  }

  document.querySelectorAll('.aba').forEach((aba) => {
    aba.addEventListener('click', () => {
      document.querySelectorAll('.aba').forEach((o) => o.classList.remove('ativa'));
      aba.classList.add('ativa');
      estado.visao = aba.dataset.visao;
      desenhar();
    });
  });

  $('btAnterior').addEventListener('click', () => {
    estado.indice = Math.max(0, estado.indice - passoDaFolha());
    desenhar();
  });
  $('btProxima').addEventListener('click', () => {
    estado.indice = Math.min(estado.total - 1, estado.indice + passoDaFolha());
    desenhar();
  });
  $('preview').addEventListener('load', aplicarZoom);
  $('zoom').addEventListener('change', aplicarZoom);
  window.addEventListener('resize', () => { if ($('zoom').value === 'auto') aplicarZoom(); });

  $('btNovoFormato').addEventListener('click', () => abrirDialogoFormato('novo'));
  $('btEditarFormato').addEventListener('click', () => abrirDialogoFormato('editar'));
  $('btDuplicarFormato').addEventListener('click', async () => {
    try {
      const dados = await apiJson(`/api/formatos/${formatoAtual().id}/duplicar`);
      preencherFormatos(dados.formatos, dados.formato.id);
      recado(`Criado "${dados.formato.nome}".`, 'ok');
    } catch (erro) { recado(erro.message, 'erro'); }
  });
  $('btApagarFormato').addEventListener('click', async () => {
    const alvo = formatoAtual();
    if (!confirm(`Apagar o formato "${alvo.nome}"?`)) return;
    try {
      const dados = await apiJson(`/api/formatos/${alvo.id}`, null, 'DELETE');
      preencherFormatos(dados.formatos);
      recado('Formato apagado.', 'ok');
      desenhar();
    } catch (erro) { recado(erro.message, 'erro'); }
  });

  for (const id of ['fLargura', 'fColunas', 'fEspaco', 'fMargem', 'fColunasNoZpl']) {
    $(id).addEventListener('input', atualizarNotaFormato);
    $(id).addEventListener('change', atualizarNotaFormato);
  }
  $('formularioFormato').addEventListener('submit', async (evento) => {
    if (evento.submitter && evento.submitter.value === 'cancelar') return;
    evento.preventDefault();
    if (await salvarFormato()) $('dialogoFormato').close();
  });
  $('formularioImagem').addEventListener('submit', (evento) => {
    if (evento.submitter && evento.submitter.value === 'inserir') inserirImagem();
  });

  $('modo').addEventListener('change', () => { explicarModo(); lembrarEscolha(); });
  $('gap').addEventListener('change', lembrarEscolha);
  $('impressora').addEventListener('change', () => {
    aplicarPreferenciaDaImpressora();
    atualizarEstadoImpressora();
    lembrarEscolha();
  });
  $('btImprimir').addEventListener('click', () => imprimir('/api/imprimir'));
  $('btTeste').addEventListener('click', () => imprimir('/api/teste'));
  $('btConferir').addEventListener('click', conferirDriver);
  $('btPng').addEventListener('click', () => baixar('/api/png', 'etiqueta.png'));
  $('btPdf').addEventListener('click', () => baixar('/api/pdf', 'etiquetas.pdf'));

  setInterval(atualizarEstadoImpressora, 20000);
}

ligarEventos();
iniciar();
