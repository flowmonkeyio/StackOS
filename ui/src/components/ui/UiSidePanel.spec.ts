import { mount } from '@vue/test-utils';
import { defineComponent, nextTick, ref } from 'vue';
import { afterEach, describe, expect, it } from 'vitest';

import UiSidePanel from './UiSidePanel.vue';

const Host = defineComponent({
  components: { UiSidePanel },
  setup() {
    const parentOpen = ref(false);
    const childOpen = ref(false);
    return { parentOpen, childOpen };
  },
  template: `
    <button id="open-parent" @click="parentOpen = true">Open parent</button>
    <UiSidePanel v-model="parentOpen" title="Parent panel">
      <button id="open-child" @click="childOpen = true">Open child</button>
    </UiSidePanel>
    <UiSidePanel v-model="childOpen" title="Child panel" />
  `,
});

function panels(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll<HTMLElement>('.ui-sidepanel'));
}

function panelBodies(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll<HTMLElement>('.ui-sidepanel__body'));
}

async function clickBodyButton(selector: string) {
  const button = document.body.querySelector<HTMLElement>(selector);
  expect(button).not.toBeNull();
  button?.click();
  await nextTick();
}

describe('UiSidePanel', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    document.body.style.overflow = '';
  });

  it('scrolls the body by default', () => {
    const wrapper = mount(UiSidePanel, {
      props: { modelValue: true, title: 'Panel' },
      slots: { default: '<div style="height: 1200px">Long content</div>' },
      attachTo: document.body,
    });

    expect(panelBodies()[0].classList.contains('overflow-y-auto')).toBe(true);

    wrapper.unmount();
  });

  it('allows body scrolling to be disabled explicitly', () => {
    const wrapper = mount(UiSidePanel, {
      props: { modelValue: true, title: 'Panel', scrollBody: false },
      attachTo: document.body,
    });

    expect(panelBodies()[0].classList.contains('overflow-y-auto')).toBe(false);

    wrapper.unmount();
  });

  it('uses unique accessible title ids for multiple open panels', async () => {
    const wrapper = mount(Host, { attachTo: document.body });

    await clickBodyButton('#open-parent');
    await clickBodyButton('#open-child');

    const [parent, child] = panels();
    expect(parent.getAttribute('aria-labelledby')).toMatch(/^ui-sidepanel-title-/);
    expect(child.getAttribute('aria-labelledby')).toMatch(/^ui-sidepanel-title-/);
    expect(parent.getAttribute('aria-labelledby')).not.toBe(child.getAttribute('aria-labelledby'));

    wrapper.unmount();
  });

  it('lets Escape close only the top side panel in a stack', async () => {
    const wrapper = mount(Host, { attachTo: document.body });

    await clickBodyButton('#open-parent');
    await clickBodyButton('#open-child');

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await nextTick();

    expect(panels()).toHaveLength(1);
    expect(panels()[0].textContent).toContain('Parent panel');

    wrapper.unmount();
  });

  it('restores focus to the opener when closed', async () => {
    const wrapper = mount(Host, { attachTo: document.body });
    const opener = document.body.querySelector<HTMLElement>('#open-parent');
    opener?.focus();

    await clickBodyButton('#open-parent');

    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close panel');

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await nextTick();

    expect(document.activeElement).toBe(opener);

    wrapper.unmount();
  });

  it('focuses the panel itself when custom content has no focusable controls', async () => {
    const wrapper = mount(UiSidePanel, {
      props: { modelValue: true, title: 'Read-only panel', hideClose: true },
      slots: { default: '<p>Read-only content</p>' },
      attachTo: document.body,
    });

    await nextTick();

    const panel = panels()[0];
    expect(panel.getAttribute('tabindex')).toBe('-1');
    expect(document.activeElement).toBe(panel);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
    expect(document.activeElement).toBe(panel);

    wrapper.unmount();
  });
});
