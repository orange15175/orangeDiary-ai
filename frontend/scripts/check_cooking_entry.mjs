// 验证本轮改动：四宫格已下线、旧地址不再能进、聊天页有做菜示例且能真的出做法。
import { chromium } from 'playwright'

const API = 'http://127.0.0.1:8000'
const WEB = 'http://127.0.0.1:5173'
const email = `uicook${Date.now()}@example.com`
const password = 'Orange2026!'

const ok = []
const bad = []
const errors = []
function check(name, pass, detail = '') {
  if (pass) { ok.push(name); console.log(`  [OK] ${name}`) }
  else { bad.push(`${name} :: ${detail}`); console.log(`  [FAIL] ${name} :: ${detail}`) }
}

async function post(path, body, token) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body)
  })
  return res.json()
}

async function put(path, body, token) {
  const res = await fetch(`${API}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body)
  })
  return res.json()
}

await post('/api/auth/register', { username: 'uicook', email, password, confirmPassword: password })
const login = await post('/api/auth/login', { account: email, password })
const tokens = login.data
// 未填档案的新账号会被引导到 /profile，先补齐再测聊天页。
await put('/api/users/profile', {
  gender: 'female', birthday: '1997-03-02', heightCm: 163,
  currentWeightKg: 56, targetWeightKg: 53, activityLevel: 'low'
}, tokens.token)
await post('/api/users/goals', { goalType: 'lose', startDate: new Date().toISOString().slice(0, 10) }, tokens.token)

const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' })
const context = await browser.newContext({ viewport: { width: 414, height: 900 } })
const page = await context.newPage()
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

await page.goto(`${WEB}/#/login`, { waitUntil: 'networkidle' })
await page.evaluate((t) => {
  localStorage.setItem('token', t.token)
  localStorage.setItem('tokenExpiresIn', String(t.expiresIn))
  localStorage.setItem('tokenExpiresAt', String(Date.now() + Number(t.expiresIn) * 1000))
  localStorage.setItem('refreshToken', t.refreshToken)
}, tokens)
// 只改 hash 不会重建应用，必须整页刷新让 Pinia 从 localStorage 读回登录态。
await page.reload({ waitUntil: 'networkidle' })

// 1) 饮食总览：四宫格必须消失
await page.goto(`${WEB}/#/diet`, { waitUntil: 'networkidle' })
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(800)
const overviewText = await page.locator('body').innerText()
check('饮食总览已移除「智能饮食」四宫格', !overviewText.includes('智能饮食'))
check('饮食总览不再出现「私人厨师」入口', !overviewText.includes('私人厨师'))
await page.screenshot({ path: 'scripts/_ui_diet.png', fullPage: false })

// 2) 旧地址不可达：应被重定向回饮食总览
for (const [path, label] of [['/diet/chef', '私人厨师'], ['/diet/plan', '周计划'], ['/diet/shopping', '采购'], ['/diet/review', '复盘']]) {
  await page.goto(`${WEB}/#${path}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const body = await page.locator('body').innerText()
  check(`${path} 不再打开原页面`, !body.includes(label), body.slice(0, 60))
}

// 3) 聊天页：做菜示例存在
await page.goto(`${WEB}/#/ai`, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)
const chatText = await page.locator('body').innerText()
check('聊天页有做菜示例问题', chatText.includes('红烧肉怎么做'))
await page.screenshot({ path: 'scripts/_ui_ai.png', fullPage: false })

// 4) 真的问一句做菜，前端能渲染回答
await page.getByText('红烧肉怎么做').first().click()
await page.waitForTimeout(1500)
await page.waitForFunction(() => document.body.innerText.includes('五花肉'), null, { timeout: 120000 })
const answerText = await page.locator('body').innerText()
check('做菜回答渲染出食材', answerText.includes('五花肉'))
check('做菜回答没有生成记录卡按钮', !answerText.includes('确认保存') && !answerText.includes('核对下方记录卡'))
await page.screenshot({ path: 'scripts/_ui_ai_cooking.png', fullPage: false })

check('无 JS 运行时报错', errors.length === 0, errors.join(' | '))

await browser.close()
console.log(`\n通过 ${ok.length} / ${ok.length + bad.length}`)
if (bad.length) { console.log('失败：'); bad.forEach((b) => console.log(' -', b)); process.exit(1) }
