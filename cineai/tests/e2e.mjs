// SmartMovieSearch end-to-end smoke test (Playwright, headless Chromium).
//
// Drives the real running stack: homepage, trending, usage badge, a full live
// search through the agent pipeline (costs 1 search + real Claude tokens),
// observability tabs, modals, theme toggle. Optionally checks the live site
// through Cloudflare with E2E_LIVE=1.
//
// Run:  make test-e2e         (from cineai/)
//  or:  cd tests && npm install && node e2e.mjs
//
// The backend rejects headless user agents on purpose (anti-bot), so the test
// spoofs a real-browser UA — that rejection is covered as its own test case.
import { chromium } from 'playwright'
import { mkdirSync } from 'fs'

const LOCAL = process.env.E2E_URL ?? 'http://localhost:5174'
const LIVE = 'https://smartmoviesearch.com'
const CHECK_LIVE = process.env.E2E_LIVE === '1'
const QUERY = 'Tell me about Inception — cast, rating, themes'
const OUT = new URL('./shots/', import.meta.url).pathname
mkdirSync(OUT, { recursive: true })
const SHOT = (n) => `${OUT}${n}.png`

const results = []
const pass = (name, detail = '') => { results.push({ name, ok: true, detail }); console.log(`  ✅ ${name}${detail ? ' — ' + detail : ''}`) }
const fail = (name, detail = '') => { results.push({ name, ok: false, detail }); console.log(`  ❌ ${name}${detail ? ' — ' + detail : ''}`) }

const browser = await chromium.launch()

// ── 0. Anti-bot guard: default headless UA must be rejected ──────────────────
console.log('\n[0] Anti-bot guard (headless UA should be blocked)')
try {
  const botCtx = await browser.newContext()
  const botPage = await botCtx.newPage()
  await botPage.goto(LOCAL, { waitUntil: 'domcontentloaded', timeout: 20000 })
  await botPage.fill('.query-input', 'test')
  await botPage.click('.query-btn')
  const banner = await botPage.waitForSelector('.error-banner-message, .error-banner', { timeout: 20000 })
  const txt = await banner.innerText()
  const blocked = /automated|bot|browser/i.test(txt)
  blocked ? pass('headless UA rejected', txt.slice(0, 60)) : fail('headless UA not rejected', txt.slice(0, 80))
  await botCtx.close()
} catch (e) { fail('anti-bot check', String(e).slice(0, 150)) }

// Real-browser UA for the rest of the suite.
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
})
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)) })
page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + String(e).slice(0, 200)))

// ── 1. Homepage ──────────────────────────────────────────────────────────────
console.log('\n[1] Homepage')
try {
  const resp = await page.goto(LOCAL, { waitUntil: 'networkidle', timeout: 20000 })
  resp.status() === 200 ? pass('homepage HTTP 200') : fail('homepage status', String(resp.status()))
  const title = await page.title()
  const titleOk = /smart\s*movie\s*search/i.test(title)
  titleOk ? pass('page title', title) : fail('page title', title)
  await page.waitForSelector('.header-title', { timeout: 10000 })
  pass('app rendered (header present)')
} catch (e) { fail('homepage load', String(e).slice(0, 150)) }

// ── 2. Trending cards (TMDB via backend) ─────────────────────────────────────
console.log('\n[2] Trending (backend /api/trending → TMDB)')
try {
  await page.waitForSelector('.movie-card', { timeout: 15000 })
  const n = await page.locator('.movie-card').count()
  pass('trending cards rendered', `${n} cards`)
  const posters = await page.locator('.movie-card img').count()
  posters > 0 ? pass('posters loaded', `${posters} imgs`) : fail('posters loaded', '0 images')
  const strayZero = await page.locator('.movie-card-info', { hasText: /^0$/ }).count()
  strayZero === 0 ? pass('no stray "0" on unrated cards') : fail('stray 0 rendered', `${strayZero} cards`)
} catch (e) { fail('trending cards', String(e).slice(0, 150)) }

// ── 3. Usage badge (rate-limit UI) ───────────────────────────────────────────
console.log('\n[3] Usage badge')
let usageBefore = ''
try {
  const badge = page.locator('.usage-badge, [class*="usage"]').first()
  await badge.waitFor({ timeout: 8000 })
  usageBefore = (await badge.innerText()).replace(/\n/g, ' ')
  pass('usage badge visible', usageBefore)
} catch (e) { fail('usage badge', String(e).slice(0, 150)) }

await page.screenshot({ path: SHOT('1-homepage') })

// ── 4. Real search (full agent pipeline + SSE streaming, shown as chat steps) ──
console.log(`\n[4] Live search — "${QUERY}" (costs 1 search + tokens)`)
try {
  await page.fill('.query-input', QUERY)
  await page.click('.query-btn')
  await page.waitForSelector('.step-trace .step', { timeout: 15000 })
  pass('step trace started in the chat')
  const q = await page.locator('.current-turn .history-q').innerText()
  q.includes(QUERY) ? pass('question shown as a chat message') : fail('question bubble', q.slice(0, 80))
  const cleared = await page.inputValue('.query-input')
  cleared === '' ? pass('input cleared for a follow-up') : fail('input not cleared', cleared)
  await page.waitForSelector('.history-turn .answer-content', { timeout: 120000 })
  const answer = await page.locator('.answer-content').last().innerText()
  answer.length > 300 ? pass('answer streamed', `${answer.length} chars`) : fail('answer too short', `${answer.length} chars: ${answer.slice(0, 80)}`)
  const relevant = /inception/i.test(answer)
  relevant ? pass('answer mentions Inception') : fail('answer relevance', answer.slice(0, 120))
  const grounded = !/couldn't find|wasn't able to find/i.test(answer)
  grounded ? pass('answer is grounded (not a "couldn\'t find" fallback)') : fail('agent found nothing', answer.slice(0, 120))
  const answerBlocks = await page.locator('.answer-content').count()
  answerBlocks === 1 ? pass('answer rendered once') : fail('answer duplicated', `${answerBlocks} blocks`)
  const steps = await page.locator('.history-turn .step-text').allInnerTexts()
  const want = [/Knowledge base:/, /Routing →/, /Combined .+ into the answer/]
  want.every(r => steps.some(t => r.test(t)))
    ? pass('steps kept in the history', steps.join(' | ').slice(0, 160))
    : fail('step trace', steps.join(' | ').slice(0, 160))
  const summary = await page.locator('.history-turn .step-toggle').innerText()
  ;/\d+ sources? · [\d.]+s · [\d,]+ in.* \/ [\d,]+ out tokens · claude-/.test(summary)
    ? pass('summary line with model', summary) : fail('summary line', summary)
  // 👍/👎 bar under the finished answer (not clicked: a vote would land in the real feedback log)
  const fbVisible = await page.locator('.fb-bar .fb-btn').first().isVisible().catch(() => false)
  fbVisible ? pass('feedback bar shown') : fail('feedback bar missing', 'no .fb-bar under the answer')
} catch (e) { fail('search flow', String(e).slice(0, 200)) }
await page.screenshot({ path: SHOT('2-search-result') })

// ── 4b. Follow-up in the same conversation (costs 1 more search) ─────────────
console.log('\n[4b] Follow-up question (chat memory)')
try {
  const docked = await page.locator('.query-section--docked').count()
  docked === 1 ? pass('composer docked under the conversation') : fail('composer', 'not docked')
  await page.fill('.query-input', 'Who composed its score?')
  await page.click('.query-btn')
  await page.waitForFunction(() => document.querySelectorAll('.history-turn').length === 2, null, { timeout: 120000 })
  const a2 = await page.locator('.history-turn').last().locator('.answer-content').innerText()
  ;/zimmer/i.test(a2) ? pass('follow-up resolved "its" from the previous turn', 'mentions Hans Zimmer')
    : fail('follow-up', a2.slice(0, 120))
  const folded = await page.locator('.history-turn').first().locator('.step-list').count()
  folded === 0 ? pass('older turn folded to its summary line') : fail('older turn not folded', `${folded}`)
} catch (e) { fail('follow-up flow', String(e).slice(0, 200)) }
await page.screenshot({ path: SHOT('3-follow-up') })

// ── 4c. Prompt & response for each step (free: already in the stream) ────────
console.log('\n[4c] Prompt & response per step')
try {
  const last = page.locator('.history-turn').last()
  const io = last.locator('.step-io')
  const n = await io.count()
  await io.first().locator('> summary').click()
  const roles = await io.first().locator('.step-io-block > summary').allInnerTexts()
  const ok = roles.some(t => /Prompt: system message/.test(t)) && roles.some(t => /Claude's response/.test(t))
  ok ? pass('steps show their prompt & response', `${n} steps expandable`) : fail('prompt & response', roles.join(' | ').slice(0, 160))
  const data = await last.locator('.step-io-data').count()
  data > 0 ? pass('source data shown (what the API returned)') : fail('source data', 'no .step-io-data block')
} catch (e) { fail('prompt & response', String(e).slice(0, 200)) }

// ── 4d. Chat history: New chat keeps the old one (free: no search) ─────────────
console.log('\n[4d] Chat history')
try {
  await page.click('.new-chat-btn')
  const left = await page.locator('.history-turn').count()
  const btn = page.locator('.chats-menu > button')
  const label = await btn.innerText()
  left === 0 && /Chats \(\d+\)/.test(label) ? pass('New chat keeps the old one', label) : fail('new chat', `${left} turns, "${label}"`)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await btn.click()
  await page.locator('.chats-open').first().click()
  const back = await page.locator('.history-turn').count()
  back === 2 ? pass('old chat reopens after a reload', `${back} turns`) : fail('reopen chat', `${back} turns`)
} catch (e) { fail('chat history', String(e).slice(0, 200)) }

// ── 5. Pipeline panel (optional, ⚡) + observability tabs ─────────────────────
console.log('\n[5] Pipeline panel + observability tabs')
try {
  const hidden = await page.locator('.panel-right').count()
  hidden === 0 ? pass('pipeline panel hidden by default') : fail('pipeline panel', 'shown by default')
  await page.click('[aria-label="AI pipeline panel"]')
  await page.waitForSelector('.panel-right', { timeout: 5000 })
  const badge = await page.locator('.obs-badge').innerText()
  const events = await page.locator('.obs-event-count').innerText()
  badge === 'COMPLETE' ? pass('panel opens with the last run', `${badge}, ${events}`) : fail('panel badge', badge)
  for (const sel of ['text=⏱ Timeline', 'text=📋 Events', 'text=📄 Context', 'text=🗺 Graph']) {
    await page.click(sel)
    await page.waitForTimeout(250)
  }
  pass('tabs clickable (graph/timeline/events/context)')
  await page.click('[aria-label="AI pipeline panel"]')       // leave the default for the next visitor's localStorage
} catch (e) { fail('observability tabs', String(e).slice(0, 150)) }

// ── 6. Usage accounting after search ─────────────────────────────────────────
console.log('\n[6] Usage accounting')
try {
  await page.waitForTimeout(1500)
  const after = (await page.locator('.usage-badge, [class*="usage"]').first().innerText()).replace(/\n/g, ' ')
  after !== usageBefore ? pass('usage badge updated', `"${usageBefore}" → "${after}"`) : fail('usage badge unchanged', after)
} catch (e) { fail('usage after search', String(e).slice(0, 150)) }

// ── 7. Modals ────────────────────────────────────────────────────────────────
console.log('\n[7] Modals')
try {
  await page.click('[aria-label="Knowledge base"]')
  await page.waitForTimeout(2500)
  const kb = await page.locator('.modal, [class*="modal"]').first().innerText()
  kb.length > 50 ? pass('knowledge modal opens', kb.slice(0, 80).replace(/\n/g, ' | ')) : fail('knowledge modal', kb.slice(0, 80))
  await page.keyboard.press('Escape')
  await page.click('[aria-label="Evals and ops"]')
  await page.waitForSelector('.ops-card', { timeout: 8000 })
  const tiers = await page.locator('.ops-card').count()
  await page.click('text=⏰ Scheduled jobs')
  await page.waitForSelector('.ops-table', { timeout: 8000 })
  const jobs = await page.locator('.ops-table tbody tr').count()
  await page.click('text=📥 Ingest & embeddings')
  const flow = await page.locator('.ops-flow').innerText()
  tiers >= 6 && jobs >= 5 && /text-embedding-3-small/.test(flow)
    ? pass('evals & ops panel', `${tiers} eval tiers, ${jobs} scheduled jobs, ingest wiring shown`)
    : fail('evals & ops panel', `${tiers} tiers, ${jobs} jobs`)
  await page.keyboard.press('Escape')
  const footer = await page.locator('.site-footer').innerText()
  ;/© \d{4} smartmoviesearch\.com/.test(footer) ? pass('footer', footer) : fail('footer', footer)
  await page.click('[aria-label="Service status"]')
  await page.waitForTimeout(2500)
  const st = await page.locator('.modal, [class*="modal"]').first().innerText()
  st.length > 30 ? pass('status modal opens', st.slice(0, 80).replace(/\n/g, ' | ')) : fail('status modal', st.slice(0, 80))
  await page.keyboard.press('Escape')
} catch (e) { fail('modals', String(e).slice(0, 150)) }

// ── 8. Theme toggle ──────────────────────────────────────────────────────────
console.log('\n[8] Theme toggle')
try {
  const before = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
  await page.click('[aria-label="Toggle theme"]')
  await page.waitForTimeout(300)
  const after = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
  before !== after ? pass('theme switches', `${before} → ${after}`) : fail('theme toggle', `stuck on ${before}`)
  await page.click('[aria-label="Toggle theme"]')
} catch (e) { fail('theme toggle', String(e).slice(0, 150)) }

// ── 9. Live site through Cloudflare (opt-in: E2E_LIVE=1) ─────────────────────
if (CHECK_LIVE) {
  console.log('\n[9] Live site (Cloudflare → mTLS origin)')
  try {
    const p2 = await ctx.newPage()
    const r = await p2.goto(LIVE, { waitUntil: 'domcontentloaded', timeout: 25000 })
    r.status() === 200 ? pass('live homepage 200 via CF') : fail('live homepage', String(r.status()))
    await p2.waitForSelector('.header-title', { timeout: 15000 })
    pass('live app renders')
    const cfRay = r.headers()['cf-ray']
    cfRay ? pass('served through Cloudflare', `cf-ray ${cfRay}`) : fail('cf-ray header missing')
    await p2.close()
  } catch (e) { fail('live site', String(e).slice(0, 150)) }
}

// ── Summary ──────────────────────────────────────────────────────────────────
console.log('\n' + '='.repeat(60))
const bad = results.filter(r => !r.ok)
console.log(`RESULT: ${results.length - bad.length}/${results.length} passed`)
if (bad.length) bad.forEach(b => console.log(`  FAILED: ${b.name} — ${b.detail}`))
if (consoleErrors.length) {
  console.log(`\nBrowser console errors (${consoleErrors.length}):`)
  ;[...new Set(consoleErrors)].slice(0, 8).forEach(e => console.log('  • ' + e))
} else console.log('\nNo browser console errors.')
await browser.close()
process.exit(bad.length ? 1 : 0)
