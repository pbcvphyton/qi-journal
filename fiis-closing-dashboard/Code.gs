/**
 * Dashboard de Controle de Fechamento de Exercício de FIIs
 * Comunicação entre Administração Fiduciária e Precificação
 *
 * Abas:
 *   1. "🖥️ Dashboard"       -> KPIs, gráficos, botão de auditoria
 *   2. "📊 Operacional"      -> linha-a-linha por imóvel (onde os times atuam)
 *   3. "⚙️ Config & Logs"   -> listas de validação, e-mails, trilha de auditoria
 *
 * Colunas da aba Operacional:
 *   A ID | B Fundo (FII) | C Nome do Imóvel | D Data Base Fechamento
 *   E Responsável (Admin) | F Link Drive | G Status do Laudo | H Data Recebimento Laudo
 *   I Responsável (Precificação) | J Valor Contábil Anterior | K Novo Valor (Laudo)
 *   L Variação % | M Comentários/Justificativas
 *   N Última Atualização | O Status Final
 */

// ============================================================================
// CONSTANTES DE CONFIGURAÇÃO
// ============================================================================

const SHEET_DASH = '🖥️ Dashboard';
const SHEET_OP = '📊 Operacional';
const SHEET_CFG = '⚙️ Config & Logs';

const COL = {
  ID: 1,
  FUNDO: 2,
  IMOVEL: 3,
  DATA_BASE: 4,
  RESP_ADMIN: 5,
  LINK_DRIVE: 6,
  STATUS: 7,
  DATA_LAUDO: 8,
  RESP_PREC: 9,
  VALOR_ANTERIOR: 10,
  VALOR_NOVO: 11,
  VARIACAO: 12,
  COMENTARIOS: 13,
  ULT_UPDATE: 14,
  STATUS_FINAL: 15
};

const STATUS = {
  AGUARDANDO: '[ADMIN] Aguardando Laudo',
  EM_CONFERENCIA: '[ADMIN] Laudo em Conferência',
  DISPONIVEL: '[PRECIFICAÇÃO] Disponível para Precificar',
  EM_ANALISE: '[PRECIFICAÇÃO] Em Análise de Valor',
  VALOR_DEFINIDO: '[PRECIFICAÇÃO] Valor Final Definido',
  PRONTO: '[AUDITORIA] Pronto para Fechamento'
};

const STATUS_LIST = [
  STATUS.AGUARDANDO,
  STATUS.EM_CONFERENCIA,
  STATUS.DISPONIVEL,
  STATUS.EM_ANALISE,
  STATUS.VALOR_DEFINIDO,
  STATUS.PRONTO
];

// Colunas que disparam entrada no log de auditoria
const AUDIT_COLUMNS = [COL.STATUS, COL.VALOR_NOVO, COL.COMENTARIOS];

// Dias de antecedência para o alerta de pendências
const ALERT_WINDOW_DAYS = 5;


// ============================================================================
// MENU E GATILHO DE ABERTURA
// ============================================================================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('🏢 Fechamento FIIs')
    .addItem('📋 Gerar Relatório de Auditoria', 'verificarPendencias')
    .addItem('📧 Testar envio de e-mail (linha selecionada)', 'testarNotificacao')
    .addSeparator()
    .addItem('🛠️ Setup inicial (criar abas e validações)', 'setupPlanilha')
    .addToUi();
}


// ============================================================================
// onEdit - ROTEADOR PRINCIPAL
// ============================================================================

/**
 * Gatilho simples executado em toda edição.
 * Roteia para: log de auditoria, carimbo de tempo, notificações.
 */
function onEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (sheet.getName() !== SHEET_OP) return;
  if (e.range.getRow() === 1) return; // ignora cabeçalho

  try {
    logAudit_(e);
    updateTimestamp_(e);
    handleStatusChange_(e);
  } catch (err) {
    console.error('onEdit falhou:', err);
  }
}


// ============================================================================
// LOG DE AUDITORIA
// ============================================================================

/**
 * Registra alterações em Status, Novo Valor e Comentários na aba Config & Logs.
 * Captura e-mail do usuário, timestamp, valor anterior e valor novo.
 */
function logAudit_(e) {
  const col = e.range.getColumn();
  if (AUDIT_COLUMNS.indexOf(col) === -1) return;

  const sheet = e.range.getSheet();
  const row = e.range.getRow();
  const id = sheet.getRange(row, COL.ID).getValue();
  const imovel = sheet.getRange(row, COL.IMOVEL).getValue();
  const fundo = sheet.getRange(row, COL.FUNDO).getValue();

  const logSheet = SpreadsheetApp.getActive().getSheetByName(SHEET_CFG);
  if (!logSheet) return;

  const logStartRow = getLogStartRow_(logSheet);
  const nextRow = Math.max(logStartRow, logSheet.getLastRow() + 1);

  const campo = nomeColuna_(col);
  const valorAnterior = (e.oldValue === undefined) ? '' : e.oldValue;
  const valorNovo = (e.value === undefined) ? e.range.getValue() : e.value;
  const user = Session.getActiveUser().getEmail() || 'desconhecido';

  logSheet.getRange(nextRow, 1, 1, 9).setValues([[
    new Date(),
    user,
    SHEET_OP,
    row,
    id,
    fundo,
    imovel,
    campo,
    `"${valorAnterior}" → "${valorNovo}"`
  ]]);
}

function nomeColuna_(col) {
  switch (col) {
    case COL.STATUS: return 'Status do Laudo';
    case COL.VALOR_NOVO: return 'Novo Valor (Laudo)';
    case COL.COMENTARIOS: return 'Comentários/Justificativas';
    default: return 'Coluna ' + col;
  }
}

/**
 * Descobre em qual linha começa a tabela de log na aba Config & Logs,
 * marcada pelo cabeçalho "=== LOG DE AUDITORIA ===".
 */
function getLogStartRow_(logSheet) {
  const marker = '=== LOG DE AUDITORIA ===';
  const values = logSheet.getRange('A1:A').getValues();
  for (let i = 0; i < values.length; i++) {
    if (values[i][0] === marker) return i + 3; // marcador + linha de cabeçalho + 1
  }
  return logSheet.getLastRow() + 1;
}


// ============================================================================
// CARIMBO DE TEMPO
// ============================================================================

/**
 * Sempre que o Status do Laudo mudar, carimba a coluna "Última Atualização".
 */
function updateTimestamp_(e) {
  if (e.range.getColumn() !== COL.STATUS) return;
  const sheet = e.range.getSheet();
  sheet.getRange(e.range.getRow(), COL.ULT_UPDATE).setValue(new Date());
}


// ============================================================================
// NOTIFICAÇÃO AUTOMÁTICA À PRECIFICAÇÃO
// ============================================================================

function handleStatusChange_(e) {
  if (e.range.getColumn() !== COL.STATUS) return;
  const novoStatus = e.range.getValue();
  if (novoStatus !== STATUS.DISPONIVEL) return;

  const sheet = e.range.getSheet();
  const row = e.range.getRow();
  const payload = {
    id: sheet.getRange(row, COL.ID).getValue(),
    fundo: sheet.getRange(row, COL.FUNDO).getValue(),
    imovel: sheet.getRange(row, COL.IMOVEL).getValue(),
    dataBase: sheet.getRange(row, COL.DATA_BASE).getValue(),
    respPrec: sheet.getRange(row, COL.RESP_PREC).getValue(),
    linkDrive: sheet.getRange(row, COL.LINK_DRIVE).getValue()
  };

  enviarNotificacaoPrecificacao(payload);
}

/**
 * Envia e-mail para o responsável da Precificação.
 * Busca o e-mail na tabela "Responsáveis" da aba Config & Logs.
 */
function enviarNotificacaoPrecificacao(payload) {
  if (!payload.respPrec) {
    console.warn('Sem responsável de Precificação para', payload.imovel);
    return;
  }
  const email = buscarEmail_(payload.respPrec);
  if (!email) {
    console.warn('E-mail não encontrado para', payload.respPrec);
    return;
  }

  const dataBaseStr = payload.dataBase instanceof Date
    ? Utilities.formatDate(payload.dataBase, Session.getScriptTimeZone(), 'dd/MM/yyyy')
    : payload.dataBase;

  const assunto = `[Fechamento FIIs] Laudo disponível: ${payload.imovel} (${payload.fundo})`;
  const corpoHtml = `
    <div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
      <p>Olá <b>${payload.respPrec}</b>,</p>
      <p>O laudo do imóvel abaixo acaba de ser liberado pela Administração Fiduciária
         e está pronto para sua análise:</p>
      <table style="border-collapse:collapse;margin:12px 0">
        <tr><td style="padding:4px 10px;background:#f3f3f3"><b>Fundo</b></td>
            <td style="padding:4px 10px">${payload.fundo}</td></tr>
        <tr><td style="padding:4px 10px;background:#f3f3f3"><b>Imóvel</b></td>
            <td style="padding:4px 10px">${payload.imovel}</td></tr>
        <tr><td style="padding:4px 10px;background:#f3f3f3"><b>ID</b></td>
            <td style="padding:4px 10px">${payload.id}</td></tr>
        <tr><td style="padding:4px 10px;background:#f3f3f3"><b>Data Base</b></td>
            <td style="padding:4px 10px">${dataBaseStr}</td></tr>
      </table>
      <p><a href="${payload.linkDrive}" style="background:#1a73e8;color:#fff;
         padding:8px 14px;border-radius:4px;text-decoration:none">
         📁 Abrir pasta no Drive</a></p>
      <p style="color:#666;font-size:12px">
        Esta mensagem foi gerada automaticamente pelo Dashboard de Fechamento de FIIs.
      </p>
    </div>`;

  MailApp.sendEmail({
    to: email,
    subject: assunto,
    htmlBody: corpoHtml
  });
}

/**
 * Busca o e-mail de um responsável na tabela de Responsáveis (Config & Logs).
 * Formato esperado: coluna C = Nome, coluna D = E-mail.
 */
function buscarEmail_(nome) {
  const cfg = SpreadsheetApp.getActive().getSheetByName(SHEET_CFG);
  if (!cfg) return null;
  const data = cfg.getRange('C2:D').getValues();
  for (let i = 0; i < data.length; i++) {
    if (data[i][0] && data[i][0].toString().trim() === nome.toString().trim()) {
      return data[i][1];
    }
  }
  return null;
}

/**
 * Permite testar a notificação manualmente usando a linha selecionada.
 */
function testarNotificacao() {
  const sheet = SpreadsheetApp.getActive().getActiveSheet();
  if (sheet.getName() !== SHEET_OP) {
    SpreadsheetApp.getUi().alert('Selecione uma linha na aba "📊 Operacional" primeiro.');
    return;
  }
  const row = sheet.getActiveRange().getRow();
  if (row < 2) return;
  enviarNotificacaoPrecificacao({
    id: sheet.getRange(row, COL.ID).getValue(),
    fundo: sheet.getRange(row, COL.FUNDO).getValue(),
    imovel: sheet.getRange(row, COL.IMOVEL).getValue(),
    dataBase: sheet.getRange(row, COL.DATA_BASE).getValue(),
    respPrec: sheet.getRange(row, COL.RESP_PREC).getValue(),
    linkDrive: sheet.getRange(row, COL.LINK_DRIVE).getValue()
  });
  SpreadsheetApp.getUi().alert('E-mail de teste enviado (se o responsável estiver cadastrado).');
}


// ============================================================================
// RELATÓRIO DE AUDITORIA
// ============================================================================

/**
 * Varre a aba Operacional e lista imóveis pendentes cuja Data Base está
 * a no máximo ALERT_WINDOW_DAYS de distância. Mostra um ui.alert.
 */
function verificarPendencias() {
  const ui = SpreadsheetApp.getUi();
  const op = SpreadsheetApp.getActive().getSheetByName(SHEET_OP);
  if (!op) { ui.alert('Aba Operacional não encontrada.'); return; }

  const lastRow = op.getLastRow();
  if (lastRow < 2) { ui.alert('Nenhum imóvel cadastrado.'); return; }

  const range = op.getRange(2, 1, lastRow - 1, COL.STATUS_FINAL).getValues();
  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);

  const pendentes = [];
  range.forEach(linha => {
    const statusFinal = linha[COL.STATUS_FINAL - 1];
    const dataBase = linha[COL.DATA_BASE - 1];
    if (!dataBase || !(dataBase instanceof Date)) return;
    if (statusFinal === 'Pronto para Fechamento') return;
    const diffDias = Math.floor((dataBase.getTime() - hoje.getTime()) / 86400000);
    if (diffDias <= ALERT_WINDOW_DAYS) {
      pendentes.push({
        fundo: linha[COL.FUNDO - 1],
        imovel: linha[COL.IMOVEL - 1],
        status: linha[COL.STATUS - 1],
        diffDias: diffDias
      });
    }
  });

  if (pendentes.length === 0) {
    ui.alert('✅ Tudo em ordem', 'Nenhum imóvel pendente dentro da janela de '
      + ALERT_WINDOW_DAYS + ' dias.', ui.ButtonSet.OK);
    return;
  }

  pendentes.sort((a, b) => a.diffDias - b.diffDias);
  const linhas = pendentes.map(p => {
    const prazo = p.diffDias < 0
      ? `ATRASADO ${Math.abs(p.diffDias)}d`
      : `em ${p.diffDias}d`;
    return `• [${prazo}] ${p.fundo} — ${p.imovel}\n    status atual: ${p.status}`;
  });

  ui.alert(
    `⚠️ ${pendentes.length} imóvel(is) pendente(s)`,
    linhas.join('\n\n'),
    ui.ButtonSet.OK
  );
}


// ============================================================================
// SETUP AUTOMATIZADO (opcional - acelera a configuração inicial)
// ============================================================================

/**
 * Cria as 3 abas com cabeçalhos, validações, formatação condicional e
 * fórmulas do Dashboard. Executar uma única vez em planilha nova.
 */
function setupPlanilha() {
  const ss = SpreadsheetApp.getActive();
  criarAbaOperacional_(ss);
  criarAbaConfig_(ss);
  criarAbaDashboard_(ss);
  SpreadsheetApp.getUi().alert('Setup concluído. Agora configure as permissões '
    + 'executando manualmente as funções enviarNotificacaoPrecificacao e '
    + 'verificarPendencias uma vez cada (para autorizar MailApp e UI).');
}

function criarAbaOperacional_(ss) {
  let sh = ss.getSheetByName(SHEET_OP);
  if (!sh) sh = ss.insertSheet(SHEET_OP);
  sh.clear();

  const headersBlocos = [[
    'ID', 'Fundo (FII)', 'Nome do Imóvel', 'Data Base Fechamento',
    'Responsável (Admin)', 'Link Drive (Pasta/Laudo)', 'Status do Laudo',
    'Data Recebimento Laudo',
    'Responsável (Precificação)', 'Valor Contábil Anterior', 'Novo Valor (Laudo)',
    'Variação %', 'Comentários/Justificativas',
    'Última Atualização', 'Status Final'
  ]];
  sh.getRange(1, 1, 1, headersBlocos[0].length).setValues(headersBlocos);

  // Cores por bloco
  const fg = '#ffffff';
  pintarBloco_(sh, 1, 4, '#424242', fg);   // Identificação - Cinza Escuro
  pintarBloco_(sh, 5, 4, '#1a3a6c', fg);   // Admin Fiduciária - Azul Escuro
  pintarBloco_(sh, 9, 5, '#1e5631', fg);   // Precificação - Verde Escuro
  pintarBloco_(sh, 14, 2, '#4a148c', fg);  // Auditoria - Roxo Escuro

  sh.setFrozenRows(1);
  sh.setFrozenColumns(3);
  sh.setRowHeight(1, 38);
  sh.getRange(1, 1, 1, headersBlocos[0].length)
    .setFontWeight('bold').setVerticalAlignment('middle').setHorizontalAlignment('center');

  // Formatos de colunas
  sh.getRange('D2:D').setNumberFormat('dd/mm/yyyy');
  sh.getRange('H2:H').setNumberFormat('dd/mm/yyyy');
  sh.getRange('J2:K').setNumberFormat('R$ #,##0.00');
  sh.getRange('L2:L').setNumberFormat('0.00%');
  sh.getRange('N2:N').setNumberFormat('dd/mm/yyyy hh:mm');

  // Fórmula Variação % (deixa preparada na linha 2 como modelo)
  sh.getRange('L2').setFormula('=IFERROR(IF(AND(ISNUMBER(J2),ISNUMBER(K2),J2<>0),(K2-J2)/J2,""),"")');
  // Fórmula Status Final
  sh.getRange('O2').setFormula(`=IF(G2="${STATUS.PRONTO}","Pronto para Fechamento","Pendente")`);

  // Validação de Status do Laudo
  const statusRange = sh.getRange('G2:G');
  const statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(STATUS_LIST, true).setAllowInvalid(false).build();
  statusRange.setDataValidation(statusRule);

  aplicarFormatacaoCondicional_(sh);
}

function pintarBloco_(sh, startCol, numCols, bg, fg) {
  sh.getRange(1, startCol, 1, numCols).setBackground(bg).setFontColor(fg);
}

function aplicarFormatacaoCondicional_(sh) {
  const rules = [];

  // Cores por status (coluna G)
  const statusColors = {
    [STATUS.AGUARDANDO]:     { bg: '#e0e0e0', fg: '#212121' },
    [STATUS.EM_CONFERENCIA]: { bg: '#fff2cc', fg: '#7f6000' },
    [STATUS.DISPONIVEL]:     { bg: '#cfe2f3', fg: '#0b5394' },
    [STATUS.EM_ANALISE]:     { bg: '#fce5cd', fg: '#b45f06' },
    [STATUS.VALOR_DEFINIDO]: { bg: '#d9ead3', fg: '#38761d' },
    [STATUS.PRONTO]:         { bg: '#1e5631', fg: '#ffffff' }
  };
  Object.keys(statusColors).forEach(s => {
    const c = statusColors[s];
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo(s)
      .setBackground(c.bg).setFontColor(c.fg)
      .setRanges([sh.getRange('G2:G')]).build());
  });

  // Variação > 10% ou < -10% -> fundo vermelho claro, texto vermelho escuro
  rules.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=OR(L2>0.1,L2<-0.1)')
    .setBackground('#f4cccc').setFontColor('#990000').setBold(true)
    .setRanges([sh.getRange('L2:L')]).build());

  // Linhas concluídas -> hachurado cinza claro
  rules.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied(`=$G2="${STATUS.PRONTO}"`)
    .setBackground('#f5f5f5').setFontColor('#9e9e9e')
    .setRanges([sh.getRange('A2:O')]).build());

  sh.setConditionalFormatRules(rules);
}

function criarAbaConfig_(ss) {
  let sh = ss.getSheetByName(SHEET_CFG);
  if (!sh) sh = ss.insertSheet(SHEET_CFG);
  sh.clear();

  sh.getRange('A1').setValue('FIIs (Fundos)').setFontWeight('bold').setBackground('#424242').setFontColor('#fff');
  sh.getRange('B1').setValue('Status disponíveis').setFontWeight('bold').setBackground('#424242').setFontColor('#fff');
  sh.getRange('C1').setValue('Responsável (Nome)').setFontWeight('bold').setBackground('#1a3a6c').setFontColor('#fff');
  sh.getRange('D1').setValue('E-mail').setFontWeight('bold').setBackground('#1a3a6c').setFontColor('#fff');
  sh.getRange('E1').setValue('Tipo (Admin/Precificação)').setFontWeight('bold').setBackground('#1a3a6c').setFontColor('#fff');

  sh.getRange(2, 2, STATUS_LIST.length, 1).setValues(STATUS_LIST.map(s => [s]));

  // Marcador + cabeçalho do log de auditoria
  sh.getRange('A20').setValue('=== LOG DE AUDITORIA ===').setFontWeight('bold').setBackground('#4a148c').setFontColor('#fff');
  const logHeader = [[
    'Timestamp', 'Usuário', 'Aba', 'Linha', 'ID', 'Fundo', 'Imóvel',
    'Campo alterado', 'Valor Anterior → Novo'
  ]];
  sh.getRange(21, 1, 1, logHeader[0].length).setValues(logHeader)
    .setFontWeight('bold').setBackground('#d9d2e9');

  sh.setFrozenRows(21);
  sh.hideSheet();
}

function criarAbaDashboard_(ss) {
  let sh = ss.getSheetByName(SHEET_DASH);
  if (!sh) sh = ss.insertSheet(SHEET_DASH, 0);
  sh.clear();
  sh.setHiddenGridlines(true);

  sh.getRange('B2').setValue('🏢 Dashboard de Fechamento de FIIs')
    .setFontSize(20).setFontWeight('bold').setFontColor('#1a3a6c');
  sh.getRange('B3').setFormula('="Atualizado em "&TEXT(NOW(),"dd/mm/yyyy hh:mm")')
    .setFontColor('#666');

  // KPIs
  const kpis = [
    ['Total de imóveis',     '=COUNTA(\'📊 Operacional\'!A2:A)'],
    ['% concluídos',         `=IFERROR(COUNTIF('📊 Operacional'!O2:O,"Pronto para Fechamento")/COUNTA('📊 Operacional'!A2:A),0)`],
    ['Aguardando laudo',     `=COUNTIF('📊 Operacional'!G2:G,"${STATUS.AGUARDANDO}")`],
    ['Disponíveis p/ precificar', `=COUNTIF('📊 Operacional'!G2:G,"${STATUS.DISPONIVEL}")`]
  ];
  kpis.forEach((kpi, i) => {
    const col = 2 + i * 2;
    sh.getRange(5, col).setValue(kpi[0])
      .setFontWeight('bold').setBackground('#1a3a6c').setFontColor('#fff')
      .setHorizontalAlignment('center');
    sh.getRange(6, col).setFormula(kpi[1])
      .setFontSize(22).setFontWeight('bold').setBackground('#f5f5f5')
      .setHorizontalAlignment('center');
    sh.setColumnWidth(col, 180);
  });
  sh.getRange('D6').setNumberFormat('0%');

  // Tabela de status (base para gráfico de pizza)
  sh.getRange('B10').setValue('Contagem por Status').setFontWeight('bold');
  sh.getRange(11, 2, STATUS_LIST.length, 1).setValues(STATUS_LIST.map(s => [s]));
  STATUS_LIST.forEach((s, i) => {
    sh.getRange(11 + i, 3).setFormula(`=COUNTIF('📊 Operacional'!G2:G,"${s}")`);
  });

  // Painel de alertas
  sh.getRange('F10').setValue('⚠️ Painel de Alertas (próximos ' + ALERT_WINDOW_DAYS + ' dias)')
    .setFontWeight('bold').setBackground('#f4cccc').setFontColor('#990000');
  sh.getRange('F11').setFormula(
    `=IFERROR(FILTER({'📊 Operacional'!B2:D,'📊 Operacional'!G2:G,'📊 Operacional'!E2:E},` +
    `'📊 Operacional'!O2:O<>"Pronto para Fechamento",` +
    `'📊 Operacional'!D2:D-TODAY()<=${ALERT_WINDOW_DAYS},` +
    `'📊 Operacional'!D2:D<>""),"Sem pendências críticas")`
  );

  sh.setColumnWidths(2, 10, 170);
}
