'use strict';
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const {chromium} = require('playwright-core');
// Optional browser regression check. See README for setup. No game inputs or RPCs.
const root = path.resolve(__dirname, '..');
assert(process.env.PS2DECOMP_BROWSER, 'Set PS2DECOMP_BROWSER to a local Chrome/Chromium executable.');
const templates = require(root + '/webview_html'), l10n = require(root + '/localization');
const out = path.join(root, 'build', 'webview-preview'); fs.mkdirSync(out, {recursive: true});
const dark = {'editor-background':'#1e1e1e','sideBar-background':'#252526','foreground':'#cccccc','descriptionForeground':'#a8a8a8','panel-border':'#454545','textLink-foreground':'#4daafc','button-secondaryBackground':'#3a3d41','button-secondaryForeground':'#ffffff','button-secondaryHoverBackground':'#45494e','button-background':'#0078d4','button-foreground':'#ffffff','button-hoverBackground':'#026ec1','input-background':'#313131','input-foreground':'#cccccc','input-border':'#555555','focusBorder':'#007fd4','list-hoverBackground':'#2a2d2e','list-inactiveSelectionBackground':'#37373d','progressBar-background':'#0e70c0','testing-iconPassed':'#89d185','editorWarning-foreground':'#cca700','errorForeground':'#f48771','charts-purple':'#b180d7','inputValidation-warningBackground':'#352a05','inputValidation-errorBackground':'#5a1d1d','inputValidation-errorBorder':'#be1100','diffEditor-removedTextBackground':'#ff000033'};
const light = {...dark,'editor-background':'#ffffff','sideBar-background':'#f7f7f7','foreground':'#333333','descriptionForeground':'#616161','panel-border':'#dedede','textLink-foreground':'#006ab1','button-secondaryBackground':'#e5e5e5','button-secondaryForeground':'#333333','button-secondaryHoverBackground':'#d7d7d7','input-background':'#ffffff','input-foreground':'#333333','input-border':'#bdbdbd','list-hoverBackground':'#f0f0f0','list-inactiveSelectionBackground':'#e4e6f1','testing-iconPassed':'#287a32','editorWarning-foreground':'#8a6900','errorForeground':'#a1260d','inputValidation-warningBackground':'#fff4ce'};
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/theme.css') {res.setHeader('Content-Type','text/css'); res.end(':root {' + Object.entries(url.searchParams.get('theme') === 'light' ? light : dark).map(([key,value]) => `--vscode-${key}:${value};`).join('') + '--vscode-font-family: "Segoe UI", sans-serif; --vscode-editor-font-family: Consolas, monospace;}'); return;}
  if (url.pathname.startsWith('/media/')) {const file = path.join(root,'media',path.basename(url.pathname)); res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'application/javascript'); res.end(fs.readFileSync(file)); return;}
  if (url.pathname === '/favicon.ico') {res.writeHead(204);res.end();return;}
  const kind = url.pathname === '/workbench' ? 'workbench' : 'automatic';
  l10n.localizedVscode({env:{language:url.searchParams.get('lang') || 'de'}});
  const html = l10n.html(templates[kind]({asset:name=>'/media/'+name, nonce:'preview',cspSource:"'self'",sizeRange:{minBytes:8,maxBytes:640},loadLimit:1000}));
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.end(html.replace('<body>',`<body class="vscode-${url.searchParams.get('theme') || 'dark'}">`).replace('</head>',`<link rel="stylesheet" href="/theme.css?theme=${url.searchParams.get('theme') || 'dark'}"></head>`));
});
const jobs = [
 {name:'func_00100000',status:'running',attempts:6,best:{score:81.25},elapsed:21,generatedVariants:6,distinctObjects:5},
 {name:'func_00100100',status:'running',attempts:3,best:{score:73.4},elapsed:10},
 {name:'func_00100200',status:'review_ready',attempts:12,best:{score:100,technicalMatch:true},reason:'Technische Prüfung aller Platzierungen bestanden. Fachliche Review offen.',checks:{technicalPassed:true}},
 {name:'func_00100300',status:'review_ready',attempts:30,best:{score:92.5},reason:'Suche beendet',supportedVariantSources:32,historySkippedVariants:2},
 {name:'func_00100400',status:'blocked',deferred:true,attempts:1,reason:'Offene Operationen/Typen/Register: input_value',best:{score:61}},
 {name:'func_00100500',status:'error',attempts:1,reason:'Grobentwurf noch nicht kompilierbar; Compilerprotokoll und letzten Entwurf prüfen',lastAttempt:{}},
 {name:'func_00100600',status:'queued',attempts:0},
 {name:'func_00100700',status:'handed_off',attempts:9,best:{score:87.2}}
];
const session = {id:'1'.repeat(32),created:'2026-11-13T19:00:00Z',status:'running',owner:'synthetic-ui-review',jobs,names:jobs.map(j=>j.name),reserved:jobs.filter(j=>j.status!=='handed_off').map(j=>j.name),options:{maxAttempts:30},alive:true,searchPass:1};
const candidates = Array.from({length:24},(_,i)=>({name:'func_'+(0x200000+i*128).toString(16).padStart(8,'0'),size:128,program:i%2?'overlay_01':'main',cached:i===0}));
const send = (page, message) => page.evaluate(m=>window.dispatchEvent(new MessageEvent('message',{data:m})),message);
const selectedTab = page => page.locator('[role="tab"][aria-selected="true"]').getAttribute('data-tab');
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({executablePath:process.env.PS2DECOMP_BROWSER,headless:true});
 const failures=[];
 try {
  for(const lang of ['de','en']) for(const theme of ['dark','light']) {
   const page=await browser.newPage({viewport:{width:1180,height:1000}});
   page.on('pageerror',e=>failures.push(e.message)); page.on('console',m=>{if(m.type()==='error')failures.push(m.text());});
   await page.addInitScript(()=>{window.hostMessages=[];window.acquireVsCodeApi=()=>({postMessage:m=>window.hostMessages.push(m)});});
   await page.goto(base+`/automatic?lang=${lang}&theme=${theme}`);
   assert.equal(await page.locator('[data-tab="sessionsView"]').textContent(),lang==='de'?'Suchläufe & Review':'Searches & review');
   assert.equal(await page.locator('[data-tab="deferredView"]').textContent(),lang==='de'?'Zurückgestellt':'Deferred');
   await send(page,{type:'versions',data:[{id:'synthetic',label:'Synthetic PAL',available:true}],selected:['synthetic']});
   await send(page,{type:'sessions',data:[{...session,functionCount:8}]});
   await send(page,{type:'session',data:session,updatedAt:'2026-11-13T19:01:00Z'});
   assert.equal(await selectedTab(page),'sessionsView');
   assert.equal(await page.locator('.session-metric').count(),3);
   await page.locator('.job-details summary').first().click();
   await page.waitForFunction(()=>document.querySelector('.job-details').open);
   await send(page,{type:'session',data:session});
   assert.equal(await page.locator('.job-details').first().getAttribute('open'),'');
   assert.equal(await page.evaluate(()=>document.activeElement.dataset.action),'details');
   await page.locator('.job-details summary').first().click();
   await page.screenshot({path:path.join(out,`sessions-${lang}-${theme}.png`),fullPage:true});
   const manyJobs=Array.from({length:125},(_,i)=>({name:'synthetic_'+i,status:'queued',attempts:0}));
   const largeSession={...session,jobs:manyJobs,names:manyJobs.map(j=>j.name),reserved:manyJobs.map(j=>j.name)};
   await send(page,{type:'session',data:largeSession});
   await page.locator('#jobNext').click();assert.equal(await page.locator('.job-row').count(),25);
   await send(page,{type:'session',data:largeSession});assert.equal(await page.locator('.job-row').count(),25);
   await page.locator('#jobPrev').click();assert.equal(await page.locator('.job-row').count(),100);
   await send(page,{type:'session',data:session});
   await send(page,{type:'sessionProgress',session:session.id,state:'failed',text:'Synthetic read error',stale:true});
   assert.equal(await page.locator('#sessionUpdate').getAttribute('data-state'),'failed');
   assert.equal(await page.locator('.job-row').count(),7);
   await page.locator('[data-tab="sessionsView"]').focus(); await page.keyboard.press('ArrowRight');
   assert.equal(await selectedTab(page),'queueView');
   await send(page,{type:'session',data:session}); assert.equal(await selectedTab(page),'queueView');
   await send(page,{type:'candidates',data:candidates});
   assert.equal(await page.locator('#candidates input:checked').count(),10);
   await page.locator('#selectNone').click(); assert.equal(await page.locator('#start').isDisabled(),true);
   await page.locator('#select10').click(); assert.equal(await page.locator('#start').isDisabled(),false);
   assert.equal(await page.locator('#startSummary').textContent(),lang==='de'?'10 Funktionen ausgewählt.':'10 functions selected.');
   await page.screenshot({path:path.join(out,`queue-${lang}-${theme}.png`),fullPage:true});
   for(const width of [360,520,800,1180]) {
    await page.setViewportSize({width,height:1000});
    for(const pane of ['queueView','sessionsView','deferredView']) {
     await page.locator(`[data-tab="${pane}"]`).click();
     assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${lang} ${theme} ${pane} overflows at ${width}`);
    }
   }
   await page.setViewportSize({width:520,height:1000}); await page.locator('[data-tab="queueView"]').click();
   await page.screenshot({path:path.join(out,`narrow-${lang}-${theme}.png`),fullPage:true});
   assert.equal(await page.evaluate(()=>window.hostMessages.filter(m=>['start','startRetry','resume','retry','retryDeferred','handoff','handoffAll'].includes(m.type)).length),0);
   await page.locator('#start').click();
   const start=await page.evaluate(()=>window.hostMessages.find(m=>m.type==='start'));
   assert.equal(start.names.length,10);assert.deepEqual(start.settings,{workers:16,maxAttempts:30,seconds:90,minBytes:8,maxBytes:640});
   assert.equal(await page.locator('#start').isDisabled(),true);
   await send(page,{type:'started'});assert.equal(await selectedTab(page),'sessionsView');
   await page.goto(base+`/automatic?lang=${lang}&theme=${theme}`); await send(page,{type:'sessions',data:[]}); assert.equal(await selectedTab(page),'queueView');
   await page.locator('[data-tab="deferredView"]').click();
   await send(page,{type:'deferred',data:[{name:'func_00100400',reason:'synthetic',session:session.id}]});
   await page.locator('#deferredJobs button').click(); assert.equal(await selectedTab(page),'sessionsView');
   assert.equal(await page.locator('#showDeferred').isChecked(),true);
   await page.setViewportSize({width:1180,height:1000});
   await page.goto(base+`/workbench?lang=${lang}&theme=${theme}`);
   await send(page,{type:'status',data:{contracts:142,compiler:true,retail:true}});
   await send(page,{type:'detail',data:{name:'func_00100000',size:128,address:'0x00100000',section:'.text',asm:'/* synthetic listing */\n    jr $ra\n    nop',warnings:[],suggestions:[{title:'Belegte Signaturen',text:'Keine Werte geraten.'}],contracts:{func_00100000:[{declaration:'int func_00100000(int value);',status:'Manuell offen',evidence:['Synthetic fixture'],source:'synthetic.h'}]},fields:[{address:'0x00100004',operation:'lw',offset:'0x8',base:'a0'}],examples:[]}});
   await page.screenshot({path:path.join(out,`workbench-${lang}-${theme}.png`),fullPage:true});
   await page.locator('[data-tab="guide"]').focus(); await page.keyboard.press('End'); assert.equal(await selectedTab(page),'result');
   await page.keyboard.press('Home'); assert.equal(await selectedTab(page),'guide');
   for(const width of [360,520]) {
    await page.setViewportSize({width,height:1000});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`workbench overflows at ${width}`);
   }
   if(lang==='de' && theme==='dark') {
    await page.emulateMedia({forcedColors:'active'});
    await page.locator('[data-tab="guide"]').focus();await page.keyboard.press('ArrowRight');
    assert.equal(await selectedTab(page),'rough');
    await page.screenshot({path:path.join(out,'workbench-high-contrast.png'),fullPage:true});
   }
   await page.close();console.log(`PASS ${lang} / ${theme}: tabs, polling, details/focus, selection, start, deferred navigation, responsive layout, workbench`);
  }
  assert.deepEqual(failures,[]);console.log('No JavaScript or CSP errors. Screenshots:',out);
 } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
