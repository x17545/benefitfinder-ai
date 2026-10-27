import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compileFromFile } from 'json-schema-to-typescript';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const schemaDir = resolve(root, 'contracts/v1');
const outputDir = resolve(root, 'apps/web/src/shared/api/generated');
const checkOnly = process.argv.includes('--check');
const schemas = (await readdir(schemaDir)).filter((name) => name.endsWith('.schema.json')).sort();
const expectedFiles = new Set(schemas.map((name) => name.replace('.schema.json', '.ts')));
const failures = [];

if (!checkOnly) await mkdir(outputDir, { recursive: true });

for (const name of schemas) {
  const outputName = name.replace('.schema.json', '.ts');
  const contents = await compileFromFile(resolve(schemaDir, name), {
    bannerComment: '/* Generated from contracts/v1. Run pnpm types:generate; do not edit. */',
    $refOptions: { resolve: { http: false } },
    style: { singleQuote: true, semi: true, tabWidth: 2 },
  });
  const target = resolve(outputDir, outputName);
  if (checkOnly) {
    const existing = await readFile(target, 'utf8').catch((error) => {
      if (error.code === 'ENOENT') return null;
      throw error;
    });
    if (existing !== contents) failures.push(outputName);
  } else {
    await writeFile(target, contents);
  }
}

const actualFiles = await readdir(outputDir).catch((error) => {
  if (error.code === 'ENOENT') return [];
  throw error;
});
const unexpected = actualFiles.filter((name) => !expectedFiles.has(name));
if (unexpected.length) {
  throw new Error(`불필요한 생성 파일을 검토·제거하세요: ${unexpected.join(', ')}`);
}
if (failures.length) {
  console.error(`계약 타입이 오래됐습니다. pnpm types:generate 후 커밋하세요: ${failures.join(', ')}`);
  process.exit(1);
}
console.log(`${schemas.length}개 계약 타입 ${checkOnly ? '검사' : '생성'} 완료`);
