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

// ── 4. Real search (full agent pipeline + SSE streaming) ─────────────────────
console.log(`\n[4] Live search — "${QUERY}" (costs 1 search + tokens)`)
try {
  await page.fill('.query-input', QUERY)
  await page.click('.query-btn')
  await page.waitForSelector('.obs-badge.live', { timeout: 15000 })
  pass('SSE stream started (LIVE badge)')
  await page.waitForSelector('.obs-badge.live', { state: 'detached', timeout: 120000 })
  const answer = await page.locator('.answer-content').last().innerText()
  answer.length > 300 ? pass('answer streamed', `${answer.length} chars`) : fail('answer too short', `${answer.length} chars: ${answer.slice(0, 80)}`)
  const relevant = /inception/i.test(answer)
  relevant ? pass('answer mentions Inception') : fail('answer relevance', answer.slice(0, 120))
  const grounded = !/couldn't find|wasn't able to find/i.test(answer)
  grounded ? pass('answer is grounded (not a "couldn\'t find" fallback)') : fail('agent found nothing', answer.slice(0, 120))
  const answerBlocks = await page.locator('.answer-content').count()
  answerBlocks === 1 ? pass('answer rendered once') : fail('answer duplicated', `${answerBlocks} blocks`)
  const events = await page.locator('.obs-event-count').innerText()
  pass('pipeline events emitted', events || '(count hidden)')
} catch (e) { fail('search flow', String(e).slice(0, 200)) }
await page.screenshot({ path: SHOT('2-search-result') })

// ── 5. Observability tabs ────────────────────────────────────────────────────
console.log('\n[5] Observability tabs')
try {
  for (const sel of ['text=⏱ Timeline', 'text=📋 Events', 'text=📄 Context', 'text=🗺 Graph']) {
    await page.click(sel)
    await page.waitForTimeout(250)
  }
  pass('tabs clickable (graph/timeline/events/context)')
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
