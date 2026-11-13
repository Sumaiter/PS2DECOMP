'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), os=require('node:os'), path=require('node:path');
const {resolveRepository}=require('../workspace_repository');
function fixture(t) {const root=fs.mkdtempSync(path.join(os.tmpdir(),'ps2decomp-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
function workspace(folders, values={}) {return {workspaceFolders:folders.map(p=>({uri:{fsPath:p}})),getConfiguration:()=>({inspect:()=>values})};}
test('detect only a configured open game; ignore global repository setting',t=>{
 const root=fixture(t);fs.writeFileSync(path.join(root,'ps2decomp.json'),'{}');
 assert.equal(resolveRepository(workspace([root],{globalValue:'C:/unrelated'}),'ps2decomp',['ps2decomp.json']),fs.realpathSync(root));
});
test('reject outside-workspace roots and ambiguous games',t=>{
 const root=fixture(t),a=path.join(root,'a'),b=path.join(root,'b');for(const p of [a,b]){fs.mkdirSync(p);fs.writeFileSync(path.join(p,'ps2decomp.json'),'{}');}
 assert.throws(()=>resolveRepository(workspace([a],{workspaceValue:b}),'ps2decomp',['ps2decomp.json']));
 assert.throws(()=>resolveRepository(workspace([a,b]),'ps2decomp',['ps2decomp.json']));
});
