"use strict";

const assert = require("node:assert/strict");
const { buildApplicationMenuTemplate } = require("../src/menu-template");

function rolesFor(template, label) {
  const menu = template.find((entry) => entry.label === label);
  assert.ok(menu, `${label} menu exists`);
  return menu.submenu.map((item) => item.role).filter(Boolean);
}

const macTemplate = buildApplicationMenuTemplate({ platform: "darwin" });
const macEditRoles = rolesFor(macTemplate, "Edit");

for (const role of ["undo", "redo", "cut", "copy", "paste", "delete", "selectAll"]) {
  assert.ok(macEditRoles.includes(role), `Edit menu includes ${role}`);
}
assert.ok(macEditRoles.includes("pasteAndMatchStyle"), "macOS Edit menu includes pasteAndMatchStyle");

const linuxTemplate = buildApplicationMenuTemplate({ platform: "linux" });
const linuxEditRoles = rolesFor(linuxTemplate, "Edit");
assert.ok(linuxEditRoles.includes("copy"), "non-macOS Edit menu includes copy");
assert.ok(!linuxEditRoles.includes("pasteAndMatchStyle"), "non-macOS Edit menu omits macOS-only pasteAndMatchStyle");
