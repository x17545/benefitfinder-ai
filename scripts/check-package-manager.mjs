const userAgent = process.env.npm_config_user_agent ?? '';

if (!userAgent.startsWith('pnpm/11.25.0 ')) {
  console.error('이 프로젝트는 pnpm 11.25.0을 사용합니다. pnpm install --frozen-lockfile을 실행하세요.');
  process.exit(1);
}

if (Number(process.versions.node.split('.')[0]) !== 24) {
  console.error('Node.js 24를 설치하고 터미널을 다시 열어 주세요. .node-version을 확인하세요.');
  process.exit(1);
}
