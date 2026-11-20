import http from './http'
import { shanghaiDate } from '../utils/reportDate'

export function reportFilename(disposition = '') {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (encoded) {
    try { return decodeURIComponent(encoded[1]).replace(/[\\/]/g, '_') } catch { /* Use safe fallback. */ }
  }
  return `今日饮食报告-${shanghaiDate()}.pdf`
}

export async function downloadTodayDietReport() {
  const response = await http.get('/reports/diet/today.pdf', { responseType: 'blob', timeout: 45_000 })
  const url = URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.download = reportFilename(response.headers?.['content-disposition'])
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Mobile browsers may consume the URL after this event loop completes.
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
