// Sample data for the screen checks: a four-printer farm, its queue, spools,
// files and history. Every screen is rendered against this, so a layout that
// only holds up when a page is empty does not pass.
// SPDX-License-Identifier: AGPL-3.0-or-later

const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const H = 3600e3;

export const printers = [
  { id: 1, name: 'H2D-01', model: 'H2D', connection_type: 'bambu', serial_number: '0948AD512300041', ip_address: '192.0.2.21', nozzle_count: 2, location: 'Print room' },
  { id: 2, name: 'MK4S-01', model: 'MK4S', connection_type: 'prusalink', serial_number: 'CZPX4021X004XK11342', ip_address: '192.0.2.22', nozzle_count: 1, location: 'Print room' },
  { id: 3, name: 'MK3S-01', model: 'MK3S+ MMU3', connection_type: 'octoprint', serial_number: 'CZPX1220X003XC28914', ip_address: '192.0.2.23', nozzle_count: 1, location: 'Print room' },
  { id: 4, name: 'V24-01', model: 'Voron 2.4', connection_type: 'klipper', serial_number: 'V24-350-0117', ip_address: '192.0.2.24', nozzle_count: 1, location: 'Garage' }
];

const tray = (type, color, remain) => ({ tray_type: type, tray_color: color + 'FF', remain, tray_sub_brands: type, nozzle_temp_min: 190, nozzle_temp_max: 240 });

export const status = {
  1: {
    connected: true, state: 'RUNNING', name: 'H2D-01', progress: 62, layer_num: 148, total_layers: 238, remaining_time: 135,
    subtask_name: 'Gear housing, plate 2 of 3', gcode_file: 'gear_housing.3mf',
    temperatures: { nozzle: 219.6, nozzle_target: 220, nozzle_2: 218.9, nozzle_2_target: 220, bed: 55.1, bed_target: 55, chamber: 34 },
    nozzles: [{ nozzle_type: 'hardened_steel', nozzle_diameter: '0.4' }, { nozzle_type: 'hardened_steel', nozzle_diameter: '0.4' }],
    supports_chamber_heater: true, supports_drying: true, chamber_light: true, speed_level: 2,
    cooling_fan_speed: 100, big_fan1_speed: 40, big_fan2_speed: 30, airduct_mode: 0,
    wifi_signal: -48, firmware_version: '01.02.00.00', hms_errors: [],
    ams: [
      { id: 0, humidity: 3, temp: 27, module_type: 'n3f', tray: [tray('PLA', '1F1F1F', 72), tray('PLA', 'F2F2F2', 45), tray('PETG', '2E6FD8', 88), tray('PLA', 'E8742A', 19)] },
      { id: 1, humidity: 2, temp: 26, module_type: 'n3f', tray: [tray('ASA', '3A3A3A', 64), tray('PETG', '9BD1A8', 100), {}, tray('PLA', 'C0392B', 33)] }
    ],
    ams_extruder_map: { 0: 1, 1: 0 }, tray_now: 2,
    vt_tray: [tray('TPU', 'FFFFFF', 50)]
  },
  2: {
    connected: true, state: 'IDLE', name: 'MK4S-01', progress: 0, awaiting_plate_clear: false,
    temperatures: { nozzle: 24.1, nozzle_target: 0, bed: 23.8, bed_target: 0 },
    firmware_version: '6.2.4'
  },
  3: {
    connected: true, state: 'PAUSE', name: 'MK3S-01', progress: 41, layer_num: 63, total_layers: 154, remaining_time: 212,
    subtask_name: 'Cable winder, slot 3 empty', gcode_file: 'cable_winder_mmu.gcode',
    temperatures: { nozzle: 214.8, nozzle_target: 215, bed: 60.2, bed_target: 60 },
    firmware_version: '3.14.1'
  },
  4: {
    connected: true, state: 'printing', name: 'V24-01', progress: 18, layer_num: 41, total_layers: 228, remaining_time: 351,
    subtask_name: 'Planter, hex, ASA-CF', gcode_file: 'planter_hex_asa_cf.gcode',
    temperatures: { nozzle: 259.7, nozzle_target: 260, bed: 104.8, bed_target: 105, chamber: 48 },
    firmware_version: 'v0.12.0-439'
  }
};

export const queue = [
  { id: 11, position: 1, status: 'pending', library_file_name: 'Gear housing, plate 3 of 3', printer_id: 1, printer_name: 'H2D-01', print_time_seconds: 3 * 3600 + 20 * 60, required_filament_types: ['PLA'], quantity: 1 },
  { id: 12, position: 2, status: 'pending', library_file_name: 'Hex planter, small', printer_id: 4, printer_name: 'V24-01', print_time_seconds: 5 * 3600, required_filament_types: ['ASA'], quantity: 2 },
  { id: 13, position: 3, status: 'pending', library_file_name: 'Cable winder', printer_id: null, target_model: 'MK3S', print_time_seconds: 2 * 3600 + 5 * 60, required_filament_types: ['PETG'], quantity: 4 },
  { id: 14, position: 4, status: 'pending', library_file_name: 'Desk caddy', printer_id: 2, printer_name: 'MK4S-01', print_time_seconds: 4 * 3600 + 40 * 60, required_filament_types: ['PLA'], quantity: 1 },
  { id: 15, position: 5, status: 'pending', library_file_name: 'Spool holder arm', printer_id: null, print_time_seconds: 90 * 60, required_filament_types: ['PETG'], quantity: 2 },
  { id: 16, position: 6, status: 'pending', library_file_name: 'Gridfinity bin 3x2', printer_id: 2, printer_name: 'MK4S-01', print_time_seconds: 2 * 3600, required_filament_types: ['PLA'], quantity: 6 }
];

export const files = [
  ['Gear housing', '3mf', 4.2], ['Hex planter, small', '3mf', 2.8], ['Cable winder', 'gcode', 1.9], ['Desk caddy', '3mf', 3.4],
  ['Spool holder arm', 'stl', 1.1], ['Gridfinity bin 3x2', '3mf', 0.9], ['Lamp shade', '3mf', 6.3], ['Phone stand', 'stl', 0.7],
  ['Keycap set', '3mf', 1.5], ['Ribbed planter', 'gcode', 5.6]
].map(([name, ext, mb], i) => ({
  id: 100 + i, filename: `${name.toLowerCase().replace(/[ ,]+/g, '_')}.${ext}`, name, file_type: ext,
  file_size: Math.round(mb * 1048576), created_at: iso((i + 1) * 26 * H), print_count: (i * 3) % 7,
  print_time_seconds: (2 + (i % 4)) * 3600, filament_used_grams: 40 + i * 17, folder_id: null, thumbnail_path: null
}));

export const spools = [
  ['Bambu Lab', 'PLA', 'Basic', 'Black', '1F1F1F', 720, 1000, 'Print room, AMS A1'],
  ['Bambu Lab', 'PLA', 'Basic', 'White', 'F2F2F2', 450, 1000, 'Print room, AMS A2'],
  ['Polymaker', 'PETG', 'PolyLite', 'Blue', '2E6FD8', 880, 1000, 'Print room, AMS A3'],
  ['Prusament', 'PLA', 'Galaxy', 'Orange', 'E8742A', 190, 1000, 'Print room, AMS A4'],
  ['Polymaker', 'ASA', 'PolyLite', 'Grey', '3A3A3A', 640, 1000, 'Garage, V24-01'],
  ['Prusament', 'PETG', '', 'Jungle green', '9BD1A8', 1000, 1000, 'Shelf 2'],
  ['Siraya Tech', 'TPU', 'Flex', 'Clear', 'FFFFFF', 500, 1000, 'Dry box'],
  ['Prusament', 'ASA', 'CF', 'Carbon', '2B2B2B', 310, 850, 'Garage, V24-01']
].map(([brand, material, subtype, colorName, color, rem, total, loc], i) => ({
  id: 200 + i, brand, material, subtype, name: `${brand} ${material} ${subtype} ${colorName}`.replace(/\s+/g, ' '),
  color_name: colorName, rgba: color + 'FF', color: '#' + color, label_weight: total, weight_used: total - rem,
  remaining_weight: rem, spool_weight: 250, cost: 24 + i, location: loc, storage_location: loc, created_at: iso(i * 50 * H)
}));

export const projects = [
  { id: 301, name: 'Shop restock, November', status: 'active', description: 'Planters and caddies for the store', color: '#8fd3ff', target_count: 40, completed_count: 23, created_at: iso(9 * 24 * H), archive_count: 23, queue_count: 4 },
  { id: 302, name: 'Gridfinity for the print room', status: 'active', description: 'Bins and baseplates', color: '#9be28f', target_count: 24, completed_count: 11, created_at: iso(20 * 24 * H), archive_count: 11, queue_count: 1 },
  { id: 303, name: 'Lamp shades, custom order', status: 'completed', description: 'Order 1042', color: '#ffb38a', target_count: 6, completed_count: 6, created_at: iso(40 * 24 * H), archive_count: 6, queue_count: 0 }
];

const outcomes = ['completed', 'completed', 'completed', 'failed', 'completed', 'completed', 'cancelled', 'completed'];
export const archives = Array.from({ length: 24 }, (_, i) => ({
  id: 400 + i, printer_id: (i % 4) + 1, filename: files[i % files.length].filename, print_name: files[i % files.length].name,
  status: outcomes[i % outcomes.length], started_at: iso((i + 1) * 5 * H), completed_at: iso((i + 1) * 5 * H - 2 * H),
  print_time_seconds: (1 + (i % 5)) * 3600, filament_used_grams: 30 + (i * 13) % 140, filament_type: ['PLA', 'PETG', 'ASA'][i % 3],
  cost: 0.6 + (i % 7) * 0.4, failure_reason: i % 8 === 3 ? 'Spaghetti detected' : null, created_at: iso((i + 1) * 5 * H),
  thumbnail_path: 'thumb.png', timelapse_path: outcomes[i % outcomes.length] === 'completed' ? 'timelapse.mp4' : null
}));

// The farm's named cameras: each printer's own, plus a chamber camera on H2D-01.
export const cameras = {
  streams: [
    ...printers.map((p) => ({ id: null, stream_name: `printer_${p.id}`, kind: 'builtin', printer_id: p.id, printer_name: p.name,
      camera_name: 'Printer camera', enabled: true, state: 'ONLINE', source: '' })),
    { id: 7, stream_name: 'printer_1_chamber', kind: 'extra', printer_id: 1, printer_name: 'H2D-01', camera_name: 'Chamber',
      enabled: true, state: 'ONLINE', source: 'rtsp://192.0.2.31:554/stream1' }
  ]
};

// The hub, at the site root, for the farm's sidebar: GET /api/nav/v1 answers its
// list (docs/nav-api.md in artisan-hub) and GET /api/stats its counts.
export const hubStats = { plate_checks: 2, jobs_failed: 0, quotes_waiting: 1, licenses_attention: 0, live_out_of_supplies: 0, open_orders: 3 };

export const stats = {
  total_prints: 412, successful_prints: 396, failed_prints: 12, cancelled_prints: 4,
  total_print_time_hours: 1863.5, total_filament_grams: 48210, total_cost: 1129.4,
  total_energy_kwh: 371.2, total_energy_cost: 55.7,
  prints_by_filament_type: { PLA: 231, PETG: 104, ASA: 61, TPU: 16 },
  prints_by_printer: { 1: 168, 2: 97, 3: 71, 4: 76 }
};

export const failures = {
  total_failures: 12, failure_rate: 2.9,
  failures_by_reason: { 'Spaghetti detected': 5, 'Filament runout': 3, 'First layer': 2, 'Cancelled by user': 2 },
  failures_by_printer: { 'H2D-01': 3, 'MK4S-01': 2, 'MK3S-01': 5, 'V24-01': 2 },
  failures_by_filament: { PLA: 4, PETG: 5, ASA: 3 }
};

export const connectors = [
  { id: 1, name: 'Print room', online: true, has_client_key: true, last_seen: iso(5e3), client: { hostname: 'print-room-pc', version: '0.1.0' } },
  { id: 2, name: 'Garage', online: true, has_client_key: true, last_seen: iso(12e3), client: { hostname: 'garage-pi', version: '0.1.0' } }
];

// The model library (the tenant's GyroidVault), as its API answers.
export const categories = [{ id: 1, name: 'Shop products' }, { id: 2, name: 'Workshop' }, { id: 3, name: 'Custom orders' }];
export const models = files.map((f, i) => ({
  id: 500 + i, name: f.name, file_count: 1 + (i % 3), print_count: f.print_count, file_types: [f.file_type],
  category_id: categories[i % 3].id, category_name: categories[i % 3].name, thumbnail: null,
  description: `${f.name}, sliced for 0.4 mm nozzles.`, source_url: '', print_tips: '', tags: [{ name: 'PLA' }],
  files: [{ id: 600 + i, filename: f.filename, file_type: f.file_type, file_size: f.file_size, url: `/library-files/${f.filename}` }],
  prints: [], created_at: f.created_at, updated_at: f.created_at
}));
