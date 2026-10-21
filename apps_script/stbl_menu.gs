/**
 * Menu « STBL » du classeur : lance les commandes Python sur GitHub Actions (workflow stbl-run.yml).
 *
 * Installation : Extensions > Apps Script, coller ce fichier, enregistrer, recharger le classeur,
 * puis STBL > Maintenance > Configurer le jeton GitHub (jeton à accès fin, dépôt stbl_project seul,
 * droit « Actions : lecture et écriture »). Le jeton est gardé dans les propriétés du script, jamais
 * dans une cellule.
 *
 * Chaque lancement ajoute une ligne à l'onglet runs (statut « demandé ») ; le traitement GitHub la met
 * à jour : en cours, puis OK / erreur, durée, lien vers le log, dernières lignes en cas d'erreur.
 */

var GITHUB = {owner: 'wmerieau85', repo: 'stbl_project', workflow: 'stbl-run.yml', ref: 'main'};
var RUNS_TAB = 'runs';
var RUNS_HEADER = ['Clé', 'Demandé le', 'Menu', 'Statut', 'Début', 'Fin', 'Durée (s)', 'Log', 'Détail'];
var TOKEN_PROPERTY = 'GITHUB_TOKEN';

// code (scripts/run.py) -> libellé du menu
var LABELS = {
  projections_update: 'Projections > Mise à jour',
  projections_recalc: 'Projections > Recalcul seul',
  lottery_draw: 'Draft > Tirage lottery',
  draft_reco: 'Draft > Reco',
  draft_watch: 'Draft > Watch',
  yahoo_draft: 'Draft > Yahoo Draft',
  yahoo_rankings: 'Draft > Yahoo Rankings',
  yahoo_rosters: 'Yahoo > MàJ rosters',
  yahoo_stats: 'Yahoo > Standings + Team Log',
  season_update: 'Saison > Projections',
  season_moves: 'Saison > Moves',
  season_all: 'Saison > Tout mettre à jour',
  history_snapshot: 'Maintenance > Photo des sources',
  sources_accuracy: 'Maintenance > Pertinence des sources',
  settings_check: 'Maintenance > Contrôle settings'
};

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('STBL')
    .addSubMenu(ui.createMenu('Projections')
      .addItem('Mise à jour', 'run_projections_update')
      .addItem('Recalcul seul', 'run_projections_recalc'))
    .addSubMenu(ui.createMenu('Draft')
      .addItem('Tirage lottery', 'run_lottery_draw')
      .addItem('Reco', 'run_draft_reco')
      .addItem('Watch', 'run_draft_watch')
      .addItem('Arrêter le watch', 'stopWatch')
      .addSeparator()
      .addItem('Yahoo Draft', 'run_yahoo_draft')
      .addItem('Yahoo Rankings', 'run_yahoo_rankings'))
    .addSubMenu(ui.createMenu('Yahoo')
      .addItem('MàJ rosters', 'run_yahoo_rosters')
      .addItem('Standings + Team Log', 'run_yahoo_stats'))
    .addSubMenu(ui.createMenu('Saison')
      .addItem('Projections', 'run_season_update')
      .addItem('Moves', 'run_season_moves')
      .addItem('Tout mettre à jour', 'run_season_all'))
    .addSubMenu(ui.createMenu('Maintenance')
      .addItem('Contrôle settings', 'run_settings_check')
      .addItem('Photo des sources', 'run_history_snapshot')
      .addItem('Pertinence des sources', 'run_sources_accuracy')
      .addItem('Dernier lancement', 'lastRun')
      .addSeparator()
      .addItem('Configurer le jeton GitHub', 'configureToken'))
    .addToUi();
}

function run_projections_update() { launch_('projections_update'); }
function run_projections_recalc() { launch_('projections_recalc'); }
function run_lottery_draw() {
  var ui = SpreadsheetApp.getUi();
  if (ui.alert('Tirage lottery', 'Lancer le tirage (boules générées et tirage écrit dans le classeur) ?',
               ui.ButtonSet.YES_NO) === ui.Button.YES) {
    launch_('lottery_draw');
  }
}
function run_draft_reco() { launch_('draft_reco'); }
function run_draft_watch() { launch_('draft_watch'); }
function run_yahoo_draft() { launch_('yahoo_draft'); }
function run_yahoo_rankings() { launch_('yahoo_rankings'); }
function run_yahoo_rosters() { launch_('yahoo_rosters'); }
function run_yahoo_stats() { launch_('yahoo_stats'); }
function run_season_update() { launch_('season_update'); }
function run_season_moves() { launch_('season_moves'); }
function run_season_all() { launch_('season_all'); }
function run_history_snapshot() { launch_('history_snapshot'); }
function run_sources_accuracy() { launch_('sources_accuracy'); }
function run_settings_check() { launch_('settings_check'); }

function token_() {
  var token = PropertiesService.getScriptProperties().getProperty(TOKEN_PROPERTY);
  if (!token) {
    throw new Error('Jeton GitHub absent : STBL > Maintenance > Configurer le jeton GitHub.');
  }
  return token;
}

function github_(method, path, payload) {
  var options = {
    method: method,
    muteHttpExceptions: true,
    headers: {
      Authorization: 'Bearer ' + token_(),
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    }
  };
  if (payload) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(payload);
  }
  var url = 'https://api.github.com/repos/' + GITHUB.owner + '/' + GITHUB.repo + path;
  var response = UrlFetchApp.fetch(url, options);
  var text = response.getContentText();
  return {code: response.getResponseCode(), body: text ? JSON.parse(text) : {}};
}

function runsSheet_() {
  var book = SpreadsheetApp.getActive();
  var sheet = book.getSheetByName(RUNS_TAB);
  if (!sheet) {
    sheet = book.insertSheet(RUNS_TAB);
    sheet.getRange(1, 1, 1, RUNS_HEADER.length).setValues([RUNS_HEADER]).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function now_(format) {
  return Utilities.formatDate(new Date(), 'Europe/Paris', format || 'yyyy-MM-dd HH:mm:ss');
}

function launch_(code) {
  var label = LABELS[code];
  var key = now_('yyyyMMdd-HHmmss') + '-' + code;
  var sheet = runsSheet_();
  var row = sheet.getLastRow() + 1;
  sheet.getRange(row, 1, 1, 4).setValues([[key, now_(), label, 'demandé']]);
  var result;
  try {
    result = github_('post', '/actions/workflows/' + GITHUB.workflow + '/dispatches',
                     {ref: GITHUB.ref, inputs: {command: code, key: key}});
  } catch (e) {
    sheet.getRange(row, 4).setValue('refusé');
    sheet.getRange(row, 9).setValue(String(e.message || e));
    SpreadsheetApp.getUi().alert(String(e.message || e));
    return;
  }
  if (result.code === 204) {
    SpreadsheetApp.getActive().toast(label + ' : lancé. Résultat dans 1 à 3 minutes (onglet ' + RUNS_TAB + ').',
                                     'STBL', 8);
  } else {
    var message = 'GitHub a refusé le lancement (' + result.code + ') : ' + (result.body.message || '');
    sheet.getRange(row, 4).setValue('refusé');
    sheet.getRange(row, 9).setValue(message);
    SpreadsheetApp.getUi().alert(message);
  }
}

function stopWatch() {
  var cancelled = 0;
  ['in_progress', 'queued'].forEach(function (status) {
    var runs = github_('get', '/actions/workflows/' + GITHUB.workflow + '/runs?status=' + status + '&per_page=50');
    (runs.body.workflow_runs || []).forEach(function (run) {
      if (String(run.display_title || '').indexOf('draft_watch') === 0) {
        if (github_('post', '/actions/runs/' + run.id + '/cancel').code === 202) {
          cancelled++;
        }
      }
    });
  });
  var sheet = runsSheet_();
  var last = sheet.getLastRow();
  if (last > 1) {
    var values = sheet.getRange(2, 1, last - 1, 4).getValues();
    for (var i = 0; i < values.length; i++) {
      if (String(values[i][0]).indexOf('-draft_watch') > 0 && ['demandé', 'en cours'].indexOf(values[i][3]) >= 0) {
        sheet.getRange(i + 2, 4, 1, 3).setValues([['arrêté', sheet.getRange(i + 2, 5).getValue(), now_()]]);
      }
    }
  }
  SpreadsheetApp.getUi().alert(cancelled ? 'Watch arrêté.' : 'Aucun watch en cours.');
}

function lastRun() {
  var sheet = runsSheet_();
  var last = sheet.getLastRow();
  if (last < 2) {
    SpreadsheetApp.getUi().alert('Aucun lancement.');
    return;
  }
  var v = sheet.getRange(last, 1, 1, RUNS_HEADER.length).getValues()[0];
  var text = v[2] + '\nStatut : ' + v[3] + '\nDemandé le : ' + v[1] + (v[6] !== '' ? '\nDurée : ' + v[6] + ' s' : '') +
             (v[7] ? '\nLog : ' + v[7] : '') + (v[8] ? '\n\n' + v[8] : '');
  SpreadsheetApp.getUi().alert('Dernier lancement', text, SpreadsheetApp.getUi().ButtonSet.OK);
}

function configureToken() {
  var ui = SpreadsheetApp.getUi();
  var answer = ui.prompt('Jeton GitHub',
    'Jeton à accès fin (github.com > Settings > Developer settings > Fine-grained tokens), dépôt ' +
    GITHUB.repo + ' seul, droit Actions : lecture et écriture. Vide = supprimer le jeton.',
    ui.ButtonSet.OK_CANCEL);
  if (answer.getSelectedButton() !== ui.Button.OK) {
    return;
  }
  var value = answer.getResponseText().trim();
  var props = PropertiesService.getScriptProperties();
  if (value) {
    props.setProperty(TOKEN_PROPERTY, value);
    var check = github_('get', '/actions/workflows/' + GITHUB.workflow);
    ui.alert(check.code === 200 ? 'Jeton enregistré, accès au traitement GitHub vérifié.'
                                : 'Jeton enregistré, mais GitHub répond ' + check.code + ' : ' + (check.body.message || ''));
  } else {
    props.deleteProperty(TOKEN_PROPERTY);
    ui.alert('Jeton supprimé.');
  }
}
