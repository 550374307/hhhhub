const fs = require('fs');
const s = fs.readFileSync('admin/console/console-app.js', 'utf8');
let ok = true;

const feats = ['openProblemAccounts', 'platformImportPaste',
               'showAllOfficialModels', 'geRealmDupCount'];
for (const f of feats) {
  const hit = s.includes(f);
  if (!hit) ok = false;
  console.log('  ' + (hit ? 'PASS' : 'FAIL') + '  ' + f);
}

const patterns = [
  ['ic prefix strip', /replace\(\/\^fa\[srb\]\?-/, ],
  ['WBIP typeof guard', /typeof WBIP !== .undefined./],
];
for (const [name, re] of patterns) {
  const hit = re.test(s);
  if (!hit) ok = false;
  console.log('  ' + (hit ? 'PASS' : 'FAIL') + '  ' + name);
}

try {
  new Function(s);
  console.log('  PASS  console-app.js syntax');
} catch (e) {
  ok = false;
  console.log('  FAIL  syntax: ' + e.message);
}

console.log('CONSOLE ALL: ' + ok);
process.exit(ok ? 0 : 1);
