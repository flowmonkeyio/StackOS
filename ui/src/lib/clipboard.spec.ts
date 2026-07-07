import { afterEach, describe, expect, it, vi } from 'vitest'

import { copyTextToClipboard } from './clipboard'

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const originalExecCommand = Object.getOwnPropertyDescriptor(document, 'execCommand')

describe('copyTextToClipboard', () => {
  afterEach(() => {
    restoreClipboard()
    restoreExecCommand()
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('uses the async clipboard API when available', async () => {
    const writeText = vi.fn(async () => undefined)
    setClipboard({ writeText })
    const execCommand = vi.fn(() => true)
    setExecCommand(execCommand)

    await expect(copyTextToClipboard('stackos')).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledWith('stackos')
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('falls back to textarea copy when async clipboard is blocked', async () => {
    const writeText = vi.fn(async () => {
      throw new DOMException('NotAllowedError')
    })
    setClipboard({ writeText })
    const execCommand = vi.fn(() => true)
    setExecCommand(execCommand)

    await expect(copyTextToClipboard('manual-url')).resolves.toBe(true)

    expect(writeText).toHaveBeenCalledWith('manual-url')
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('returns false when no copy mechanism succeeds', async () => {
    setClipboard(undefined)
    setExecCommand(vi.fn(() => false))

    await expect(copyTextToClipboard('stackos')).resolves.toBe(false)
  })
})

function setClipboard(value: { writeText: (text: string) => Promise<void> } | undefined): void {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value,
  })
}

function setExecCommand(value: (command: string) => boolean): void {
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value,
  })
}

function restoreClipboard(): void {
  if (originalClipboard) {
    Object.defineProperty(navigator, 'clipboard', originalClipboard)
  } else {
    Reflect.deleteProperty(navigator, 'clipboard')
  }
}

function restoreExecCommand(): void {
  if (originalExecCommand) {
    Object.defineProperty(document, 'execCommand', originalExecCommand)
  } else {
    Reflect.deleteProperty(document, 'execCommand')
  }
}
