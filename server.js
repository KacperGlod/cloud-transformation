"use strict";

// Preserve npm start while the application and processing queue live in Python.
const fs = require("node:fs");
const path = require("node:path");
const {spawn} = require("node:child_process");

const venvPython = path.join(__dirname, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const python = process.env.PYTHON || (fs.existsSync(venvPython) ? venvPython : "python");
const child = spawn(python, [path.join(__dirname, "run.py"), ...process.argv.slice(2)], {
    cwd: __dirname,
    stdio: "inherit",
    shell: false,
    windowsHide: true,
});
child.on("error", error => {
    console.error(`Nie udało się uruchomić Pythona: ${error.message}`);
    console.error("Utwórz .venv i zainstaluj requirements.txt zgodnie z README.md.");
    process.exitCode = 1;
});
child.on("exit", code => { process.exitCode = code || 0; });
for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => { if (child.exitCode === null) child.kill(signal); });
}
