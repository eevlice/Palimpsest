// Run: node tests/review_frontend.js
// The review screen's grouped questions, with the real functions pulled out of index.html (no copies): the
// "apply to the others" text of a stretch of pages and of look-alikes across the book; a group opened with
// "Tek tek sor" and closed again; answers given while it is open belong to its parts.
const fs = require('fs'), path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function fn(name) {               // a top-level "function name(...){...}" by brace matching
  const at = src.indexOf('function ' + name + '(');
  if (at < 0) throw new Error('missing function ' + name);
  let i = src.indexOf('{', src.indexOf(')', at)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(at, i + 1);
  }
  throw new Error('unbalanced ' + name);
}
const escLine = src.split('\n').find(l => l.startsWith('const esc='));
let saved = 0;
const CK = {data: null, i: 0, answers: {}, open: new Set(), other: false};
const ckSave = () => { saved++; }, ckShow = () => {};
eval([escLine, fn('ckQs'), fn('ckAllText'), fn('ckGroup')].join('\n') + '\nObject.assign(global,{ckQs,ckAllText,ckGroup});');

let failed = 0;
function check(name, cond, extra) {
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : '  ' + JSON.stringify(extra)));
  if (!cond) failed++;
}

check('a stretch of pages: "Bu bölgedeki N yere de uygula (s. X–Y)"',
      ckAllText({scope: 'bolge', count: 5, pages: '355–370'}) === 'Bu bölgedeki 4 yere de uygula (s. 355–370)');
check('look-alikes across the book keep today\'s text', ckAllText({scope: 'kitap', count: 3, pages: '7, 12'}) === 'Kitapta aynı görünen 2 yere de uygula'
      && ckAllText({count: 3}) === 'Kitapta aynı görünen 2 yere de uygula');

const parts = [{id: 'q2.1', targets: [['a']]}, {id: 'q2.2', targets: [['b']]}];
CK.data = {questions: [{id: 'q1'}, {id: 'q2', scope: 'bolge', parts}, {id: 'q3'}]};
CK.answers = {q2: {key: 'role:gövde', all: true}};
CK.i = 1;
const ids = () => ckQs().map(q => q.id).join(' ');
check('closed: the group is one card', ids() === 'q1 q2 q3', ids());
ckGroup('q2', true);
check('"Tek tek sor": its parts in its place, the first of them shown', ids() === 'q1 q2.1 q2.2 q3' && CK.i === 1 && ckQs()[1].group === 'q2', [ids(), CK.i]);
check('...the group\'s own answer goes, the change is saved', !CK.answers.q2 && saved === 1 && CK.open.has('q2'));
CK.answers['q2.2'] = {key: 'role:alıntı', all: true};
check('opened: a part is answered on its own', ckQs()[2].id === 'q2.2' && CK.answers['q2.2'].key === 'role:alıntı');
CK.i = 2;
ckGroup('q2', false);
check('"Tek soruda topla": one card again, shown in its place; the parts\' answers go', ids() === 'q1 q2 q3' && CK.i === 1
      && !CK.answers['q2.2'] && !CK.open.has('q2') && saved === 2, [ids(), CK.i, CK.answers]);

console.log(failed ? 'FAILED ' + failed : 'ALL PASS');
process.exit(failed ? 1 : 0);
