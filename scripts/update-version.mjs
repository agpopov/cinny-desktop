import fs from "fs";
import path from "path";
import { execSync } from "child_process";

const version = process.argv[2];

if (!version) {
  console.error("Missing version");
  process.exit(1);
}

console.log(`Preparing release ${version}`);

// 1. Update npm versions
execSync(`npm version ${version} --no-git-tag-version`, {
  stdio: "inherit"
});
console.log(`Updated package.json and package-lock.json -> ${version}`);

// 2. Update Cargo.toml
const cargoToml = "src-tauri/Cargo.toml";
let cargoContent = fs.readFileSync(cargoToml, "utf8");

cargoContent = cargoContent.replace(
  /^version\s*=\s*".*"/m,
  `version = "${version}"`
);

fs.writeFileSync(cargoToml, cargoContent);
console.log(`Updated ${cargoToml} -> ${version}`);

// 3. Update Cargo.lock

execSync("cargo update --workspace", { cwd: "src-tauri", stdio: "inherit" });

console.log(`Updated Cargo.lock -> ${version}`);

// 4. Update tauri.conf.json
const tauriConfigPath = "src-tauri/tauri.conf.json";
const tauriConfig = JSON.parse(fs.readFileSync(tauriConfigPath));

tauriConfig.version = version;

fs.writeFileSync(
  tauriConfigPath,
  JSON.stringify(tauriConfig, null, 2) + "\n"
);
console.log(`Updated ${tauriConfigPath} -> ${version}`);

// 5. Pin the corporate web client, or an explicitly supplied release commit.
// Choosing the newest tag by commit date could silently package older code.
const webRef = process.argv[3] || "origin/corp";
execSync("git submodule update --init --recursive", { stdio: "inherit" });
execSync("git fetch origin corp --tags", { cwd: "cinny", stdio: "inherit" });
const { execFileSync } = await import("child_process");
const webCommit = execFileSync("git", ["rev-parse", "--verify", `${webRef}^{commit}`], {
  cwd: "cinny", encoding: "utf8",
}).trim();
execFileSync("git", ["checkout", "--detach", webCommit], { cwd: "cinny", stdio: "inherit" });
execSync("git add cinny", { stdio: "inherit" });
console.log(`Pinned cinny web: ${webCommit}`);
console.log("Release preparation complete");
