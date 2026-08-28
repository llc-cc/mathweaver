const { app, BrowserWindow, dialog, shell } = require("electron");
const { execFileSync, spawn } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");

const BACKEND_HOST = "127.0.0.1";
const DEFAULT_BACKEND_PORT = 8000;
let backendPort = DEFAULT_BACKEND_PORT;
let backendProcess = null;
let mainWindow = null;
let logFile = path.join(path.dirname(process.execPath), "mathweaver-desktop.log");

function log(message) {
  try {
    if (!logFile) return;
    fs.appendFileSync(logFile, `[${new Date().toISOString()}] ${message}\n`);
  } catch (_) {
    // Logging must never break startup.
  }
}

log("main module loaded");
log(`process.type: ${process.type || "browser"}`);
log(`app available: ${Boolean(app && app.whenReady)}`);

process.on("uncaughtException", (error) => {
  log(`uncaughtException: ${error.stack || error.message}`);
});

process.on("unhandledRejection", (reason) => {
  log(`unhandledRejection: ${reason && reason.stack ? reason.stack : reason}`);
});

setTimeout(() => {
  log(`timer tick; app.isReady=${app.isReady()}`);
}, 1000);

const keepAlive = setInterval(() => {
  log(`keepalive; app.isReady=${app.isReady()}`);
}, 1000);

function backendExecutable() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend", "backend.exe");
  }
  return path.join(__dirname, "..", "dist", "backend", "backend.exe");
}

function backendUrl(pathname = "") {
  return `http://${BACKEND_HOST}:${backendPort}${pathname}`;
}

function openHttpsExternal(targetUrl) {
  try {
    const parsedUrl = new URL(targetUrl);
    if (parsedUrl.protocol !== "https:") {
      log(`blocked non-HTTPS external URL: ${parsedUrl.protocol}`);
      return;
    }
    shell.openExternal(parsedUrl.toString()).catch((error) => {
      log(`external URL open failed: ${error.message}`);
    });
  } catch (error) {
    log(`blocked invalid external URL: ${error.message}`);
  }
}

function canListen(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, BACKEND_HOST);
  });
}

async function chooseBackendPort() {
  for (let port = DEFAULT_BACKEND_PORT; port < DEFAULT_BACKEND_PORT + 20; port += 1) {
    if (await canListen(port)) return port;
  }
  throw new Error("No available localhost port found for backend startup");
}

function startBackend(port) {
  const exe = backendExecutable();
  const dataDir = path.join(app.getPath("userData"), "backend-data");
  const localAppData = process.env.LOCALAPPDATA || path.join(app.getPath("userData"), "..", "Local");
  const ocrDir = path.join(localAppData, "MathWeaver", "ocr");
  const ocrManifest = app.isPackaged
    ? path.join(process.resourcesPath, "ocr", "manifest.json")
    : path.join(__dirname, "..", "backend", "assets", "ocr", "manifest.json");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(ocrDir, { recursive: true });
  log(`starting backend: ${exe}`);
  log(`backend port: ${port}`);
  log(`backend exists: ${fs.existsSync(exe)}`);
  log(`backend data directory: ${dataDir}`);
  const spawnedBackend = spawn(exe, [], {
    cwd: path.dirname(exe),
    env: {
      ...process.env,
      MATHGRAPH_PORT: String(port),
      MATHGRAPH_DATA_DIR: dataDir,
      MATHGRAPH_PARENT_PID: String(process.pid),
      MATHWEAVER_OCR_DIR: ocrDir,
      MATHWEAVER_OCR_MANIFEST: ocrManifest,
    },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendProcess = spawnedBackend;

  spawnedBackend.stdout.on("data", (chunk) => log(`backend stdout: ${chunk}`));
  spawnedBackend.stderr.on("data", (chunk) => log(`backend stderr: ${chunk}`));
  spawnedBackend.on("error", (error) => {
    log(`backend spawn error: ${error.stack || error.message}`);
  });
  spawnedBackend.on("exit", (code, signal) => {
    log(`backend exited: code=${code}, signal=${signal}`);
    if (backendProcess === spawnedBackend) {
      backendProcess = null;
    }
  });
}

async function waitForBackend(timeoutMs = 60000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (!backendProcess) {
      throw new Error("Backend process exited before becoming healthy");
    }
    try {
      const response = await fetch(backendUrl("/health"));
      if (response.ok) return;
    } catch (_) {
      // Backend is still booting.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Backend did not become healthy within ${timeoutMs}ms`);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 700,
    title: "MathWeaver",
    icon: app.isPackaged
      ? path.join(process.resourcesPath, "mathweaver-icon.png")
      : path.join(__dirname, "..", "public", "mathweaver-icon.png"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    openHttpsExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    try {
      if (new URL(url).origin === new URL(backendUrl()).origin) return;
    } catch (_) {
      // Invalid navigation targets are blocked below.
    }
    event.preventDefault();
    openHttpsExternal(url);
  });

  mainWindow.once("close", () => {
    log("main window closing; stopping backend");
    stopBackend();
  });
  mainWindow.once("closed", () => {
    mainWindow = null;
    log("main window closed");
  });

  mainWindow.loadURL(backendUrl());
}

function stopBackend() {
  const processToStop = backendProcess;
  backendProcess = null;
  if (!processToStop?.pid) return;
  try {
    if (process.platform === "win32") {
      // backend.exe can own child processes on Windows. Kill only this known
      // process tree so closing MathWeaver never leaves a stale localhost server.
      execFileSync("taskkill.exe", ["/pid", String(processToStop.pid), "/t", "/f"], {
        stdio: "ignore",
        windowsHide: true,
        timeout: 5000,
      });
    } else if (!processToStop.killed) {
      processToStop.kill();
    }
    log(`backend stopped: ${processToStop.pid}`);
  } catch (error) {
    log(`backend stop error: ${error.message}`);
    try {
      if (!processToStop.killed) {
        processToStop.kill();
        log(`backend direct-stop fallback sent: ${processToStop.pid}`);
      }
    } catch (fallbackError) {
      log(`backend direct-stop fallback error: ${fallbackError.message}`);
    }
  }
}

app.whenReady().then(async () => {
  clearInterval(keepAlive);
  log("electron ready");
  log(`resourcesPath: ${process.resourcesPath}`);
  try {
    backendPort = await chooseBackendPort();
    startBackend(backendPort);
    await waitForBackend();
    log("backend healthy");
    createWindow();
  } catch (error) {
    log(`startup failed: ${error.stack || error.message}`);
    dialog.showErrorBox("MathWeaver 启动失败", error.message);
    app.quit();
  }
});

app.on("window-all-closed", () => {
  log("all windows closed; exiting Electron process");
  stopBackend();
  app.exit(0);
});

app.on("before-quit", stopBackend);
app.on("will-quit", () => {
  log("electron will-quit");
  stopBackend();
});
process.once("exit", stopBackend);
