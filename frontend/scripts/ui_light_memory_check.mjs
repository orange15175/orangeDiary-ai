// 独立浏览器交互测试：全部 API 拦截为固定数据，不访问真实账号/数据库/模型。
import { chromium } from 'playwright'
import assert from 'node:assert/strict'

const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' })
const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
const errors = []
const calls = []
let memories = []
let observations = []
let activeVersion = 1
let purchased = false
const makePlan = (version = activeVersion) => ({ planId: 1, startDate: '2026-11-20', endDate: '2026-11-20', status: 'active', activeVersion, versionNumber: version, versionStatus: version === activeVersion ? 'adopted' : 'draft', reason: version > 1 ? '今晚加班' : null, checks: { code: 'OK', diff: version > 1 ? [{ date: '2026-11-20', mealType: 'dinner', fromName: '香菇鸡腿', toName: '快手鸡腿' }] : [] }, meals: [{ id: 10, date: '2026-11-20', mealType: 'dinner', recipeName: '鸡腿饭', servings: 1, executionState: 'planned' }] })
const listing = () => ({ id: 1, startDate: '2026-11-20', endDate: '2026-11-20', totals: { priceStatus: 'unknown', budgetStatus: 'unverified', unknownItemCount: 1 }, items: [{ id: 20, label: '鸡腿', needQuantity: '200', unit: 'g', state: purchased ? 'purchased' : 'needs_check', price: {} }] })
const report = { id: 1, periodStart: '2026-11-09', periodEnd: '2026-11-15', status: 'current', metrics: { execution: { primaryText: '反馈不足' } }, dataGaps: [], suggestions: [], draftPlanRef: 'meal_plan:1:v3' }
page.on('pageerror', error => errors.push(error.message))
await page.addInitScript(() => {
  localStorage.setItem('token', 'offline-ui-test')
  localStorage.setItem('tokenExpiresAt', String(Date.now() + 3600000))
})
await page.route('**/api/**', async route => {
  const req = route.request()
  const url = new URL(req.url())
  const path = url.pathname.replace('/api', '')
  const body = req.postDataJSON() || {}
  calls.push({ path, method: req.method(), body })
  let data = null
  if (path === '/users/me') data = { id: 1, nickname: '测试', role: 'user', profileCompleted: true }
  else if (path === '/diet-preferences') data = { allergens: [], dislikes: [], equipment: [], version: 1 }
  else if (path === '/known-ingredients' && req.method() === 'GET') data = memories
  else if (path.startsWith('/known-ingredients')) { memories = [{ id: 1, label: body.label, presence: body.presence, version: 1, hint: '今天确认还有一些，不估算剩余克重。' }]; data = memories[0] }
  else if (path === '/meal-plans') data = [makePlan()]
  else if (path === '/meal-plans/1/versions') data = [{ version: 1, status: 'adopted' }, { version: 2, status: 'draft' }]
  else if (path === '/meal-plans/1/adjustments') data = makePlan(2)
  else if (path === '/meal-plans/1/adopt') { activeVersion = body.versionNumber; data = makePlan() }
  else if (path === '/meal-plans/1') data = makePlan(Number(url.searchParams.get('versionNumber')) || activeVersion)
  else if (path === '/ingredient-checks' || path.startsWith('/ingredient-checks/')) data = { id: 1, version: 1, status: body.answers ? 'confirmed' : 'open', items: [{ id: 2, label: '鸡腿', status: body.answers ? 'confirmed' : 'needs_check' }] }
  else if (path === '/shopping-lists' || path === '/shopping-lists/1') data = listing()
  else if (path === '/shopping-items/20/purchase') { purchased = true; data = listing() }
  else if (path === '/shopping-items/20/purchase-reversal') { purchased = false; data = listing() }
  else if (path === '/meal-observations') {
    if (req.method() === 'POST') observations.push({ id: 1, ...body, status: 'draft' })
    data = req.method() === 'GET' ? observations : observations.at(-1)
  }
  else if (path === '/reviews') data = req.method() === 'GET' ? [] : report
  else if (path === '/diet-records') data = []
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data }) })
})
const base = process.env.UI_TEST_URL || 'http://127.0.0.1:5187'
try {
  await page.goto(`${base}/#/diet/preferences`)
  await page.getByLabel('过敏食材（多项用顿号分开）').fill('花生、虾')
  await page.getByRole('button', { name: '保存偏好', exact: true }).click()
  await page.getByLabel('顺手记一种食材').fill('鸡腿')
  await page.getByRole('button', { name: '记一下', exact: true }).click()
  await page.getByRole('button', { name: '还有一些', exact: true }).waitFor()
  assert.deepEqual(calls.find(c => c.path === '/diet-preferences' && c.method === 'PUT').body.allergens, ['花生', '虾'])
  assert(!('quantity' in calls.find(c => c.path === '/known-ingredients' && c.method === 'POST').body))
  await page.screenshot({ path: `${process.env.TEMP}/orange-light-memory-preferences.png`, fullPage: true })

  await page.goto(`${base}/#/diet/plan`)
  await page.getByRole('button', { name: '打开计划与调整记录' }).click()
  await page.getByRole('button', { name: '顺便核对食材' }).click()
  await page.getByRole('button', { name: '还有一些', exact: true }).click()
  await page.getByText('今天确认还有一些', { exact: true }).waitFor()
  assert.equal(calls.find(c => c.path === '/ingredient-checks/1/answers').body.answers[0].answer, 'some')
  await page.getByLabel('发生了什么').fill('今晚加班')
  await page.getByLabel('后续每餐最多几分钟').fill('15')
  await page.getByRole('button', { name: '生成调整草稿', exact: true }).click()
  await page.getByText('香菇鸡腿 → 快手鸡腿').waitFor()
  assert.equal(calls.find(c => c.path.endsWith('/adjustments')).body.maxPrepMinutes, 15)
  await page.getByRole('button', { name: '采纳这份计划', exact: true }).click()
  await page.getByRole('button', { name: '看看需要买什么 →', exact: true }).click()
  await page.getByRole('button', { name: '标记已买', exact: true }).click()
  await page.getByRole('button', { name: '撤销购买', exact: true }).click()
  await page.getByRole('button', { name: '标记已买', exact: true }).waitFor()

  await page.goto(`${base}/#/diet/review`)
  await page.getByLabel('吃了什么', { exact: true }).fill('和同事吃火锅，份量记不清')
  await page.getByRole('button', { name: '记下来', exact: true }).click()
  await page.getByText('和同事吃火锅，份量记不清', { exact: true }).waitFor()
  await page.getByRole('checkbox', { name: '同时生成下周草稿（不会自动采纳）' }).check()
  await page.getByRole('button', { name: '生成报告', exact: true }).click()
  await page.getByRole('button', { name: '查看下周草稿，再决定采纳 →' }).click()
  await page.waitForURL('**/diet/plan?planId=1&version=3')
  assert.equal(calls.find(c => c.path === '/reviews' && c.method === 'POST').body.withDraftPlan, true)
  assert.deepEqual(errors, [])
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  console.log('PASS: 偏好保存、轻量食材、计划详情、核对、调整对比、采纳、购买/撤销、实际饮食、下周草稿导航；无运行时错误/横向溢出。所有API均为离线桩。')
} finally { await browser.close() }
