import { access, readFile } from 'node:fs/promises'
import path from 'node:path'

const outputRoots = [
  path.resolve(process.cwd(), '../agent_platform/web/static'),
  path.resolve(process.cwd(), '../agent_platform/web/static-parallel'),
]

function fail(message) {
  console.error(`[parallel-artifact-check] FAIL: ${message}`)
  process.exitCode = 1
}

async function verifyArtifact(root) {
  const indexPath = path.join(root, 'index.html')
  try {
    await access(indexPath)
  } catch {
    fail(`missing build entry: ${indexPath}`)
    return
  }

  const html = await readFile(indexPath, 'utf8')
  if (!/<html[\\s>]/i.test(html) || !/<div[^>]+id=["']root["']/i.test(html)) {
    fail(`invalid Vite application entry: ${indexPath}`)
  }

  const assetPaths = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)]
    .map((match) => match[1])
    .filter((asset) => asset.startsWith('/app/'))

  if (assetPaths.length === 0) {
    fail(`no /app/ asset references found in ${indexPath}`)
    return
  }

  for (const asset of assetPaths) {
    const assetPath = path.resolve(root, `.${asset.slice('/app'.length)}`)
    if (!assetPath.startsWith(`${root}${path.sep}`)) {
      fail(`asset escapes artifact directory: ${asset}`)
      continue
    }
    try {
      await access(assetPath)
    } catch {
      fail(`referenced asset is missing: ${assetPath}`)
    }
  }
}

if (outputRoots[0] === outputRoots[1]) {
  fail('production and parallel output roots must be different')
} else {
  await Promise.all(outputRoots.map(verifyArtifact))
}

if (process.exitCode) {
  process.exit(process.exitCode)
}
console.log('[parallel-artifact-check] both production and parallel artifacts are self-contained')
