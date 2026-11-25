import JSZip from 'jszip';
import { DOMParser } from '@xmldom/xmldom';
import fs from 'node:fs';
import path from 'node:path';
globalThis.JSZip = JSZip;
globalThis.DOMParser = DOMParser;
const TPL = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../src/templates');
export const loadTemplate = async (name) => JSON.parse(fs.readFileSync(path.join(TPL, name + '.json'), 'utf8'));
