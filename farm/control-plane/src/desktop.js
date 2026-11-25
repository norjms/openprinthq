// Open in OrcaSlicer (B49). The OpenPrintHQ installer can add OrcaSlicer as a desktop in the browser, shared by
// everyone or one for each person. It mounts each desktop's inbox here too (OPHQ_ORCASLICER_INBOX): the shared
// desktop's at <inbox>/shared, a person's at <inbox>/people/<their login>. A file put there, followed by a
// marker ".open-<name>", opens in that desktop's OrcaSlicer (the desktop's own watcher takes each marker once).
// A person's file only ever goes to their own desktop: the folder is named by the login the proxy vouches for.
import { mkdir, writeFile, rename, chmod } from 'node:fs/promises';
import { join } from 'node:path';

const LOGIN = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
const OPENABLE = /\.(3mf|stl|obj|step|stp|amf|svg|gcode)$/i;

export function desktopSettings(env = process.env) {
  return {
    inbox: String(env.OPHQ_ORCASLICER_INBOX || '').replace(/\/+$/, ''),
    perPerson: env.OPHQ_ORCASLICER_DESKTOPS === 'per-person'
  };
}

// The inbox a person's file goes to, or null when there is none for them.
export function inboxFor(settings, login) {
  if (!settings.inbox) return null;
  if (!settings.perPerson) return join(settings.inbox, 'shared');
  const who = String(login || '').toLowerCase();
  if (!LOGIN.test(who) || who.includes('..')) return null;
  return join(settings.inbox, 'people', who);
}

// A file name that is safe in a folder and keeps what OrcaSlicer opens it by: no folders, no leading dot (the
// markers'), and a model's extension.
export function inboxName(name, fallback = 'model.3mf') {
  let n = String(name || '').split(/[\\/]/).pop().replace(/[\u0000-\u001f\u007f]/g, '').trim();
  n = n.replace(/^\.+/, '');
  if (n.length > 180) {
    const ext = (n.match(/\.[a-z0-9]{1,5}$/i) || [''])[0];
    n = n.slice(0, 180 - ext.length) + ext;
  }
  if (!n || !OPENABLE.test(n)) return null;
  return n || fallback;
}

// Writes the file, then its marker. Both, and the folder, are open to the desktop's own user, which takes the
// marker away and may tidy the folder.
export async function sendToInbox(dir, name, bytes) {
  await mkdir(dir, { recursive: true, mode: 0o777 });
  await chmod(dir, 0o777).catch(() => {});
  const tmp = join(dir, '.part-' + name);
  await writeFile(tmp, bytes, { mode: 0o666 });
  await chmod(tmp, 0o666).catch(() => {});
  await rename(tmp, join(dir, name));
  const marker = join(dir, '.open-' + name);
  await writeFile(marker, '', { mode: 0o666 });
  await chmod(marker, 0o666).catch(() => {});
}
