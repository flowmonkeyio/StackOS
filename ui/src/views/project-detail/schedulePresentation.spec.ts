// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { readableSchedule } from './schedulePresentation'
describe('exact schedule descriptions', () => {
  it.each([
    ['0 22 * * *', 'Every day at 10:00 PM'],
    ['0 9 * * 0', 'Mondays at 9:00 AM'],
    ['0 16 * * mon-fri', 'Weekdays at 4:00 PM'],
    ['* * * * *', 'Every minute'],
    ['0 9 1 * *', 'Cron: 0 9 1 * *'],
    ['bad', 'Cron: bad'],
  ])('describes %s without changing its meaning', (cron, text) => expect(readableSchedule(cron)).toBe(text))
})
