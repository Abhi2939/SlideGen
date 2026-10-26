const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('app/static/index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const mode = process.argv[2];
const id = 'a'.repeat(32);

async function run() {
  const elements = new Map();
  const storedKey = mode === 'completed_restore' ? 'slidegen.completedJob' : 'slidegen.activeJob';
  const restore = ['resume', 'expired', 'completed_restore'].includes(mode);
  const storage = new Map(restore ? [[storedKey, id]] : []);
  const timers = new Map();
  let sequence = 0, timerId = 0, reviewRequested = false, generationPosts = 0;
  const element = name => {
    if (!elements.has(name)) {
      const classes = new Set();
      elements.set(name, {
        value: 'A presentation about dogs', disabled: false, hidden: true, style: {}, handlers: {}, children: [],
        classList: {
          add(...values) { values.forEach(value => classes.add(value)); },
          remove(...values) { values.forEach(value => classes.delete(value)); },
          toggle(value, enabled) { enabled ? classes.add(value) : classes.delete(value); },
          contains(value) { return classes.has(value); },
        },
        addEventListener(event, fn) { this.handlers[event] = fn; },
        replaceChildren() { this.children = []; },
        appendChild(child) { this.children.push(child); },
      });
    }
    return elements.get(name);
  };
  const response = data => ({ ok: true, status: 200, json: async () => data });
  const context = {
    document: { getElementById: element, querySelectorAll: () => [], createElement: () => ({textContent:''}) },
    sessionStorage: { getItem: key => storage.get(key), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k) },
    AbortController, Date, JSON, URL, console,
    setTimeout(fn, ms) {
      const n = ++timerId;
      if (ms === 30000) timers.set(n, fn);
      else queueMicrotask(fn);
      return n;
    },
    clearTimeout(n) { timers.delete(n); },
    async fetch(url, options) {
      if (url === '/api/jobs') { generationPosts++; return response({ id, status:'queued' }); }
      if (url.endsWith('/review')) {
        if (options.method === 'POST') { reviewRequested = true; return response({status:'queued'}); }
        if (!reviewRequested) return response({status:'not_requested'});
        return mode === 'review_failure'
          ? response({status:'failed', message:'Design review unavailable. Your draft is still available.'})
          : response({status:'completed', message:'Review complete.', grades:[{score:7, fixes:[{detail:'Improve spacing.'}]}]});
      }
      if (mode === 'expired') return {ok:false,status:404,json:async()=>({detail:'Job not found.'})};
      if (mode === 'timeout') {
        return {ok:true,status:200,json:()=>new Promise((resolve,reject)=>{
          options.signal.addEventListener('abort',()=>reject(new Error('aborted')));
          queueMicrotask(()=>[...timers.values()].at(-1)());
        })};
      }
      if (mode === 'failed') return response({id,status:'failed',failure:{message:'Content checks failed.'}});
      return response({id,status:sequence++===0?'running':'done',stage:'Drafting 6 slides'});
    },
  };
  vm.createContext(context);
  vm.runInContext(script, context);
  if (!restore) await element('job-form').handlers.submit({preventDefault(){}});
  for (let i=0;i<10;i++) await new Promise(resolve=>setImmediate(resolve));
  if (mode.startsWith('review_')) {
    await element('review-design').handlers.click();
    assert.equal(element('download').classList.contains('visible'),true);
    assert.equal(generationPosts,1);
    if (mode === 'review_failure') assert.match(element('review-status-text').textContent,/still available/);
    else assert.equal(element('review-findings').children.length,1);
  }
  assert.equal(element('generate').disabled,false);
  assert.equal(timers.size,0);
  if (mode === 'timeout') {
    assert.equal(storage.get('slidegen.activeJob'),id);
    assert.match(element('status-text').textContent,/Refresh this page to reconnect/);
    assert.match(element('status-text').textContent,/Job reference:/);
  } else if (['expired','failed'].includes(mode)) {
    assert.equal(storage.size,0);
    assert.match(element('status-text').textContent,mode==='expired'?/Job not found/:/Content checks failed/);
  } else {
    assert.equal(storage.get('slidegen.activeJob'),undefined);
    assert.equal(storage.get('slidegen.completedJob'),id);
    assert.equal(element('download-link').href,`/api/jobs/${id}/download`);
    assert.match(element('status-text').textContent,/draft is ready/);
    assert.equal(element('review-controls').hidden,false);
  }
  console.log(mode+': passed');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
