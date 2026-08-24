/**
 * =============================================================================
 * Dashboard de Controle de Fechamento de Exercicio - FIIs
 * =============================================================================
 * Escopo: comunicacao entre Administracao Fiduciaria e Precificacao,
 * com trilha de auditoria completa (quem, o que, quando).
 *
 * Convencoes:
 *   - Nao acentuar identificadores/constantes (evita bugs de encoding).
 *   - Toda escrita passa por helpers para manter formato consistente.
 *   - Logs de auditoria sao append-only (linhas nunca sao editadas).
 * =============================================================================
 */

// -----------------------------------------------------------------------------
// CONFIGURACAO CENTRAL
// -----------------------------------------------------------------------------

var CFG = {
  SHEETS: {
    DASHBOARD:   '🖥️ Dashboard',    // "🖥️ Dashboard"
    OPERACIONAL: '📊 Operacional',        // "📊 Operacional"
    CONFIG:      '⚙️ Config & Logs'        // "⚙️ Config & Logs"
  },

  // Cabecalhos da aba Operacional (ordem = colunas A..O)
  HEADERS_OP: [
    'ID', 'Fundo (FII)', 'Nome do Imovel', 'Data Base Fechamento',
    'Responsavel (Admin)', 'Link Drive (Pasta/Laudo)', 'Status do Laudo', 'Data Recebimento Laudo',
    'Responsavel (Precificacao)', 'Valor Contabil Anterior', 'Novo Valor (Laudo)', 'Variacao %', 'Comentarios/Justificativas',
    'Ultima Atualizacao', 'Status Final'
  ],

  // Indices 1-based para leitura defensiva (evita "magic numbers" espalhados)
  COL: {
    ID: 1, FUNDO: 2, IMOVEL: 3, DATA_BASE: 4,
    RESP_ADMIN: 5, LINK_DRIVE: 6, STATUS_LAUDO: 7, DATA_RECEB: 8,
    RESP_PREC: 9, VALOR_ANTERIOR: 10, VALOR_NOVO: 11, VARIACAO: 12, COMENTARIOS: 13,
    ULTIMA_ATU: 14, STATUS_FINAL: 15
  },

  // Cores por bloco (cabecalhos)
  BLOCK_COLORS: {
    IDENT:  { bg: '#3f3f3f', fg: '#ffffff' },   // Cinza Escuro
    ADMIN:  { bg: '#0b3d91', fg: '#ffffff' },   // Azul Escuro
    PREC:   { bg: '#0f5132', fg: '#ffffff' },   // Verde Escuro
    AUDIT:  { bg: '#4b246c', fg: '#ffffff' }    // Roxo Escuro
  },

  // Status do Laudo: (rotulo, cor de fundo, cor do texto)
  STATUS_LAUDO: [
    { label: '[ADMIN] Aguardando Laudo',              bg: '#d9d9d9', fg: '#1f1f1f' },
    { label: '[ADMIN] Laudo em Conferencia',          bg: '#fff2cc', fg: '#7f6000' },
    { label: '[PRECIFICACAO] Disponivel para Precificar', bg: '#cfe2f3', fg: '#0b3d91' },
    { label: '[PRECIFICACAO] Em Analise de Valor',    bg: '#fce5cd', fg: '#b45f06' },
    { label: '[PRECIFICACAO] Valor Final Definido',   bg: '#d9ead3', fg: '#274e13' },
    { label: '[AUDITORIA] Pronto para Fechamento',    bg: '#38761d', fg: '#ffffff' }
  ],

  STATUS_FINAL: [
    'Em Andamento',
    'Aguardando Revisao',
    'Pronto para Fechamento',
    'Bloqueado'
  ],

  // Colunas monitoradas para gravacao de log
  AUDITED_COLS: [7, 11, 13], // Status Laudo, Novo Valor, Comentarios

  // Coluna que muda -> atualiza carimbo de tempo
  TIMESTAMP_TRIGGER_COLS: [7, 11, 13, 15],

  // Dias de antecedencia para alerta de pendencia
  DIAS_ALERTA_PENDENCIA: 5,

  // Mapa de responsavel -> email (preenchido na aba Config)
  CONFIG_RANGE_EMAILS: 'C2:D',   // Nome | Email

  MAX_ROWS: 500
};

// -----------------------------------------------------------------------------
// MENU CUSTOMIZADO
// -----------------------------------------------------------------------------

/**
 * Cria menu proprio ao abrir a planilha.
 * Trigger simples - nao precisa autorizacao manual.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📋 FII Fechamento')
    .addItem('Verificar Pendencias', 'verificarPendencias')
    .addItem('Enviar Notificacoes Pendentes (manual)', 'enviarNotificacoesEmLote')
    .addSeparator()
    .addSubMenu(
      SpreadsheetApp.getUi().createMenu('Setup / Manutencao')
        .addItem('1) Criar/Recriar Estrutura de Abas', 'setupBootstrap')
        .addItem('2) Aplicar Validacoes de Dados', 'setupValidacoes')
        .addItem('3) Aplicar Formatacao Condicional', 'setupFormatacaoCondicional')
        .addItem('4) Popular linhas de exemplo', 'setupSeedExemplo')
    )
    .addToUi();
}

// -----------------------------------------------------------------------------
// BOOTSTRAP - constroi toda a planilha do zero
// -----------------------------------------------------------------------------

/**
 * Cria as 3 abas com estrutura, cores e cabecalhos.
 * Idempotente: pode ser rodado varias vezes.
 */
function setupBootstrap() {
  var ss = SpreadsheetApp.getActive();
  criarSheetOperacional_(ss);
  criarSheetDashboard_(ss);
  criarSheetConfigLogs_(ss);
  setupValidacoes();
  setupFormatacaoCondicional();
  SpreadsheetApp.getUi().alert(
    'Estrutura criada.\n\n' +
    'Proximo passo: menu "FII Fechamento" > Setup > Aplicar Validacoes/Formatacao.\n' +
    'Depois: crie o trigger onEdit instalavel (ver SETUP.md).'
  );
}

function criarSheetOperacional_(ss) {
  var sh = ss.getSheetByName(CFG.SHEETS.OPERACIONAL) || ss.insertSheet(CFG.SHEETS.OPERACIONAL);
  sh.clear();
  sh.setHiddenGridlines(false);

  // Cabecalho
  var range = sh.getRange(1, 1, 1, CFG.HEADERS_OP.length);
  range.setValues([CFG.HEADERS_OP])
       .setFontWeight('bold')
       .setFontColor('#ffffff')
       .setHorizontalAlignment('center')
       .setVerticalAlignment('middle')
       .setWrap(true);
  sh.setRowHeight(1, 42);

  // Blocos coloridos (cabecalho)
  aplicarBlocoCor_(sh, 1, 4,  CFG.BLOCK_COLORS.IDENT);   // A..D
  aplicarBlocoCor_(sh, 5, 4,  CFG.BLOCK_COLORS.ADMIN);   // E..H
  aplicarBlocoCor_(sh, 9, 5,  CFG.BLOCK_COLORS.PREC);    // I..M
  aplicarBlocoCor_(sh, 14, 2, CFG.BLOCK_COLORS.AUDIT);   // N..O

  // Larguras
  var widths = [70, 130, 240, 130, 150, 190, 240, 140, 160, 150, 150, 100, 260, 150, 190];
  widths.forEach(function(w, i) { sh.setColumnWidth(i + 1, w); });

  // Formatos numericos e de data
  sh.getRange(2, CFG.COL.DATA_BASE, CFG.MAX_ROWS).setNumberFormat('dd/mm/yyyy');
  sh.getRange(2, CFG.COL.DATA_RECEB, CFG.MAX_ROWS).setNumberFormat('dd/mm/yyyy');
  sh.getRange(2, CFG.COL.ULTIMA_ATU, CFG.MAX_ROWS).setNumberFormat('dd/mm/yyyy HH:mm:ss');
  sh.getRange(2, CFG.COL.VALOR_ANTERIOR, CFG.MAX_ROWS).setNumberFormat('R$ #,##0.00');
  sh.getRange(2, CFG.COL.VALOR_NOVO, CFG.MAX_ROWS).setNumberFormat('R$ #,##0.00');
  sh.getRange(2, CFG.COL.VARIACAO, CFG.MAX_ROWS).setNumberFormat('0.00%');

  // Formula Variacao % (relativa) - preenchida como arrayformula na col L
  var formVariacao = '=ARRAYFORMULA(IF((ROW(K2:K)>1)*(K2:K<>"")*(J2:J<>"")*(J2:J<>0), (K2:K-J2:J)/J2:J, ""))';
  sh.getRange(2, CFG.COL.VARIACAO).setFormula(formVariacao);

  // Congelar 1a linha e 3 primeiras colunas
  sh.setFrozenRows(1);
  sh.setFrozenColumns(3);

  // Alinhamentos padrao do corpo
  sh.getRange(2, 1, CFG.MAX_ROWS, CFG.HEADERS_OP.length)
    .setVerticalAlignment('middle')
    .setFontFamily('Arial')
    .setFontSize(10);

  // Coluna Link Drive: exibir como hyperlink amigavel (usuario cola a URL)
  sh.getRange(2, CFG.COL.LINK_DRIVE, CFG.MAX_ROWS).setHorizontalAlignment('left');
}

function criarSheetDashboard_(ss) {
  var sh = ss.getSheetByName(CFG.SHEETS.DASHBOARD) || ss.insertSheet(CFG.SHEETS.DASHBOARD);
  sh.clear();
  sh.setHiddenGridlines(true);
  sh.setTabColor('#0b3d91');

  // Titulo
  sh.getRange('B2:H2').merge()
    .setValue('Dashboard - Fechamento de Exercicio FIIs')
    .setFontSize(20).setFontWeight('bold')
    .setFontColor('#0b3d91')
    .setHorizontalAlignment('center');
  sh.setRowHeight(2, 44);

  // Cards de KPI (formulas contam a partir da Operacional)
  var opRef = "'" + CFG.SHEETS.OPERACIONAL + "'";
  var kpis = [
    { label: 'Total de Imoveis',           formula: '=COUNTA(' + opRef + '!C2:C)' },
    { label: 'Aguardando Laudo',           formula: '=COUNTIF(' + opRef + '!G2:G,"[ADMIN] Aguardando Laudo")' },
    { label: 'Disponivel p/ Precificar',   formula: '=COUNTIF(' + opRef + '!G2:G,"[PRECIFICACAO] Disponivel para Precificar")' },
    { label: 'Prontos para Fechamento',    formula: '=COUNTIF(' + opRef + '!O2:O,"Pronto para Fechamento")' }
  ];
  var col = 2;
  kpis.forEach(function(k) {
    sh.getRange(4, col, 1, 2).merge()
      .setValue(k.label)
      .setBackground('#0b3d91').setFontColor('#ffffff')
      .setFontWeight('bold').setHorizontalAlignment('center');
    sh.getRange(5, col, 1, 2).merge()
      .setFormula(k.formula)
      .setBackground('#f0f4fb').setFontColor('#0b3d91')
      .setFontSize(22).setFontWeight('bold')
      .setHorizontalAlignment('center');
    sh.setRowHeight(5, 60);
    col += 2;
  });

  // Painel de Alertas: imoveis atrasados (Data Base < hoje e Status Final != Pronto)
  sh.getRange('B8').setValue('Painel de Alertas - Laudos Atrasados')
    .setFontSize(14).setFontWeight('bold').setFontColor('#a61b1b');

  var headerAlertas = [['Fundo', 'Imovel', 'Data Base', 'Dias em Atraso', 'Status Atual']];
  sh.getRange(9, 2, 1, 5).setValues(headerAlertas)
    .setBackground('#a61b1b').setFontColor('#ffffff').setFontWeight('bold');

  // Formula que traz atrasados via QUERY - simples e reativa
  var qFormula =
    '=IFERROR(QUERY(' + opRef + "!B2:O, " +
    "\"select B, C, D, TODAY()-D, G where D is not null and D < date '\"&TEXT(TODAY(),\"yyyy-mm-dd\")&\"' " +
    "and O <> 'Pronto para Fechamento' order by D asc label TODAY()-D 'Dias em Atraso'\", 0), \"Sem pendencias.\")";
  sh.getRange('B10').setFormula(qFormula);

  // Areas reservadas para graficos (usuario insere manualmente - Inserir > Grafico)
  sh.getRange('B22').setValue('[Espaco reservado] Grafico: Status dos Laudos (Pizza)')
    .setFontStyle('italic').setFontColor('#6b7280');
  sh.getRange('E22').setValue('[Espaco reservado] Grafico: Imoveis por Fundo (Barras)')
    .setFontStyle('italic').setFontColor('#6b7280');

  // Botao (desenho) - Instrucao para o usuario
  sh.getRange('B26').setValue(
    'Passo manual: Inserir > Desenho, criar retangulo "Gerar Relatorio de Auditoria", ' +
    'atribuir script "verificarPendencias".'
  ).setFontStyle('italic').setFontColor('#374151');

  // Larguras
  [40, 200, 260, 130, 130, 200, 40].forEach(function(w, i) { sh.setColumnWidth(i + 1, w); });
}

function criarSheetConfigLogs_(ss) {
  var sh = ss.getSheetByName(CFG.SHEETS.CONFIG) || ss.insertSheet(CFG.SHEETS.CONFIG);
  sh.clear();
  sh.setTabColor('#4b246c');

  // Bloco A: Listas de dominio (fundos, responsaveis)
  sh.getRange('A1').setValue('LISTAS DE DOMINIO').setFontWeight('bold').setBackground('#e5e7eb');
  sh.getRange('A2').setValue('Fundos (FII)').setFontWeight('bold');
  sh.getRange('C1').setValue('MAPA DE RESPONSAVEIS').setFontWeight('bold').setBackground('#e5e7eb');
  sh.getRange('C2:D2').setValues([['Nome', 'Email']]).setFontWeight('bold');

  // Bloco B: LOG de auditoria (append-only)
  sh.getRange('F1').setValue('LOG DE AUDITORIA (append-only - NAO EDITAR)')
    .setFontWeight('bold').setBackground('#4b246c').setFontColor('#ffffff');
  var logHeaders = [['Timestamp', 'Usuario', 'Aba', 'Linha', 'ID do Imovel', 'Coluna Alterada', 'Valor Anterior', 'Valor Novo']];
  sh.getRange(2, 6, 1, 8).setValues(logHeaders)
    .setFontWeight('bold').setBackground('#e5e7eb');
  sh.getRange(2, 6, 1, 8).setBorder(true, true, true, true, false, false);

  // Larguras
  var widths = { A: 220, B: 20, C: 200, D: 240, E: 20, F: 160, G: 200, H: 130, I: 90, J: 120, K: 200, L: 260, M: 260 };
  Object.keys(widths).forEach(function(letter) {
    sh.setColumnWidth(letterToCol_(letter), widths[letter]);
  });

  sh.setFrozenRows(2);

  // Ocultar (pode ser reexibida via menu de abas)
  sh.hideSheet();

  // Protecao contra edicao acidental (opcional; setUnprotectedRanges nao existe -
  // aqui bloqueamos toda a aba, exceto os intervalos de dominio A/C:D).
  var prot = sh.protect().setDescription('Config & Logs - protegida');
  prot.setWarningOnly(true); // apenas aviso - permite edicao pelos admins
}

function aplicarBlocoCor_(sh, colStart, colCount, color) {
  sh.getRange(1, colStart, 1, colCount)
    .setBackground(color.bg)
    .setFontColor(color.fg);
}

// -----------------------------------------------------------------------------
// VALIDACOES DE DADOS
// -----------------------------------------------------------------------------

function setupValidacoes() {
  var ss = SpreadsheetApp.getActive();
  var op = ss.getSheetByName(CFG.SHEETS.OPERACIONAL);
  if (!op) throw new Error('Aba Operacional nao existe. Rode setupBootstrap primeiro.');

  // Status do Laudo (col G) - lista fixa
  var statusList = CFG.STATUS_LAUDO.map(function(s) { return s.label; });
  var ruleStatus = SpreadsheetApp.newDataValidation()
    .requireValueInList(statusList, true)
    .setAllowInvalid(false)
    .setHelpText('Selecione um status do fluxo.')
    .build();
  op.getRange(2, CFG.COL.STATUS_LAUDO, CFG.MAX_ROWS).setDataValidation(ruleStatus);

  // Status Final (col O)
  var ruleFinal = SpreadsheetApp.newDataValidation()
    .requireValueInList(CFG.STATUS_FINAL, true)
    .setAllowInvalid(false)
    .build();
  op.getRange(2, CFG.COL.STATUS_FINAL, CFG.MAX_ROWS).setDataValidation(ruleFinal);

  // Fundo (col B) - lista dinamica da Config!A3:A
  var config = ss.getSheetByName(CFG.SHEETS.CONFIG);
  if (config) {
    var fundosRange = config.getRange('A3:A');
    var ruleFundo = SpreadsheetApp.newDataValidation()
      .requireValueInRange(fundosRange, true)
      .setAllowInvalid(true) // permite digitar novo, sinaliza fora da lista
      .build();
    op.getRange(2, CFG.COL.FUNDO, CFG.MAX_ROWS).setDataValidation(ruleFundo);

    // Responsavel Admin e Precificacao a partir do mapa (col C)
    var responsaveis = config.getRange('C3:C');
    var ruleResp = SpreadsheetApp.newDataValidation()
      .requireValueInRange(responsaveis, true)
      .setAllowInvalid(true)
      .build();
    op.getRange(2, CFG.COL.RESP_ADMIN, CFG.MAX_ROWS).setDataValidation(ruleResp);
    op.getRange(2, CFG.COL.RESP_PREC, CFG.MAX_ROWS).setDataValidation(ruleResp);
  }
}

// -----------------------------------------------------------------------------
// FORMATACAO CONDICIONAL
// -----------------------------------------------------------------------------

function setupFormatacaoCondicional() {
  var ss = SpreadsheetApp.getActive();
  var op = ss.getSheetByName(CFG.SHEETS.OPERACIONAL);
  if (!op) throw new Error('Aba Operacional nao existe.');

  var rules = [];

  // 1) Cores por Status do Laudo (col G)
  var rangeStatus = op.getRange(2, CFG.COL.STATUS_LAUDO, CFG.MAX_ROWS);
  CFG.STATUS_LAUDO.forEach(function(s) {
    rules.push(
      SpreadsheetApp.newConditionalFormatRule()
        .whenTextEqualTo(s.label)
        .setBackground(s.bg)
        .setFontColor(s.fg)
        .setRanges([rangeStatus])
        .build()
    );
  });

  // 2) Variacao % - fundo vermelho claro quando |x| > 10%
  var rangeVar = op.getRange(2, CFG.COL.VARIACAO, CFG.MAX_ROWS);
  rules.push(
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND(ISNUMBER(L2), ABS(L2)>0.10)')
      .setBackground('#f8d7da')
      .setFontColor('#7a1620')
      .setBold(true)
      .setRanges([rangeVar])
      .build()
  );

  // 3) Linha inteira hachurada quando Status Final = "Pronto para Fechamento"
  var fullRow = op.getRange(2, 1, CFG.MAX_ROWS, CFG.HEADERS_OP.length);
  rules.push(
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=$O2="Pronto para Fechamento"')
      .setBackground('#f1f3f4')
      .setFontColor('#6b7280')
      .setRanges([fullRow])
      .build()
  );

  // 4) Data Base < hoje e Status Final != Pronto -> destaca coluna D
  var rangeDataBase = op.getRange(2, CFG.COL.DATA_BASE, CFG.MAX_ROWS);
  rules.push(
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND(ISDATE(D2), D2<TODAY(), $O2<>"Pronto para Fechamento")')
      .setBackground('#fde2e2')
      .setBold(true)
      .setRanges([rangeDataBase])
      .build()
  );

  op.setConditionalFormatRules(rules);
}

// -----------------------------------------------------------------------------
// EDIT HANDLER - roteador central
// -----------------------------------------------------------------------------

/**
 * Handler unico do trigger onEdit instalavel.
 * DEVE ser criado como trigger instalavel (nao simples), para poder enviar email.
 */
function onEditInstallable(e) {
  try {
    if (!e || !e.range) return;
    var sh = e.range.getSheet();
    if (sh.getName() !== CFG.SHEETS.OPERACIONAL) return;

    var col = e.range.getColumn();
    var row = e.range.getRow();
    if (row === 1) return; // cabecalho

    // 1) LOG de auditoria nas colunas monitoradas
    if (CFG.AUDITED_COLS.indexOf(col) !== -1) {
      registrarLog_(e, sh, row, col);
    }

    // 2) Carimbo de tempo sempre que campo relevante muda
    if (CFG.TIMESTAMP_TRIGGER_COLS.indexOf(col) !== -1) {
      atualizarCarimbo_(sh, row);
    }

    // 3) Notificacao ao mudar Status para "Disponivel para Precificar"
    if (col === CFG.COL.STATUS_LAUDO &&
        String(e.value) === '[PRECIFICACAO] Disponivel para Precificar') {
      enviarNotificacaoPrecificacao(sh, row);
    }
  } catch (err) {
    // Nao levantar - onEdit falhando trava a UI do usuario.
    console.error('onEditInstallable error:', err);
  }
}

// -----------------------------------------------------------------------------
// LOG DE AUDITORIA
// -----------------------------------------------------------------------------

function registrarLog_(e, sh, row, col) {
  var ss = SpreadsheetApp.getActive();
  var logSh = ss.getSheetByName(CFG.SHEETS.CONFIG);
  if (!logSh) return;

  var user = (Session.getActiveUser() && Session.getActiveUser().getEmail()) ||
             (Session.getEffectiveUser() && Session.getEffectiveUser().getEmail()) ||
             'desconhecido';

  var idImovel = sh.getRange(row, CFG.COL.ID).getValue();
  var colName = CFG.HEADERS_OP[col - 1] || ('Col ' + col);
  var oldVal = (e.oldValue !== undefined) ? e.oldValue : '';
  var newVal = (e.value !== undefined) ? e.value : e.range.getValue();

  // Append no bloco de log (colunas F..M da aba Config)
  var lastRow = Math.max(logSh.getLastRow(), 2);
  logSh.getRange(lastRow + 1, 6, 1, 8).setValues([[
    new Date(), user, sh.getName(), row, idImovel, colName, oldVal, newVal
  ]]);
}

// -----------------------------------------------------------------------------
// CARIMBO DE TEMPO
// -----------------------------------------------------------------------------

function atualizarCarimbo_(sh, row) {
  sh.getRange(row, CFG.COL.ULTIMA_ATU).setValue(new Date());
}

// -----------------------------------------------------------------------------
// NOTIFICACAO POR EMAIL - Precificacao
// -----------------------------------------------------------------------------

/**
 * Envia email ao responsavel de Precificacao quando o laudo esta pronto.
 * @param {Sheet} sh Aba operacional
 * @param {number} row Linha do imovel
 */
function enviarNotificacaoPrecificacao(sh, row) {
  var dados = lerLinhaImovel_(sh, row);
  var email = resolverEmail_(dados.respPrec);
  if (!email) {
    console.warn('Sem email para responsavel:', dados.respPrec);
    return;
  }

  var assunto = '[FII Fechamento] Laudo disponivel: ' + dados.imovel + ' (' + dados.fundo + ')';
  var corpoHtml =
    '<div style="font-family:Arial,sans-serif;font-size:14px;color:#1a2332">' +
      '<h2 style="color:#0b3d91;margin:0 0 8px">Laudo disponivel para precificacao</h2>' +
      '<p>Ola ' + escapeHtml_(dados.respPrec) + ',</p>' +
      '<p>O laudo do imovel abaixo foi disponibilizado pela Administracao Fiduciaria ' +
      'e esta pronto para sua analise de valor.</p>' +
      '<table cellpadding="6" style="border-collapse:collapse;border:1px solid #d0d5dd">' +
        row_('Fundo', dados.fundo) +
        row_('Imovel', dados.imovel) +
        row_('Data Base Fechamento', formatarData_(dados.dataBase)) +
        row_('Responsavel Admin', dados.respAdmin) +
        row_('Link do Laudo', dados.linkDrive ? '<a href="' + escapeHtml_(dados.linkDrive) + '">Abrir Drive</a>' : '-') +
      '</table>' +
      '<p style="margin-top:16px;color:#4a5568;font-size:12px">' +
      'Mensagem automatica - Dashboard FII Fechamento.</p>' +
    '</div>';

  MailApp.sendEmail({ to: email, subject: assunto, htmlBody: corpoHtml });
}

function row_(label, value) {
  return '<tr>' +
           '<td style="border:1px solid #d0d5dd;background:#f8f9fb"><b>' + escapeHtml_(label) + '</b></td>' +
           '<td style="border:1px solid #d0d5dd">' + (value || '-') + '</td>' +
         '</tr>';
}

function lerLinhaImovel_(sh, row) {
  var v = sh.getRange(row, 1, 1, CFG.HEADERS_OP.length).getValues()[0];
  return {
    id:         v[CFG.COL.ID - 1],
    fundo:      v[CFG.COL.FUNDO - 1],
    imovel:     v[CFG.COL.IMOVEL - 1],
    dataBase:   v[CFG.COL.DATA_BASE - 1],
    respAdmin:  v[CFG.COL.RESP_ADMIN - 1],
    linkDrive:  v[CFG.COL.LINK_DRIVE - 1],
    status:     v[CFG.COL.STATUS_LAUDO - 1],
    dataReceb:  v[CFG.COL.DATA_RECEB - 1],
    respPrec:   v[CFG.COL.RESP_PREC - 1],
    valorAnt:   v[CFG.COL.VALOR_ANTERIOR - 1],
    valorNovo:  v[CFG.COL.VALOR_NOVO - 1],
    variacao:   v[CFG.COL.VARIACAO - 1],
    coment:     v[CFG.COL.COMENTARIOS - 1],
    ultimaAtu:  v[CFG.COL.ULTIMA_ATU - 1],
    statusFinal:v[CFG.COL.STATUS_FINAL - 1]
  };
}

/**
 * Resolve nome -> email pelo mapa da aba Config (C:D).
 * Fallback: se o "nome" ja parece email, retorna ele mesmo.
 */
function resolverEmail_(nome) {
  if (!nome) return null;
  var s = String(nome).trim();
  if (/@/.test(s)) return s;
  var config = SpreadsheetApp.getActive().getSheetByName(CFG.SHEETS.CONFIG);
  if (!config) return null;
  var mapa = config.getRange('C3:D').getValues();
  for (var i = 0; i < mapa.length; i++) {
    if (mapa[i][0] && String(mapa[i][0]).trim().toLowerCase() === s.toLowerCase()) {
      return mapa[i][1] || null;
    }
  }
  return null;
}

// -----------------------------------------------------------------------------
// RELATORIO DE AUDITORIA - botao "Gerar Relatorio de Auditoria"
// -----------------------------------------------------------------------------

/**
 * Varre a aba Operacional e alerta imoveis pendentes cuja Data Base
 * esta a menos de N dias (default 5).
 */
function verificarPendencias() {
  var ui = SpreadsheetApp.getUi();
  var op = SpreadsheetApp.getActive().getSheetByName(CFG.SHEETS.OPERACIONAL);
  if (!op) {
    ui.alert('Aba Operacional nao encontrada.');
    return;
  }

  var lastRow = op.getLastRow();
  if (lastRow < 2) {
    ui.alert('Nenhum imovel cadastrado.');
    return;
  }

  var dados = op.getRange(2, 1, lastRow - 1, CFG.HEADERS_OP.length).getValues();
  var hoje = new Date(); hoje.setHours(0, 0, 0, 0);
  var limite = new Date(hoje.getTime() + CFG.DIAS_ALERTA_PENDENCIA * 86400000);

  var pendentes = [];
  dados.forEach(function(r, idx) {
    var dataBase = r[CFG.COL.DATA_BASE - 1];
    var statusFinal = r[CFG.COL.STATUS_FINAL - 1];
    var fundo = r[CFG.COL.FUNDO - 1];
    var imovel = r[CFG.COL.IMOVEL - 1];
    if (!(dataBase instanceof Date)) return;
    if (statusFinal === 'Pronto para Fechamento') return;
    if (dataBase.getTime() <= limite.getTime()) {
      var diasRest = Math.round((dataBase.getTime() - hoje.getTime()) / 86400000);
      pendentes.push({
        linha: idx + 2, fundo: fundo, imovel: imovel,
        dataBase: dataBase, diasRest: diasRest, status: r[CFG.COL.STATUS_LAUDO - 1]
      });
    }
  });

  if (pendentes.length === 0) {
    ui.alert('OK', 'Nenhuma pendencia critica nos proximos ' +
             CFG.DIAS_ALERTA_PENDENCIA + ' dias.', ui.ButtonSet.OK);
    return;
  }

  pendentes.sort(function(a, b) { return a.diasRest - b.diasRest; });

  var linhas = pendentes.slice(0, 25).map(function(p) {
    var qual = (p.diasRest < 0) ? ('ATRASADO ' + Math.abs(p.diasRest) + 'd')
                                 : ('em ' + p.diasRest + 'd');
    return '  - [' + p.fundo + '] ' + p.imovel +
           '  (' + formatarData_(p.dataBase) + ', ' + qual + ') - ' + p.status;
  }).join('\n');

  var extra = pendentes.length > 25 ? ('\n\n(+ ' + (pendentes.length - 25) + ' outros...)') : '';
  ui.alert('Pendencias de Fechamento (' + pendentes.length + ')',
           linhas + extra, ui.ButtonSet.OK);
}

// -----------------------------------------------------------------------------
// NOTIFICACOES EM LOTE - opcional (util em rerun manual)
// -----------------------------------------------------------------------------

function enviarNotificacoesEmLote() {
  var op = SpreadsheetApp.getActive().getSheetByName(CFG.SHEETS.OPERACIONAL);
  if (!op) return;
  var lastRow = op.getLastRow();
  if (lastRow < 2) return;

  var dados = op.getRange(2, 1, lastRow - 1, CFG.HEADERS_OP.length).getValues();
  var enviados = 0;
  dados.forEach(function(r, idx) {
    if (r[CFG.COL.STATUS_LAUDO - 1] === '[PRECIFICACAO] Disponivel para Precificar') {
      enviarNotificacaoPrecificacao(op, idx + 2);
      enviados++;
    }
  });
  SpreadsheetApp.getUi().alert('Emails enviados: ' + enviados);
}

// -----------------------------------------------------------------------------
// SEED - popula exemplos para teste
// -----------------------------------------------------------------------------

function setupSeedExemplo() {
  var ss = SpreadsheetApp.getActive();
  var op = ss.getSheetByName(CFG.SHEETS.OPERACIONAL);
  var config = ss.getSheetByName(CFG.SHEETS.CONFIG);
  if (!op || !config) throw new Error('Rode setupBootstrap primeiro.');

  // Dominios
  config.getRange('A3:A6').setValues([['FII ABCD11'], ['FII XYZW11'], ['FII LMNO11'], ['FII QRST11']]);
  config.getRange('C3:D5').setValues([
    ['Ana Admin',     'ana.admin@exemplo.com'],
    ['Bruno Prec',    'bruno.prec@exemplo.com'],
    ['Carla Prec',    'carla.prec@exemplo.com']
  ]);

  var hoje = new Date();
  function addDias(d) { return new Date(hoje.getTime() + d * 86400000); }

  var linhas = [
    ['IM-001', 'FII ABCD11', 'Edificio Alpha',   addDias(3),  'Ana Admin', 'https://drive.google.com/exemplo1', '[ADMIN] Aguardando Laudo',                 '',           'Bruno Prec',  1000000, '',      '', '', '', 'Em Andamento'],
    ['IM-002', 'FII ABCD11', 'Galpao Beta',      addDias(-2), 'Ana Admin', 'https://drive.google.com/exemplo2', '[PRECIFICACAO] Disponivel para Precificar', addDias(-5),  'Carla Prec',  2500000, 2800000, '', '', '', 'Em Andamento'],
    ['IM-003', 'FII XYZW11', 'Shopping Gamma',   addDias(10), 'Ana Admin', 'https://drive.google.com/exemplo3', '[PRECIFICACAO] Valor Final Definido',      addDias(-8),  'Bruno Prec',  8000000, 9500000, '', 'Reavaliacao positiva por vacancia menor', '', 'Aguardando Revisao'],
    ['IM-004', 'FII LMNO11', 'Torre Delta',      addDias(20), 'Ana Admin', 'https://drive.google.com/exemplo4', '[AUDITORIA] Pronto para Fechamento',       addDias(-15), 'Carla Prec',  5000000, 4600000, '', 'Reducao dentro do esperado', '', 'Pronto para Fechamento']
  ];
  op.getRange(2, 1, linhas.length, linhas[0].length).setValues(linhas);
}

// -----------------------------------------------------------------------------
// UTILITARIOS
// -----------------------------------------------------------------------------

function letterToCol_(letter) {
  var s = String(letter).toUpperCase();
  var n = 0;
  for (var i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64);
  return n;
}

function formatarData_(d) {
  if (!(d instanceof Date)) return d || '';
  return Utilities.formatDate(d, Session.getScriptTimeZone(), 'dd/MM/yyyy');
}

function escapeHtml_(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
