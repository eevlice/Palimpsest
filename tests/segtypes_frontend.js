// Run: node tests/segtypes_frontend.js
// Pulls the real functions out of index.html (no copies) and checks them
// against the same fixture the Python tests use.
const fs = require('fs'), path = require('path');
const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'segtypes_fixture.json'), 'utf8'));

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
function block(prefix) {          // a top-level "window.name=(...)=>{...};" by brace matching
  const at = src.indexOf(prefix);
  if (at < 0) throw new Error('missing ' + prefix);
  let i = src.indexOf('{', at), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(at, i + 1) + ';';
  }
  throw new Error('unbalanced ' + prefix);
}
function line(prefix) {           // a one-line top-level statement
  const at = src.indexOf(prefix);
  if (at < 0) throw new Error('missing ' + prefix);
  return src.slice(at, src.indexOf('\n', at));
}
const code = [
  line('const esc='), line('const SEG_H='), line('const SEG_REF='), line('const SEG_IMG='), line('const SEG_CAP='), line('const SEG_TAB='), line('const CAP_OPEN='), line('const SEG_FENCE='), line('const FENCE_TYPE='), line('const SEG_BQ='), line('const FENCE_STYLE_OF='), line('const NAMED='), line("const MASK="), line('const PARSE_CACHE='), line('const WIN='), line('const RAIL_PER_ROW='), line('const STATE_CLS='), line('const PAGE_REF_NOTE='),
  ...['foldCase', 'segType', 'segMarkerLen', 'segTailLen', 'wrapSeg', 'fnNumber', 'typeOf', 'refLabels', 'maskRefs',
      'inlineParse', 'isWordChar', 'findSpans', 'parseTerms', 'glossHitsForText',
      'searchSpans', 'renderSource', 'refMismatch', 'neighborText', 'canMerge', 'exportText', 'exportSeg', 'hasPageRef', 'pageRefFlag', 'docxComment',
      'normalizeProject', 'isDone', 'mergeMeta', 'projectForSave', 'splitRow', 'parseMdTable', 'cellEsc', 'buildMdTable', 'emptyLike', 'tableShapeOf', 'tableMismatch', 'gridFits', 'mdInline', 'tableHTML', 'hasSource', 'srcBtn', 'devFlag', 'firstSegForPage', 'linkifyPages', 'autoTermsFilter', 'headingTitle', 'computeSections', 'sectionStats', 'segState', 'padWidth', 'railLabel',
      'windowStartFor', 'windowLabel', 'goToWindow', 'railGoto', 'pageEarlier', 'pageLater', 'jumpToLastTranslated', 'lastTranslatedIndex',
      'roman', 'headingDepths', 'railModel', 'flowCells', 'railWhere', 'railCellClass', 'railOpen', 'applyRailOpen', 'setRailOpen'].map(fn), block('const ICON='), block('window.mergeDown='), block('window.undoMerge='),
  'return {segType,inlineParse,findSpans,renderSource,refMismatch,neighborText,canMerge,exportText,normalizeProject,' +
  'headingTitle,computeSections,sectionStats,isDone,mergeMeta,projectForSave,parseMdTable,buildMdTable,emptyLike,tableShapeOf,tableMismatch,gridFits,tableHTML,hasSource,srcBtn,devFlag,hasPageRef,pageRefFlag,docxComment,firstSegForPage,linkifyPages,autoTermsFilter,mergeDown:(i)=>window.mergeDown(i),undoMerge:()=>window.undoMerge(),getP:()=>P,segState,railLabel,roman,headingDepths,railModel,flowCells,railWhere,railCellClass,railOpen,applyRailOpen,setRailOpen,RAIL_PER_ROW,' +
  'windowStartFor,windowLabel,goToWindow,railGoto,pageEarlier,pageLater,jumpToLastTranslated,PREFS_:()=>PREFS,LAYOUT,SAVED,' +
  'getWin:()=>winStart,setWin:v=>{winStart=v},calls:CALLS,setMerge:v=>{MERGE_MODE=v},setP:p=>{P=p},setSearch:q=>{SEARCH={query:q}}};'
].join('\n');
// DOM-free stubs for the window / rail navigation functions pulled out of index.html
const STUBS = `let winStart=0, MERGE_MODE=false, GLOSS_FILTER=null, PREFS={}; const CALLS=[], SAVED=[], LAYOUT={closed:false,pressed:null};
const window={innerWidth:1400,scrollTo(){}}; const document={querySelector(sel){ return sel==='.ws-layout'?{classList:{toggle(c,on){ if(c==='rail-closed') LAYOUT.closed=!!on; }}}:null; }}; function savePref(k,v){ SAVED.push([k,v]); PREFS[k]=v; } const $=id=>({scrollIntoView(){CALLS.push('scroll:'+id)},classList:{toggle(){}},setAttribute(k,v){ if(id==='btn-rail'&&k==='aria-pressed') LAYOUT.pressed=v; }});
function renderWindow(){CALLS.push('renderWindow')} function updateProgress(){} function updateUndoBar(){} function queueSave(){CALLS.push('save')} function renderRail(){CALLS.push('renderRail')}
function mergeSelClick(i){CALLS.push('merge:'+i)} function updateRail(){CALLS.push('updateRail')} function clearGlossaryFilter(){GLOSS_FILTER=null;CALLS.push('clearFilter')} function closeRailMobile(){}
`;
const api = new Function('let P=null, SEARCH={query:""};\n' + STUBS + code.replace(/^const esc=/m, 'const esc=') )();

let failed = 0;
const check = (name, cond, extra) => { console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : ' ' + (extra || ''))); if (!cond) failed++; };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// 2. same fixture as Python
fixture.forEach(r => check('segType ' + JSON.stringify(r.text), eq(api.segType(r.text), r.type), JSON.stringify(api.segType(r.text))));

// 3. old project: unmarked paragraphs -> all para, no fields added
const old = { id: 'p_1', name: 'Eski', source_lang: 'English', target_lang: 'Turkish', model: 'x',
  segments: ['Bir paragraf.', 'İki.', 'Üç, "alıntı" ve *italik*.'], finals: ['Bir.', null, null],
  approved: [true, false, false], synopsis: '', terms: '', rules: '', style: '', cost: 0, segCost: {}, segEffort: {}, _undo: null };
const before = JSON.stringify(old);
const normalized = api.normalizeProject(JSON.parse(before));
const added = Object.keys(normalized).filter(k => !(k in JSON.parse(before)));
const { segMeta: _sm, ...rest } = normalized;
check('old project: normalizeProject adds only segMeta (all null) and changes nothing else',
  added.join() === 'segMeta' && _sm.every(x => x === null) && JSON.stringify(rest) === before, added);
check('old project: every segment is a paragraph', old.segments.every(s => api.segType(s).type === 'para'));

// helpers on a typed project
const P = { segments: ['# Birinci Bölüm', 'Internet policy is here[^b1-1] now.', '[^b1-1]: Internet note.', 'Next paragraph.', '## Alt', 'Last[^b1-2].', '[^b1-2]: Son.'],
  finals: [null, null, null, null, null, null, null], approved: [false, false, false, false, false, false, false],
  terms: 'internet policy = internet politikası\nIP =\nb1 =\nBölüm =', segCost: {} };
api.setP(P);

// merge guard
check('canMerge: heading+para blocked', api.canMerge(0) === false);
check('canMerge: para+footnote blocked', api.canMerge(1) === false);
check('canMerge: footnote+para blocked', api.canMerge(2) === false);
check('canMerge: last segment has no next', api.canMerge(6) === false);
check('canMerge: two plain paragraphs allowed', (() => { const q = { segments: ['A.', 'B.'], finals: [null, null] }; api.setP(q); const r = api.canMerge(0); api.setP(P); return r; })());

// neighbours skip footnotes
check('neighborText after para skips footnote', api.neighborText(1, 1) === 'Next paragraph.');
check('neighborText before para after footnote skips it', api.neighborText(3, -1) === 'Internet policy is here[^b1-1] now.');
check('neighborText at edges is empty', api.neighborText(0, -1) === '' && api.neighborText(6, 1) === '');

// visible text: marker and **/* delimiters gone, refs are barriers, map points back to raw
['# Birinci Bölüm', 'Internet policy is here[^b1-1] now.', '[^b1-1]: Internet note.', 'The **Internet** *policy*.'].forEach(s => {
  const c = api.inlineParse(s);
  check('inlineParse map is raw-indexed and monotonic: ' + s, c.vis.length === c.map.length && c.map.every((k, j) => k >= c.off && k < s.length && (j === 0 || k > c.map[j - 1])));
});
check('visible text drops marker', api.inlineParse('## Alt başlık').vis === 'Alt başlık');
check('visible text drops delimiters', api.inlineParse('a **b** *c* d').vis === 'a b c d');
check('refs are barriers in visible text', api.inlineParse('x[^a-1]y').vis === 'x\u0001\u0001\u0001\u0001\u0001\u0001y');
check('stray asterisks are text', api.inlineParse('5 * 3 * 2').vis === '5 * 3 * 2');
// named blocks: markers and a verse's hard breaks are not visible; export puts a marker-less translation back in
const V = '::: {custom-style="Verse"}\n*One* line\\\nTwo line\n:::';
check('verse: visible text keeps the lines, drops fence and backslashes', api.inlineParse(V).vis === 'One line\nTwo line', JSON.stringify(api.inlineParse(V).vis));
check('blockquote: visible text drops "> "', api.inlineParse('> Bir alıntı').vis === 'Bir alıntı');
check('export re-wraps a verse translation with hard breaks', api.exportText([V], ['Bir dize\nİki dize']) === '::: {custom-style="Verse"}\nBir dize\\\nİki dize\n:::', JSON.stringify(api.exportText([V], ['Bir dize\nİki dize'])));
check('export re-wraps epigraph, attribution, box and quote', api.exportText(['::: {custom-style="Epigraph"}\nA\n:::', '::: {custom-style="Attribution"}\n—B\n:::', '::: {custom-style="Box"}\nC\n:::', '> D'], ['a', '—b', 'c', 'd'])
  === '::: {custom-style="Epigraph"}\na\n:::\n\n::: {custom-style="Attribution"}\n—b\n:::\n\n::: {custom-style="Box"}\nc\n:::\n\n> d');
check('untranslated named block keeps its fence', api.exportText(['::: {custom-style="Epigraph"}\nA\n:::'], [null]) === '::: {custom-style="Epigraph"}\n[UNTRANSLATED] A\n:::');
check('unclosed ** is text', api.inlineParse('**oops and *oops').vis === '**oops and *oops');
check('nested italic inside bold', api.inlineParse('**a *b* c**').vis === 'a b c');
check('glossary: "b1" does not match inside [^b1-1]', api.findSpans(P.segments[1], 'b1').length === 0);
check('glossary: "b1" does not match footnote label prefix', api.findSpans(P.segments[2], 'b1').length === 0);
check('glossary: term across bold edge, raw indices cover delimiters', (() => { const t = 'the **Internet** policy is'; const s = api.findSpans(t, 'internet policy'); return s.length === 1 && t.slice(s[0].start, s[0].end) === 'Internet** policy'; })());
check('glossary: term inside italic', (() => { const t = 'a *Internet policy* b'; const s = api.findSpans(t, 'Internet policy'); return s.length === 1 && t.slice(s[0].start, s[0].end) === 'Internet policy'; })());
check('glossary: term inside heading with bold', (() => { const t = '## The **Internet** Policy'; const s = api.findSpans(t, 'internet policy'); return s.length === 1 && t.slice(s[0].start, s[0].end) === 'Internet** Policy'; })());
check('glossary: "**" and "*" never match', api.findSpans('a **b** *c*', '**').length === 0 && api.findSpans('a **b** *c*', '*').length === 0);
check('glossary: "internet policy" found in paragraph with ref', (() => { const s = api.findSpans(P.segments[1], 'internet policy'); return s.length === 1 && P.segments[1].slice(s[0].start, s[0].end) === 'Internet policy'; })());
check('glossary: IP not inside "skipped"', api.findSpans('He skipped[^a-1] it', 'IP').length === 0);
check('glossary: term after a ref keeps correct indices', (() => { const t = 'See[^q-1] Internet policy now'; const s = api.findSpans(t, 'internet policy'); return s.length === 1 && t.slice(s[0].start, s[0].end) === 'Internet policy'; })());
check('glossary: heading text matches, marker does not', api.findSpans(P.segments[0], 'Bölüm').length === 1 && api.findSpans(P.segments[0], '#').length === 0);

// rendering
const h0 = api.renderSource(0);
check('heading render: no marker shown', !h0.includes('#') && h0.includes('Birinci'), h0);
const h1 = api.renderSource(1);
check('para render: ref is a small superscript number', h1.includes('<sup class="fn-ref">1</sup>') && !h1.includes('[^'), h1);
check('para render: glossary mark wraps whole term', h1.includes('>Internet policy</mark>'), h1);
const h2 = api.renderSource(2);
check('footnote render: label prefix not shown', !h2.includes('[^') && h2.startsWith('Internet note'), h2);
api.setSearch('b1');
check('search "b1": no highlight inside a ref', !api.renderSource(1).includes('search-mark'));
api.setSearch('now');
const hs = api.renderSource(1);
check('search "now": highlighted, ref sup intact', hs.includes('<mark class="search-mark">now</mark>') && hs.includes('<sup class="fn-ref">1</sup>'), hs);
api.setSearch('internet');
check('search is case-folded (Internet <-> internet)', api.renderSource(1).includes('<mark class="search-mark">Internet</mark>'));
api.setSearch('sup');
check('search "sup": does not corrupt generated markup', !api.renderSource(1).includes('<mark class="search-mark">sup'));
api.setSearch('');

// emphasis rendering
P.segments.push('The **bold Internet** and *italic* words[^b1-1], **both *nested* here**.');
P.finals.push(null); P.approved.push(false);
const he = api.renderSource(P.segments.length - 1);
check('emphasis: no asterisks in source render', !he.includes('*'), he);
check('emphasis: bold, italic, nested tags', he.includes('<strong>bold Internet</strong>') && he.includes('<em>italic</em>') && he.includes('<strong><em>nested</em></strong>') && he.replace(/<[^>]+>/g, '') === 'The bold Internet and italic words1, both nested here.', he);
check('emphasis: ref still a sup', he.includes('<sup class="fn-ref">1</sup>'), he);
check('emphasis: tags balanced', (he.match(/<strong>/g) || []).length === (he.match(/<\/strong>/g) || []).length && (he.match(/<em>/g) || []).length === (he.match(/<\/em>/g) || []).length && (he.match(/<mark/g) || []).length === (he.match(/<\/mark>/g) || []).length, he);
P.terms += '\nbold internet =\nitalic words =';
const hg = api.renderSource(P.segments.length - 1);
check('emphasis + glossary: whole bold term marked, balanced', /<mark class="gloss-hit"[^>]*><strong>bold Internet<\/strong><\/mark>/.test(hg) && (hg.match(/<mark/g) || []).length === (hg.match(/<\/mark>/g) || []).length, hg);
check('emphasis + glossary: term across bold/italic edge becomes one mark per run', (() => { const q = { segments: ['See **bold** words here'], finals: [null], terms: 'bold words =' }; api.setP(q); const r = api.renderSource(0); api.setP(P); return (r.match(/class="gloss-hit"/g) || []).length === 2 && !r.includes('*'); })());
api.setSearch('**');
check('search "**": nothing highlighted', !api.renderSource(P.segments.length - 1).includes('search-mark'));
api.setSearch('bold internet');
check('search across bold content works', api.renderSource(P.segments.length - 1).includes('search-mark'));
api.setSearch('');
P.segments.pop(); P.finals.pop(); P.approved.pop(); P.terms = P.terms.replace('\nbold internet =\nitalic words =', '');

// row warning
P.finals[1] = 'İnternet politikası burada[^b1-1] şimdi.';
check('refMismatch: same refs -> none', api.refMismatch(1) === '');
P.finals[1] = 'İnternet politikası burada şimdi.';
check('refMismatch: missing ref -> warning', api.refMismatch(1).startsWith('Referanslar uyuşmuyor'), api.refMismatch(1));
P.finals[1] = 'Burada[^b1-1][^b1-9].';
check('refMismatch: extra ref -> warning', api.refMismatch(1).includes('kaynak: 1'), api.refMismatch(1));
P.finals[1] = 'Burada⟦1⟧.';
check('refMismatch: leftover placeholder -> warning', api.refMismatch(1) !== '');
P.finals[1] = '';
check('refMismatch: empty box -> none', api.refMismatch(1) === '');
P.finals[1] = null;

// export
P.finals = ['Chapter One', 'Turkish para[^b1-1].', 'Turkish note.', null, '## Zaten işaretli', 'Son[^b1-2].', null];
const ex = api.exportText(P.segments, P.finals).split('\n\n');
check('export: heading marker restored', ex[0] === '# Chapter One', ex[0]);
check('export: paragraph unchanged', ex[1] === 'Turkish para[^b1-1].');
check('export: footnote label restored', ex[2] === '[^b1-1]: Turkish note.', ex[2]);
check('export: untranslated paragraph unchanged', ex[3] === '[UNTRANSLATED] Next paragraph.');
check('export: final that already has its marker is not doubled', ex[4] === '## Zaten işaretli', ex[4]);
check('export: untranslated footnote: label first, tag after', ex[6] === '[^b1-2]: [UNTRANSLATED] Son.', ex[6]);
const ex2 = api.exportText(['## Last heading', 'P.', '[^b1-2]: Second note.', '#\tTab'], [null, null, null, null]).split('\n\n');
check('export: untranslated heading: marker first, tag after', ex2[0] === '## [UNTRANSLATED] Last heading', ex2[0]);
check('export: untranslated paragraph keeps old format', ex2[1] === '[UNTRANSLATED] P.', ex2[1]);
check('export: untranslated footnote (second case)', ex2[2] === '[^b1-2]: [UNTRANSLATED] Second note.', ex2[2]);
check('export: original marker spelling preserved', ex2[3] === '#\t[UNTRANSLATED] Tab', JSON.stringify(ex2[3]));


// ---------- sections, parts, counts, active section, rail HTML ----------
const secOfText = (segs, name) => { const r = api.computeSections(segs, name); return r.secs.map(x => [x.num, x.title, x.start, x.end, x.lead]); };
// headless (every old project): one section, the project name, nothing lost
{
  const segs = ['Bir.', 'İki.', 'Üç[^a-1].', '[^a-1]: Not.'];
  const r = api.computeSections(segs, 'Eski Kitap');
  check('sections: headless -> one lead section named after the project', r.secs.length === 1 && r.secs[0].lead && r.secs[0].num === '—' && r.secs[0].title === 'Eski Kitap' && r.secs[0].start === 0 && r.secs[0].end === 4);
  check('sections: headless secOf all 0', Array.from(r.secOf).every(v => v === 0));
}
check('sections: empty project does not crash', (() => { const r = api.computeSections([], 'x'); return r.secs.length === 1 && r.secs[0].end === 0; })());
// lead-in before the first heading
check('sections: segments before the first heading -> "—" lead-in', eq(secOfText(['a', 'b', '# A', 'c', '# B', 'd'], 'N'), [['—', '—', 0, 2, true], ['1', 'A', 2, 4, false], ['2', 'B', 4, 6, false]]));
check('sections: first segment is the heading -> no lead-in', eq(secOfText(['# A', 'c', '# B', 'd'], 'N'), [['1', 'A', 0, 2, false], ['2', 'B', 2, 4, false]]));
// H1 + H2 nested
{
  const segs = ['# T1', 'a', '## S1', 'b', 'c', '## S2', 'd', '# T2', 'e'];
  const r = api.computeSections(segs, 'N');
  check('sections: H1 starts sections, H2 stays inside', eq(r.secs.map(x => [x.num, x.title, x.start, x.end]), [['1', 'T1', 0, 7], ['2', 'T2', 7, 9]]));
  check('sections: sizes add up to n', r.secs.reduce((a, x) => a + x.end - x.start, 0) === segs.length);
}
// only H2s
check('sections: project with only H2 -> H2 is the top level', eq(secOfText(['## A', 'x', '## B', 'y'], 'N'), [['1', 'A', 0, 2, false], ['2', 'B', 2, 4, false]]));
// H2 before the first H1 stays in the lead-in and still gets its own part
{
  const segs = ['## pre', 'x', '# A', 'y'];
  const r = api.computeSections(segs, 'N');
  check('sections: H2 before first H1 lives in the lead-in', eq(r.secs.map(x => [x.num, x.start, x.end]), [['—', 0, 2], ['1', 2, 4]]));
}
check('sections: heading titles lose marker and emphasis', api.computeSections(['# The **Big** *Start*', 'x'], 'N').secs[0].title === 'The Big Start');
check('sections: heading with a footnote ref keeps the title clean', api.headingTitle('# Title[^a-1]') === 'Title');

// counts
{
  const q = { segments: ['# A', 'p1', '[^a-1]: n', 'p2', '# B', 'p3'], finals: ['T', 'x', null, 'y', null, null], approved: [true, true, false, false, false, false], name: 'N' };
  api.setP(q);
  const r = api.computeSections(q.segments, 'N');
  const st = api.sectionStats(r.secs, q.finals, q.approved);
  check('stats (strip): approved/translated/total include headings and footnotes', eq(st, [{ appr: 2, trans: 3, total: 4 }, { appr: 0, trans: 0, total: 2 }]), JSON.stringify(st));
  check('segState: approved > translated > pending', api.segState(1) === 's-a' && api.segState(3) === 's-t' && api.segState(2) === 's-p' && api.segState(5) === 's-p');
  q.finals[5] = ''; check('segState: empty translation box is still pending', api.segState(5) === 's-p'); q.finals[5] = null;
}
// ---------- window start, cell/row clicks, paging, label ----------
{
  const q = { segments: Array.from({ length: 1810 }, (_, i) => (i % 100 === 0 ? '# H' + i : 'p' + i)), finals: [], approved: [], name: 'N' };
  q.finals = q.segments.map(() => null); q.approved = q.segments.map(() => false);
  api.setP(q);
  check('windowStartFor: the target itself, clamped to the book', api.windowStartFor(412, 1810) === 412 && api.windowStartFor(-3, 1810) === 0 && api.windowStartFor(5000, 1810) === 1809);
  check('windowLabel: first row is the target', api.windowLabel(412, 1810) === '¶ 413–422 of 1810');
  check('windowLabel: short last window', api.windowLabel(1805, 1810) === '¶ 1806–1810 of 1810');
  api.calls.length = 0; api.railGoto(412);
  check('clicking a cell/row puts that segment first in the window (no centring, no 10-block grid)', api.getWin() === 412, api.getWin());
  check('click renders window and rail and scrolls to the clicked segment', api.calls.includes('renderWindow') && api.calls.includes('renderRail') && api.calls.includes('scroll:seg-412'), api.calls);
  api.railGoto(415);
  check('a click inside the current window still restarts the window there', api.getWin() === 415, api.getWin());
  api.pageLater();
  check('NEXT moves exactly +10 from the new start', api.getWin() === 425 && api.windowLabel(api.getWin(), 1810) === '¶ 426–435 of 1810');
  api.pageEarlier(); api.pageEarlier();
  check('PREVIOUS moves exactly -10', api.getWin() === 405);
  api.railGoto(0); api.pageEarlier();
  check('PREVIOUS stops at the first segment', api.getWin() === 0);
  api.railGoto(1807);
  check('a click near the end gives a short window starting at the target', api.getWin() === 1807 && api.windowLabel(1807, 1810) === '¶ 1808–1810 of 1810');
  q.finals[300] = 'x'; api.jumpToLastTranslated();
  check('resume ("kaldığın yere dön") starts the window at the last translated segment', api.getWin() === 300, api.getWin());
  api.setMerge(true); api.calls.length = 0; const w0 = api.getWin(); api.railGoto(50);
  check('in merge mode a click selects instead of moving the window', api.calls.includes('merge:50') && api.getWin() === w0);
  api.setMerge(false);
  api.goToWindow(900);
  check('goToWindow (search results, merge) also starts at the target', api.getWin() === 900);
}


// ---------- rail 4A: whole-book flow, table of contents, where-am-I, open/close ----------
{
  const segs = ['# Part One Beginnings', '# Chapter A', 'intro para', '## Sub One', 'p', '### Deep', 'p', '[^a-1]: note', '# Chapter B', 'p2', '## Sub Two', 'p3'];
  const finals = [null, 'x', null, 'x', 'x', null, null, null, null, null, null, null];
  const approved = [false, true, false, true, false, false, false, false, false, false, false, false];
  const m = api.railModel(segs, finals, approved);
  const cells = api.flowCells(m);
  check('flow: one cell per segment, in book order', cells.length === segs.length && cells.every((c, i) => c.idx === i));
  check('flow: kinds (heading / paragraph / footnote)', cells.map(c => c.kind).join() === 'hd,hd,p,hd,p,hd,p,fn,hd,p,hd,p', cells.map(c => c.kind).join());
  check('flow: heading shape level = number of #', cells.filter(c => c.kind === 'hd').map(c => c.num).join() === '1,1,2,3,1,2', cells.filter(c => c.kind === 'hd').map(c => c.num).join());
  check('flow: no row breaks, a heading only differs by its clipped corner', cells.every(c => !('top' in c)) && api.railCellClass(cells[0], false, false) === 'mx s-p h1' && api.railCellClass(cells[1], false, false) === 'mx s-a h1' && api.railCellClass(cells[3], false, false) === 'mx s-a h2');
  check('flow: 10 squares per row by default', api.RAIL_PER_ROW === 10);
  check('flow: non-headings carry no number', cells.filter(c => c.kind !== 'hd').every(c => c.num === 0));
  check('states: approved / translated / waiting', m.state[1] === 2 && m.state[3] === 2 && m.state[4] === 1 && m.state[0] === 0 && m.state[2] === 0);
  check('cell class: approved ink, translated mid, waiting pale', api.railCellClass(cells[1], false, false).startsWith('mx s-a') && api.railCellClass(cells[4], false, false) === 'mx s-t' && api.railCellClass(cells[2], false, false) === 'mx s-p');
  check('cell class: footnote is a circle in its state colour', api.railCellClass(cells[7], false, false) === 'mx fn s-p');
  const cls6 = cells.map(c => api.railCellClass(c, c.idx === 6, false));
  check('exactly one "you are here" cell, and nothing else marks the window', cls6.filter(x => / here/.test(x)).length === 1 && / here/.test(cls6[6]) && !cls6.some(x => /win|focus|ring/.test(x)));
  check('merge selection is its own marker', api.railCellClass(cells[2], false, true).endsWith(' msel'));
  check('heading model: the headings in book order with their depth', m.heads.map(h => h.idx).join() === '0,1,3,5,8,10' && m.heads.map(h => h.depth).join() === '1,2,3,4,2,3');
  check('heading titles are plain text', m.heads[0].title === 'Part One Beginnings');
  // depth rules
  const d1 = api.headingDepths(['# A {.bolum}', '# B', '## C'], ['# A {.bolum}', '# B', '## C'].map(api.segType));
  check('{.bolum} marks a chapter; then a plain # is a part', Array.from(d1).join() === '2,1,3', Array.from(d1).join());
  const d2 = api.headingDepths(['# Foo', '## Bar', '### Baz'], ['# Foo', '## Bar', '### Baz'].map(api.segType));
  check('without any marker a plain # is a chapter', Array.from(d2).join() === '2,3,4', Array.from(d2).join());
  check('"Part…"/"Kısım…" titles at # level are parts', Array.from(api.headingDepths(['# Kısım Bir', '# Bölüm'], ['# Kısım Bir', '# Bölüm'].map(api.segType))).join() === '1,2');
  check('headingTitle drops the {.bolum} attribute', api.headingTitle('# Başlık {.bolum}') === 'Başlık');
  check('roman numerals for the part line', [1, 4, 9, 14].map(api.roman).join() === 'I,IV,IX,XIV');
  // where am I
  const w6 = api.railWhere(m, 6, 'Book');
  check('where: chapter number counts from the start of the book', w6.chap.n === 1 && api.railWhere(m, 10, 'B').chap.n === 2);
  check('where: part, chapter and nearest sub-heading', w6.part.n === 1 && w6.part.title === 'Part One Beginnings' && w6.chap.title === 'Chapter A' && w6.sub.title === 'Deep', JSON.stringify(w6));
  const w10 = api.railWhere(m, 10, 'Book');
  check('where: a new chapter resets the sub-heading, the part stays', w10.chap.title === 'Chapter B' && w10.sub.title === 'Sub Two' && w10.part.n === 1);
  const w9 = api.railWhere(m, 9, 'Book');
  check('where: no sub-heading yet in this chapter -> none', w9.chap.title === 'Chapter B' && w9.sub === null);
  const w0 = api.railWhere(m, 0, 'Book');
  check('where: on the part heading itself, no chapter yet', w0.part.n === 1 && w0.chap === null);
  check('where: before every heading nothing is known', api.railWhere(api.railModel(['text', '# H'], [null, null], [false, false]), 0, 'B').part === null);
  // click -> window, merge mode
  const q = { segments: segs.slice(), finals: finals.slice(), approved: approved.slice(), name: 'N' };
  api.setP(q); api.railGoto(8);
  check('clicking any square makes that segment the first row of the window', api.getWin() === 8);
  // open / close
  check('rail is open by default', api.railOpen() === true);
  api.setRailOpen(false);
  check('closing: pref saved, column hidden, toolbar button not pressed', api.railOpen() === false && api.SAVED.some(x => x[0] === 'rail_open' && x[1] === false) && api.LAYOUT.closed === true && api.LAYOUT.pressed === 'false');
  api.calls.length = 0; api.setRailOpen(true);
  check('opening: pref saved, column back, toolbar button pressed, rail redrawn', api.railOpen() === true && api.SAVED.some(x => x[0] === 'rail_open' && x[1] === true) && api.LAYOUT.closed === false && api.LAYOUT.pressed === 'true' && api.calls.includes('updateRail'));
}

// ---------- figure placeholders, captions, tables (import round 2) ----------
{
  const img = '![Görsel · s.94](gorsel-94-1)';
  const cap = '::: {custom-style="Caption"}\n*Fig. 5:* A cat\n:::';
  const tab = '| a | b |\n|---|---|\n| 1 | 2 |';
  check('caption: the fences are not visible text', api.inlineParse(cap).vis === 'Fig. 5: A cat', api.inlineParse(cap).vis);
  check('caption: the closing fence is not rendered', !/:::/.test(api.renderSource ? (api.setP({ segments: [cap], finals: [null], approved: [false] }), api.renderSource(0)) : ''));
  check('export: a placeholder goes out as it is, translated or not', api.exportText([img], [null]) === img && api.exportText([img], ['x']) === img);
  check('export: a caption translation goes back into its fence', api.exportText([cap], ['*Şek. 5:* Bir kedi']) === '::: {custom-style="Caption"}\n*Şek. 5:* Bir kedi\n:::');
  check('export: an untranslated table stays a table', api.exportText([tab], [null]) === tab);
  // in-book page references: a gutter mark and, in the Word export, a Pandoc comment span around the text only
  api.setP({segments: ['See p. 42.', 'Plain.'], segMeta: [{pages: [3], flags: ['sayfa-gonderme'], page_refs: ['s.42']}, {pages: [3], flags: []}]});
  check('page reference: a gutter mark with the note, none elsewhere', api.pageRefFlag(0).includes('Sayfa göndermesi: Türkçe baskıda sayfa numarası değişecek.') && api.pageRefFlag(1) === '');
  const cm = api.docxComment('::: {custom-style="Epigraph"}\nBkz. s. 42.\n:::', 7);
  check('page reference: the comment spans the text inside the fence', cm === '::: {custom-style="Epigraph"}\n[Sayfa göndermesi: Türkçe baskıda sayfa numarası değişecek.]{.comment-start id="7" author="Palimpsest"}Bkz. s. 42.[]{.comment-end id="7"}\n:::', cm);
  check('page reference: a note definition keeps its label outside the comment', api.docxComment('[^b1-2]: Bkz. s. 4.', 1).startsWith('[^b1-2]: [Sayfa'));
  const p = api.normalizeProject({ segments: ['Text.', img], finals: [null, null], approved: [false, false] });
  check('normalize leaves a placeholder alone (nothing written into the project)', p.finals[1] === null && p.approved[1] === false);
  const m = api.railModel(['Text.', img], [null, null], [false, false]);
  check('rail: a placeholder counts as done without being approved', m.state[1] === 2 && m.state[0] === 0);
  const secs = [{ start: 0, end: 2 }];
  const ss = api.sectionStats(secs, [null, null], [false, false], ['Text.', img]);
  check('strip: a placeholder counts as approved and translated', ss[0].appr === 1 && ss[0].trans === 1 && ss[0].total === 2);
  const cells = api.flowCells(m);
  check('rail: a figure gets its own (hollow) cell', cells[1].kind === 'img' && api.railCellClass(cells[1], false, false).startsWith('mx img '));
  api.setP({ segments: ['Before.', img, 'After.'], finals: [null, img, null], approved: [false, true, false] });
  check('model context skips placeholders', api.neighborText(0, 1) === 'After.' && api.neighborText(2, -1) === 'Before.');
  check('no merge across a figure or a caption', !api.canMerge(1) && !api.canMerge(0));
}

// ---------- segMeta: the PDF source of each segment, a parallel array ----------
{
  const m = (pages, flags) => ({ pages, boxes: pages.map(p => [p, 1, 2, 3, 4]), style: 'Body 11', flags: flags || [] });
  const old = api.normalizeProject({ segments: ['a', 'b'], finals: [null, null], approved: [false, false] });
  check('segMeta: missing -> nulls of the right length (older and non-PDF projects)', Array.isArray(old.segMeta) && old.segMeta.length === 2 && old.segMeta.every(x => x === null));
  const keep = api.normalizeProject({ segments: ['a'], finals: [null], approved: [false], segMeta: [m([17])] });
  check('segMeta: kept when its length matches', keep.segMeta[0].pages[0] === 17);
  const bad = api.normalizeProject({ segments: ['a', 'b'], finals: [null, null], approved: [false, false], segMeta: [m([1])] });
  check('segMeta: a wrong length is replaced by nulls, never left out of step', bad.segMeta.length === 2 && bad.segMeta[0] === null);
  const mm = api.mergeMeta(m([18], ['stil-farkli']), m([17, 18]));
  check('mergeMeta: pages and boxes together, flags kept', mm.pages.join() === '17,18' && mm.boxes.length === 3 && mm.flags.join() === 'stil-farkli' && mm.style === 'Body 11');
  check('mergeMeta: null + meta keeps the meta; null + null stays null', api.mergeMeta(null, m([5])).pages.join() === '5' && api.mergeMeta(null, null) === null);
  const q = api.normalizeProject({ segments: ['First part of a', 'paragraph that goes on.', 'Next one.'], finals: [null, null, null],
    approved: [false, false, false], segMeta: [m([17]), m([18]), m([19])] });
  api.setP(q); api.mergeDown(0);
  const P2 = api.getP();
  const lens = [P2.segments.length, P2.finals.length, P2.approved.length, P2.segMeta.length];
  check('merge: every parallel array, segMeta included, keeps the same length', lens.every(x => x === 2), lens);
  check('merge: the merged segment\'s meta covers both sources', P2.segMeta[0].pages.join() === '17,18' && P2.segMeta[1].pages.join() === '19');
  api.undoMerge();
  const P3 = api.getP();
  check('undo (split back): segMeta returns to one entry per segment', P3.segMeta.length === 3 && P3.segMeta[1].pages.join() === '18' && P3.segments.length === 3);
}

// ---------- what reaches the project file ----------
{
  const plain = api.normalizeProject({ id: 'p', segments: ['a', 'b'], finals: [null, null], approved: [false, false] });
  const out = JSON.parse(JSON.stringify(api.projectForSave(plain)));
  check('save: an all-null segMeta (non-PDF, older projects) is left out of the file', !('segMeta' in out) && Array.isArray(plain.segMeta));
  const pdf = api.normalizeProject({ id: 'p', segments: ['a', 'b'], finals: [null, null], approved: [false, false], segMeta: [{ pages: [3], boxes: [], style: 'X 1', flags: [] }, null] });
  check('save: a PDF project keeps its segMeta', JSON.parse(JSON.stringify(api.projectForSave(pdf))).segMeta.length === 2);
  check('save: the in-memory project is not changed', Array.isArray(plain.segMeta) && plain.segMeta.length === 2);
  // an undo snapshot saved without segMeta (it was all null) still splits back to matching lengths
  const q = api.normalizeProject({ segments: ['First part of a', 'paragraph that goes on.', 'Next one.'], finals: [null, null, null], approved: [false, false, false] });
  api.setP(q); api.mergeDown(0);
  const P2 = api.getP(); delete P2._undo.segMeta;
  api.undoMerge();
  const P3 = api.getP();
  check('undo from a snapshot without segMeta: segMeta rebuilt with the right length', P3.segMeta.length === P3.segments.length && P3.segments.length === 3);
}

// ---------- round B: tables, source preview, warnings, glossary filter ----------
{
  const src = '| Name | Note |\n|---|---|\n| a \\| b | *one* |\n| c |  |';
  const t = api.parseMdTable(src);
  check('table: header and rows parsed, an escaped pipe stays inside its cell', t.head.join('/') === 'Name/Note' && t.rows[0][0] === 'a | b' && t.rows[1][1] === '', t);
  const back = api.buildMdTable(t);
  check('table: written back as markdown, the pipe escaped again', back === '| Name | Note |\n|---|---|\n| a \\| b | *one* |\n| c |  |', back);
  check('table: a typed pipe or line break in a cell cannot break the grid', api.buildMdTable({ head: ['x|y'], rows: [['line\nbreak']] }) === '| x\\|y |\n|---|\n| line break |');
  check('table: an empty grid has the same shape', JSON.stringify(api.tableShapeOf(api.buildMdTable(api.emptyLike(t)))) === JSON.stringify(api.tableShapeOf(src)));
  api.setP({ segments: [src], finals: ['| A | B |\n|---|---|'], approved: [false] });
  check('table: a lost row is the usual structure warning', /satır/.test(api.tableMismatch(0)) && !api.gridFits(0));
  api.setP({ segments: [src], finals: [api.buildMdTable({ head: ['Ad', 'Not'], rows: [['a | b', 'bir'], ['c', '']] })], approved: [false] });
  check('table: same shape -> no warning, the grid is used', api.tableMismatch(0) === '' && api.gridFits(0));
  check('table: drawn as a real HTML table', /<table class="mdt"><thead><tr><th>Name<\/th>/.test(api.tableHTML(src)) && /<em>one<\/em>/.test(api.tableHTML(src)));
  // source preview availability
  const meta = { pages: [17, 18], boxes: [[17, 1, 2, 3, 4]], style: 'X 9', flags: ['stil-farkli'] };
  api.setP({ segments: ['a', 'b'], finals: [null, null], approved: [false, false], segMeta: [meta, null] });
  check('source button only where segMeta exists', /src-btn/.test(api.srcBtn(0)) && api.srcBtn(1) === '' && api.hasSource(0) && !api.hasSource(1));
  check('style flag mark with the style in its title', /Gövde stilinden farklı: X 9/.test(api.devFlag(0)) && api.devFlag(1) === '');
  api.setP({ segments: ['a'], finals: [null], approved: [false] });
  check('no segMeta at all (non-PDF project): no button, no mark', api.srcBtn(0) === '' && api.devFlag(0) === '');
  // warnings: page numbers become links to the first segment from that page
  const segMeta = [{ pages: [3] }, { pages: [5, 6] }, null, { pages: [9] }];
  check('first segment of a page (or the next page that has one)', api.firstSegForPage(segMeta, 6) === 1 && api.firstSegForPage(segMeta, 7) === 3 && api.firstSegForPage(segMeta, 20) === -1);
  const html = api.linkifyPages('İki sütunlu görünen sayfalar: 5, 23 (önce sol). Dizin (s. 393–401) dahil edilmedi. 181 paragraf birleştirildi.');
  check('page numbers in warnings are links; counts are not', (html.match(/pg-link/g) || []).length === 3 && /reviewJump\(393\)/.test(html) && !/reviewJump\(181\)/.test(html), html);
  // glossary: entries under 5 occurrences stay out of the automatic list
  const segs = ['The koan again.', 'A koan, the koan.', 'Koan here and koan there.', 'Rare word once.'];
  const f = api.autoTermsFilter('koan = koan\nrare word = nadir\nnot a term line', segs, 5);
  check('auto glossary: kept when the source occurs 5+ times (same matcher as the text marks)', f.kept === 'koan = koan\nnot a term line' && f.dropped.join() === 'rare word', f);
}

console.log(failed ? 'FAILED ' + failed : 'ALL PASS');
process.exit(failed ? 1 : 0);
