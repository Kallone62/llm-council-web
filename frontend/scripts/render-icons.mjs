// Rasterize the one canonical SVG into Windows icon inputs; no manual redraws.
// Run from frontend/: npm install --no-save --no-package-lock sharp@0.34.4 && node scripts/render-icons.mjs
import sharp from 'sharp';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const destination = path.resolve(frontend, '..', 'build', 'brand');
const svg = path.join(frontend, 'public', 'llm-council.svg');
await mkdir(destination, { recursive: true });

for (const size of [16, 24, 32, 48, 64, 128, 256]) {
  await sharp(svg, { density: 384 })
    .resize(size, size)
    .png()
    .toFile(path.join(destination, `icon-${size}.png`));
}

console.log('Generated canonical SVG-based PNG icons at', destination);
