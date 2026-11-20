import http from './http'

// 每次进入受保护页面都可调用，后端会保证当天只记录一次。
export const recordTodayCheckin = () => {
  // One protected-page visit is the same daily check-in intent across routes and refreshes.
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(new Date())
  const date = `${parts.find((part) => part.type === 'year').value}-${parts.find((part) => part.type === 'month').value}-${parts.find((part) => part.type === 'day').value}`
  return http.post('/check-ins/today', null, { headers: { 'X-Request-Id': `checkin-${date}` } })
}
export const getCheckinFootprint = (startDate, endDate) => http.get('/check-ins', { params: { startDate, endDate } })
