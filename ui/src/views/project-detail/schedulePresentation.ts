/** Describe only cron forms we can render exactly; retain all other expressions. */
export function readableSchedule(cron: string): string {
  const fields = cron.trim().split(/\s+/)
  const [minute, hour, day, month, weekday] = fields
  if (fields.length !== 5) return `Cron: ${cron}`
  if (cron.trim() === '* * * * *') return 'Every minute'
  if (day === '*' && month === '*' && /^\d+$/.test(minute) && /^\d+$/.test(hour) && +hour < 24 && +minute < 60) {
    const time = `${+hour % 12 || 12}:${minute.padStart(2, '0')} ${+hour < 12 ? 'AM' : 'PM'}`
    if (weekday === '*') return `Every day at ${time}`
    // APScheduler's five-field cron uses Monday=0, not Unix Sunday=0.
    if (weekday === 'mon-fri' || weekday === '0-4') return `Weekdays at ${time}`
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    const index = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].indexOf(weekday)
    const numeric = /^[0-6]$/.test(weekday) ? +weekday : index
    if (numeric >= 0) return `${days[numeric]}s at ${time}`
  }
  return `Cron: ${cron}`
}
