/*
 * Export des projections LineupExperts en CSV (tous les joueurs, toutes les pages).
 *
 * Le site est protégé par Cloudflare : on ne peut pas le lire depuis un script.
 * Ce code s'exécute donc dans votre navigateur, sur la page déjà ouverte :
 *   https://www.lineupexperts.com/basketball/projections?flt_proj_time_period=Preseason
 *
 * Il lit toutes les lignes du tableau via l'API DataTables (y compris les pages
 * non affichées), garde la valeur de chaque stat (sans le z-score entre
 * parenthèses) et télécharge lineupexperts_<étape>_AAAA-MM-JJ.csv.
 *
 * Utilisation : voir tools/README.md (favori « bookmarklet » ou console F12).
 */
(function () {
  var $ = window.jQuery;
  var body = document.querySelector('table.projectionsOutputTable tbody');
  var tableEl = document.querySelector('table#results') || (body && body.closest('table'));
  if (!tableEl) { alert('Tableau des projections introuvable : ouvrez la page Projections de LineupExperts.'); return; }

  var api = ($ && $.fn && $.fn.dataTable && $.fn.dataTable.isDataTable(tableEl)) ? $(tableEl).DataTable() : null;
  var rows = api ? api.rows().nodes().toArray() : Array.prototype.slice.call(tableEl.querySelectorAll('tbody tr'));
  var headerCells = api ? api.columns().header().toArray() : Array.prototype.slice.call(tableEl.querySelectorAll('thead th'));
  var headers = headerCells.map(function (th) { return th.textContent.replace(/\s+/g, ' ').trim(); });

  function cellValue(td) {
    var order = td.getAttribute('data-order');
    if (order !== null && order !== '') return order.trim();
    var main = td.querySelector('.metricValue, b');
    var text = (main ? main.textContent : td.textContent).replace(/\s+/g, ' ').trim();
    return text.replace(/\s*\(.*\)\s*$/, '');   // retire le z-score "(1.234)"
  }

  function playerInfo(td) {
    var full = td.querySelector('.hideForMobile') || td.querySelector('a');
    var small = td.querySelector('small');
    var m = small ? small.textContent.match(/\(\s*([A-Za-z]+)\s*-\s*([^)]*)\)/) : null;
    return [full ? full.textContent.trim() : '', m ? m[1] : '', m ? m[2].replace(/\s+/g, '') : ''];
  }

  function csvField(v) { v = String(v == null ? '' : v); return /[;"\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }

  var out = [['Player', 'Team', 'Position'].concat(headers.slice(1))];
  rows.forEach(function (tr) {
    var tds = tr.querySelectorAll('td');
    if (tds.length !== headers.length) return;
    var line = playerInfo(tds[0]);
    for (var i = 1; i < tds.length; i++) line.push(cellValue(tds[i]));
    if (line[0]) out.push(line);
  });
  if (out.length < 2) { alert('Aucun joueur lu dans le tableau.'); return; }

  var period = new URLSearchParams(location.search).get('flt_proj_time_period') || 'Preseason';
  var stage = /preseason/i.test(period) ? 'draft' : 'ros';
  var d = new Date();
  var date = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  var name = 'lineupexperts_' + stage + '_' + date + '.csv';

  var csv = '\ufeff' + out.map(function (r) { return r.map(csvField).join(';'); }).join('\r\n');
  window.__stblLineupExpertsCsv = csv;   // utile pour vérifier depuis la console
  var a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  alert((out.length - 1) + ' joueurs exportés dans ' + name + (period !== 'Preseason' ? ' (période : ' + period + ')' : ''));
})();
