import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packagePath = path.join(desktopDir, "package.json");
const generatedConfigPath = path.join(desktopDir, ".electron-builder.generated.json");
const generatedUpdateConfigPath = path.join(desktopDir, ".update-config.generated.json");
const updateUrl = process.env.STACKOS_UPDATE_URL;

const pkg = JSON.parse(fs.readFileSync(packagePath, "utf8"));
const config = { ...pkg.build };

if (updateUrl) {
  fs.writeFileSync(
    generatedUpdateConfigPath,
    `${JSON.stringify({ updateUrl }, null, 2)}\n`
  );
  config.extraResources = [
    ...(config.extraResources || []),
    {
      from: ".update-config.generated.json",
      to: "update-config.json"
    }
  ];
  config.publish = [
    {
      provider: "generic",
      url: updateUrl
    }
  ];
  fs.writeFileSync(generatedConfigPath, `${JSON.stringify(config, null, 2)}\n`);
}

const command = process.platform === "win32" ? "electron-builder.cmd" : "electron-builder";
const args = ["--mac"];
if (updateUrl) {
  args.push("--config", generatedConfigPath);
}

const result = spawnSync(command, args, {
  cwd: desktopDir,
  stdio: "inherit",
  shell: false
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
