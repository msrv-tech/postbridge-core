import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..', 'src')
const allowed = new Set([
  path.join(root, 'i18n', 'catalogs.js'),
  path.join(root, 'mdEditorRu.js'),
])
const extensions = new Set(['.js', '.jsx'])
const cyrillic = /[А-Яа-яЁё]/
const stringLiteral = /(["'`])(?:\\.|(?!\1)[\s\S])*?\1/g

function walk(dir) {
  const result = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      result.push(...walk(fullPath))
    } else if (extensions.has(path.extname(entry.name))) {
      result.push(fullPath)
    }
  }
  return result
}

const violations = []

for (const file of walk(root)) {
  if (allowed.has(file)) continue
  const source = readFileSync(file, 'utf8')
  for (const match of source.matchAll(stringLiteral)) {
    const literal = match[0]
    if (!cyrillic.test(literal)) continue
    const line = source.slice(0, match.index).split('\n').length
    violations.push(`${path.relative(path.resolve(import.meta.dirname, '..'), file)}:${line}: ${literal.slice(0, 100)}`)
  }
}

if (violations.length) {
  console.error('Cyrillic UI strings must live in src/i18n/catalogs.js:')
  for (const violation of violations) console.error(`- ${violation}`)
  process.exit(1)
}

console.log('i18n boundary check passed')
