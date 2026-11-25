"""One-time: build vendor templates from sample files saved by each vendor's software.
Usage: python3 extract_templates.py demo.xcs demo.xs Solar_System.xcs Solar_System.xs lantern.lac h2d.lac Angel.fds outdir
Only settings/preset data is kept; no model geometry from the samples is copied."""
import base64, json, sys, zipfile, zlib, struct, copy, os

demo_xcs, demo_xs, solar_xcs, solar_xs, lac_r1, lac_h2d, fds, out = sys.argv[1:]
w = lambda name, obj: json.dump(obj, open(os.path.join(out, name), 'w'), indent=1, ensure_ascii=False)

def native_profiles(xsz):
    profs = json.loads(xsz.read('profiles.json'))['profiles']
    base = {}
    for pid, p in sorted(profs.items(), key=lambda kv: not kv[0].startswith('profile:')):
        base.setdefault(p['processingType'], {k: v for k, v in p['values'].items() if k not in ('materialId', 'processingType')})
    return base

# ---- xTool P2S (2D laser plane)
d = json.load(open(demo_xcs)); xz = zipfile.ZipFile(demo_xs)
cm = d['device']['data']['value'][0][1]
params = {}
for _, e in cm['displays']['value']:
    for k, v in e['data'].items():
        v = copy.deepcopy(v); v['parameter']['official'].pop('materialId', None); params.setdefault(k, v)
proj = json.loads(xz.read('project.json'))
w('xtool-p2s.json', {
    'label': 'xTool P2S (55 W CO2)', 'kind': '2d', 'version': d['version'], 'ua': d['ua'],
    'minRequiredVersion': d['minRequiredVersion'], 'extId': d['extId'], 'extName': d['extName'],
    'device': {k: d['device'][k] for k in ('id', 'deviceCode', 'extId', 'extName', 'power')},
    'mode': cm['mode'], 'modeData': cm['data'][cm['mode']], 'extendInfo': d['canvas'][0]['extendInfo'],
    'params': {k: params[k] for k in ('VECTOR_CUTTING', 'VECTOR_ENGRAVING', 'FILL_VECTOR_ENGRAVING')},
    'xs': {'versionInfo': proj['versionInfo'], 'persistenceMeta': json.loads(xz.read('meta/persistence-meta.json')),
           'format': xz.read('.format').decode(), 'baseProfiles': native_profiles(xz)},
})

# ---- xTool F2 Ultra UV (3D inner engraving)
s = json.load(open(solar_xcs)); sz = zipfile.ZipFile(solar_xs)
sc = s['canvas'][0]; sm = s['device']['data']['value'][0][1]; disp = sc['displays'][0]
entry = sm['displays']['value'][0][1]
proj = json.loads(sz.read('project.json'))
w('xtool-f2uuv-inner.json', {
    'label': 'xTool F2 Ultra UV (inner engraving)', 'kind': '3d', 'version': s['version'], 'ua': s['ua'],
    'minRequiredVersion': s['minRequiredVersion'], 'extId': s['extId'], 'extName': s['extName'],
    'device': {k: s['device'][k] for k in ('id', 'deviceCode', 'extId', 'extName', 'power')},
    'mode': sm['mode'], 'modeData': sm['data'][sm['mode']], 'extendInfo': sc['extendInfo'],
    'params': entry['data'], 'activeType': entry['processingType'],
    'display': {k: v for k, v in disp.items() if k not in ('modelData', 'data', 'processingType', 'customData', 'userData')},
    'xs': {'versionInfo': proj['versionInfo'], 'persistenceMeta': json.loads(sz.read('meta/persistence-meta.json')),
           'format': sz.read('.format').decode(), 'baseProfiles': native_profiles(sz)},
})

# ---- Bambu Suite machines
def bambu(path, fname, label):
    z = zipfile.ZipFile(path)
    ps = json.loads(z.read('Metadata2D/project_settings.json'))
    machine = ps['project_settings']['machine_settings_name']
    b = ps['canvas_settings'][0]['making_batch_list'][0]
    mat = b['material_settings_name']
    files = {}
    for n in z.namelist():
        base = n.split('/')[-1]
        if n.startswith('Metadata2D/') and not base.endswith('.png') and (base == machine + '.config' or base.startswith(mat)):
            data = z.read(n)
            files[base] = {'b64': base64.b64encode(data).decode()} if base.endswith('.png') else {'text': data.decode('utf-8')}
    m2d = json.loads(z.read('2D/2dmodel.json'))
    w(fname, {'label': label, 'kind': '2d', 'application': m2d['Application'], 'fileVersion': m2d['FileVersion'],
              'machine': machine, 'material': {'id': b['material_id'], 'name': b['material_name'], 'settingsName': mat},
              'batchSettings': b['batch_settings'], 'projectSettings': ps['project_settings'], 'files': files,
              'contentTypes': z.read('[Content_Types].xml').decode()})

bambu(lac_r1, 'bambu-r1-55w.json', 'Bambu Lab R1 (55 W)')
bambu(lac_h2d, 'bambu-h2d-10w.json', 'Bambu Lab H2D (10 W laser)')

# ---- Creality Falcon (crystal)
raw = open(fds, 'rb').read(); i = 0; blocks = []
while i < len(raw):
    n = struct.unpack('<I', raw[i:i + 4])[0]; blob = raw[i + 4:i + 4 + n]
    blocks.append(json.loads(zlib.decompress(blob[4:]))); i += 4 + n
ce = blocks[2]['crtystalEngrave']
md = copy.deepcopy(ce['ModelData'][0])
for k in ('source', 'sourceAssetId', 'name'): md.pop(k, None)
w('falcon-t1.json', {'label': 'Creality Falcon T1 (crystal)', 'kind': '3d', 'device': blocks[0], 'project': blocks[1],
                     'engraveParm': ce['EngraveParm'], 'modelData': md, 'blockKeys': list(blocks[2].keys())})
print('ok', [len(b) for b in blocks])
