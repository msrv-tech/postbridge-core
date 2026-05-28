import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync, writeFileSync, copyFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const bundlesByPlatform = {
  linux: 'deb,rpm,appimage',
  win32: 'nsis',
}

const bundles = bundlesByPlatform[process.platform]

if (!bundles) {
  console.error(`Postbridge Desktop builds are currently supported on Linux and Windows only. Unsupported platform: ${process.platform}`)
  process.exit(1)
}

const result = spawnSync('tauri', ['build', '--bundles', bundles], {
  shell: process.platform === 'win32',
  stdio: 'inherit',
})

if (result.status) {
  process.exit(result.status)
}

if (process.platform === 'win32') {
  fixWindowsWebViewLoaderBundle()
}

process.exit(0)

function fixWindowsWebViewLoaderBundle() {
  const desktopDir = dirname(dirname(fileURLToPath(import.meta.url)))
  const tauriDir = join(desktopDir, 'src-tauri')
  const releaseDir = join(tauriDir, 'target', 'release')
  const nsisDir = join(releaseDir, 'nsis', 'x64')
  const bundleDir = join(releaseDir, 'bundle', 'nsis')
  const nsiPath = join(nsisDir, 'installer.nsi')
  const loaderPath = join(releaseDir, 'WebView2Loader.dll')
  const runtimePath = join(desktopDir, 'runtime')

  if (!existsSync(nsiPath) || !existsSync(loaderPath)) {
    console.error('Windows NSIS bundle is missing installer.nsi or WebView2Loader.dll')
    process.exit(1)
  }
  if (!existsSync(runtimePath)) {
    console.error(`Windows NSIS bundle is missing desktop runtime directory at ${runtimePath}`)
    process.exit(1)
  }

  let nsi = readFileSync(nsiPath, 'utf8')
  if (!nsi.includes('WebView2Loader.dll')) {
    nsi = nsi.replace(
      '  File "${MAINBINARYSRCPATH}"',
      `  File "\${MAINBINARYSRCPATH}"\r\n  File "${loaderPath}"`,
    )
  }
  if (!nsi.includes(`${runtimePath}\\*.*`) && !nsi.includes(`${runtimePath}\\\\*.*`)) {
    nsi = nsi.replace(
      `  File "${loaderPath}"`,
      `  File "${loaderPath}"\r\n  File /r "${runtimePath}"`,
    )
  }
  writeFileSync(nsiPath, nsi, 'utf8')

  const makensis = findMakensis()
  const nsisResult = spawnSync(makensis, ['installer.nsi'], {
    cwd: nsisDir,
    shell: false,
    stdio: 'inherit',
  })
  if (nsisResult.status) {
    process.exit(nsisResult.status)
  }

  const setup = readdirSync(bundleDir).find((name) => name.endsWith('-setup.exe'))
  if (!setup) {
    console.error(`Could not find NSIS setup exe in ${bundleDir}`)
    process.exit(1)
  }
  copyFileSync(join(nsisDir, 'nsis-output.exe'), join(bundleDir, setup))
}

function findMakensis() {
  const localAppData = process.env.LOCALAPPDATA
  const candidates = [
    process.env.MAKENSIS,
    localAppData ? join(localAppData, 'tauri', 'NSIS', 'makensis.exe') : null,
    localAppData ? join(localAppData, 'tauri', 'NSIS', 'Bin', 'makensis.exe') : null,
  ].filter(Boolean)

  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate
  }

  console.error('makensis.exe was not found after Tauri NSIS bundling')
  process.exit(1)
}
