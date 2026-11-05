import { defineComponent, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { DOMWrapper, mount } from '@vue/test-utils'

import UiDialog from './UiDialog.vue'
import UiSidePanel from './UiSidePanel.vue'
import UiConfirmDialog from './UiConfirmDialog.vue'

const Host = defineComponent({
  components: { UiDialog },
  setup() {
    const parentOpen = ref(false)
    const childOpen = ref(false)
    return { parentOpen, childOpen }
  },
  template: `
    <button id="open-parent" @click="parentOpen = true">Open parent</button>
    <UiDialog v-model="parentOpen" title="Parent dialog">
      <button id="open-child" @click="childOpen = true">Open child</button>
    </UiDialog>
    <UiDialog v-model="childOpen" title="Child dialog" />
  `,
})

function dialogs(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll<HTMLElement>('[role="dialog"]'))
}

describe('UiDialog', () => {
  it('opens, closes and reopens with a Teleport stub without render-driven stack registration', async () => {
    const w = mount(defineComponent({
      components: { UiConfirmDialog },
      setup: () => ({ open: ref(false) }),
      template: '<button id="open-parent" @click="open = true">Open confirmation</button><UiConfirmDialog v-model="open" title="Detach account?" :description="open ? \'This account remains reusable.\' : undefined" />',
    }), { attachTo: document.body, global: { stubs: { teleport: true } } })
    document.body.style.overflow = 'auto'
    const opener = w.get('#open-parent').element as HTMLElement
    opener.focus()
    await w.get('#open-parent').trigger('click')
    await nextTick()
    expect(dialogs()).toHaveLength(1)
    expect(document.body.style.overflow).toBe('hidden')
    await w.get('[aria-label="Close dialog"]').trigger('click')
    await nextTick()
    expect(dialogs()).toHaveLength(0)
    expect(document.body.style.overflow).toBe('auto')
    expect(document.activeElement).toBe(opener)
    await w.get('#open-parent').trigger('click')
    await nextTick()
    expect(dialogs()).toHaveLength(1)
    expect(Number(dialogs()[0].style.zIndex)).toBeGreaterThan(1100)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(dialogs()).toHaveLength(0)
    expect(document.body.style.overflow).toBe('auto')
    w.unmount()
    document.body.style.overflow = ''
  })

  it('mounts its backdrop outside a spaced scrolling parent and restores its opener', async () => {
    const w = mount(defineComponent({
      components: { UiDialog },
      setup: () => ({ open: ref(false) }),
      template: '<main class="space-y-4" style="overflow:auto;transform:translateX(0)"><button id="opener" @click="open = true">Open</button><UiDialog v-model="open" title="Detail" layout="detail" /></main>',
    }), { attachTo: document.body })
    const opener = w.get('#opener').element as HTMLElement
    opener.focus()
    opener.click()
    await nextTick()
    const overlay = document.querySelector('.ui-dialog__overlay')!
    expect(document.body.contains(overlay)).toBe(true)
    expect(overlay.closest('main')).toBeNull()
    expect(w.find('.ui-dialog__overlay').exists()).toBe(false)
    document.querySelector<HTMLButtonElement>('[aria-label="Close dialog"]')!.click()
    await nextTick()
    expect(document.activeElement).toBe(opener)
    w.unmount()
  })

  it('keeps a confirmation above its drawer with top-only keyboard handling and scroll lock', async () => {
    const w = mount(defineComponent({
      components: { UiDialog, UiSidePanel },
      setup: () => ({ drawer: ref(false), confirm: ref(false) }),
      template: '<button id="drawer-opener" @click="drawer = true">Open drawer</button><UiSidePanel v-model="drawer" title="Drawer"><button id="confirm-opener" @click="confirm = true">Confirm action</button></UiSidePanel><UiDialog v-model="confirm" title="Confirmation"><button id="confirm-last">Confirm</button></UiDialog>',
    }), { attachTo: document.body })
    document.body.style.overflow = 'auto'
    await w.get('#drawer-opener').trigger('click')
    await nextTick()
    const opener = document.querySelector<HTMLButtonElement>('#confirm-opener')!
    opener.focus()
    opener.click()
    await nextTick()
    const [drawer, confirm] = dialogs()
    expect.soft(Number(confirm.style.zIndex)).toBeGreaterThan(Number(drawer.style.zIndex))
    document.querySelector<HTMLButtonElement>('#confirm-last')!.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect.soft(document.activeElement?.getAttribute('aria-label')).toBe('Close dialog')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect.soft(dialogs()).toHaveLength(1)
    expect.soft(dialogs()[0]?.textContent).toContain('Drawer')
    expect.soft(document.activeElement).toBe(opener)
    expect.soft(document.body.style.overflow).toBe('hidden')
    w.unmount()
    document.body.style.overflow = ''
  })

  it('opts into the bounded detail frame without changing the default layout', () => {
    const normal = mount(UiDialog, { props: { modelValue: true, title: 'Normal' } })
    const normalFrame = new DOMWrapper(dialogs().at(-1)!)
    const detail = mount(UiDialog, { props: { modelValue: true, title: 'Detail', layout: 'detail', size: 'lg' } })
    const detailFrame = new DOMWrapper(dialogs().at(-1)!)
    expect(normalFrame.classes()).not.toContain('ui-dialog--detail')
    expect(detailFrame.classes()).toContain('ui-dialog--detail')
    expect(detailFrame.classes()).toContain('max-w-2xl')
    const close = detailFrame.get('[aria-label="Close dialog"]')
    expect(close.classes()).toContain('h-8')
    expect(close.classes()).toContain('w-8')
    expect(close.classes()).toContain('shrink-0')
    expect(close.get('svg').classes()).toEqual(expect.arrayContaining(['h-4', 'w-4']))
    normal.unmount()
    detail.unmount()
  })

  it('excludes retained hidden panes and closed disclosures from keyboard focus trapping', async () => {
    const w = mount(UiDialog, {
      props: { modelValue: true, title: 'Inspection' },
      slots: { default: '<div hidden inert><button id="hidden-control">Hidden</button></div><details><summary>Trace</summary><button id="closed-control">Closed</button></details><button id="last-visible">Last</button>' },
    })
    await nextTick()
    w.vm.focusTitle()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement?.id).toBe('last-visible')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close dialog')
    w.unmount()
  })

  it('stacks newly opened dialogs above existing dialogs', async () => {
    const w = mount(Host, { attachTo: document.body })

    await w.find('#open-parent').trigger('click')
    await nextTick()
    document.querySelector<HTMLElement>('#open-child')!.click()
    await nextTick()

    const [parent, child] = dialogs()
    expect(parent.textContent).toContain('Parent dialog')
    expect(child.textContent).toContain('Child dialog')
    expect(Number(child.style.zIndex)).toBeGreaterThan(Number(parent.style.zIndex))

    w.unmount()
  })

  it('lets Escape close only the top dialog in a stack', async () => {
    const w = mount(Host, { attachTo: document.body })

    await w.find('#open-parent').trigger('click')
    await nextTick()
    document.querySelector<HTMLElement>('#open-child')!.click()
    await nextTick()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()

    expect(dialogs()).toHaveLength(1)
    expect(dialogs()[0].textContent).toContain('Parent dialog')

    w.unmount()
  })
})
