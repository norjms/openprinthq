#!/usr/bin/env python3
# Generate the OpenPrintHQ control-command framework schema + seed SQL.
# Grounded entirely in the OrcaSlicer nightly (v2.5.0-dev) code analysis:
#   - GCodeWriter.cpp per-flavor syntax (temps/fans/move/extrude/accel/PA/toolchange)
#   - DeviceManager.cpp Bambu MQTT structured commands (+ gcode_line fallbacks)
#   - PrintHost transports (Moonraker/OctoPrint/Duet/etc.)
#   - resources/profiles machine capability flags (chamber/nozzle count)
# Emits DDL + seed data + a generative cross-ref (printer x command -> supported/UNSUPPORTED).
import csv, json, sys

OUT = []
def w(s=""): OUT.append(s)

def sql_str(v):
    if v is None: return "NULL"
    return "'" + str(v).replace("'", "''") + "'"
def sql_json(obj):
    if obj is None: return "NULL"
    return "'" + json.dumps(obj, separators=(',',':')).replace("'", "''") + "'::jsonb"
def sql_bool(b):
    return "TRUE" if b else "FALSE"

# ---------------------------------------------------------------- DDL
w("-- OpenPrintHQ control-command framework (analysis-driven; OrcaSlicer nightly v2.5.0-dev)")
w("-- Idempotent. Reference/catalog tables (identical across tenants).")
w("BEGIN;")
w("""
CREATE TABLE IF NOT EXISTS ctl_comm_mechanism (
  key            TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  transport      TEXT NOT NULL,          -- mqtt | http_rest | websocket | tcp_console | serial
  gcode_passthrough BOOLEAN NOT NULL,    -- can arbitrary G-code be sent through it?
  passthrough_ref TEXT,                  -- endpoint/method for passthrough (or NULL)
  auth           TEXT,                   -- how credentials are supplied
  orca_verified  BOOLEAN NOT NULL DEFAULT TRUE,
  notes          TEXT
);

CREATE TABLE IF NOT EXISTS ctl_dialect (
  key            TEXT PRIMARY KEY,       -- command SYNTAX dialect (gcode flavor / bambu)
  name           TEXT NOT NULL,
  notes          TEXT
);

CREATE TABLE IF NOT EXISTS ctl_command (
  key            TEXT PRIMARY KEY,       -- canonical generic command the frontend sends
  category       TEXT NOT NULL,          -- temperature|fan|motion|print_control|tuning|material|light|calibration|status
  display_name   TEXT NOT NULL,
  params         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- param schema [{name,type,unit}]
  gate           TEXT,                   -- capability required: chamber|aux_fan|chamber_fan|multi_nozzle|bambu_only|NULL
  description    TEXT
);

CREATE TABLE IF NOT EXISTS ctl_command_template (
  dialect_key    TEXT NOT NULL REFERENCES ctl_dialect(key),
  command_key    TEXT NOT NULL REFERENCES ctl_command(key),
  send_method    TEXT NOT NULL,          -- gcode | mqtt_json | mqtt_gcode_line | host_rpc | rr_gcode
  template       TEXT NOT NULL,          -- concrete command w/ {placeholders}; JSON for mqtt_json
  wait_variant   TEXT,                   -- optional blocking form (e.g. M109/M190)
  orca_verified  BOOLEAN NOT NULL DEFAULT TRUE,
  source_ref     TEXT,                   -- OrcaSlicer file:line the syntax came from
  notes          TEXT,
  PRIMARY KEY (dialect_key, command_key)
);

CREATE TABLE IF NOT EXISTS ctl_printer_type (
  id             SERIAL PRIMARY KEY,
  vendor         TEXT NOT NULL,
  model          TEXT NOT NULL,
  dialect_key    TEXT NOT NULL REFERENCES ctl_dialect(key),
  mechanism_key  TEXT NOT NULL REFERENCES ctl_comm_mechanism(key),
  gcode_flavor   TEXT,                   -- raw OrcaSlicer gcode_flavor value
  nozzle_count   INTEGER NOT NULL DEFAULT 1,
  has_chamber_heater BOOLEAN NOT NULL DEFAULT FALSE,
  has_aux_fan    BOOLEAN NOT NULL DEFAULT FALSE,
  is_multi_nozzle BOOLEAN NOT NULL DEFAULT FALSE,
  popularity_rank INTEGER,               -- lower = more popular
  difficulty     TEXT,                   -- low|medium|high (to bring up in this app)
  source_profile TEXT,                   -- representative OrcaSlicer profile ref
  notes          TEXT,
  UNIQUE (vendor, model)
);

CREATE TABLE IF NOT EXISTS ctl_printer_command_support (
  printer_type_id INTEGER NOT NULL REFERENCES ctl_printer_type(id) ON DELETE CASCADE,
  command_key    TEXT NOT NULL REFERENCES ctl_command(key),
  status         TEXT NOT NULL,          -- 'supported' | 'UNSUPPORTED'
  supported      BOOLEAN NOT NULL,
  template_override TEXT,                -- per-model override (usually NULL -> resolve via dialect)
  reason         TEXT,                   -- why UNSUPPORTED
  PRIMARY KEY (printer_type_id, command_key)
);
CREATE INDEX IF NOT EXISTS ix_ctl_support_cmd ON ctl_printer_command_support(command_key);
CREATE INDEX IF NOT EXISTS ix_ctl_ptype_mech ON ctl_printer_type(mechanism_key);
""")

# ---- clean reseed of catalog tables (idempotent content) ----
for t in ["ctl_printer_command_support","ctl_command_template","ctl_command","ctl_printer_type","ctl_dialect","ctl_comm_mechanism"]:
    w(f"DELETE FROM {t};")

# ---------------------------------------------------------------- comm mechanisms
MECHS = [
 # key, name, transport, passthrough, passthrough_ref, auth, verified, notes
 ("bambu_mqtt","Bambu Lab MQTT","mqtt",True,"gcode_line tunnel {\"print\":{\"command\":\"gcode_line\"}} (DeviceManager.cpp:4650)","LAN access code / cloud token",True,"Structured JSON commands preferred; gcode_line fallback. Topic device/{id}/request (in closed BambuNetwork plugin)."),
 ("moonraker","Moonraker (Klipper)","http_rest",True,"POST /printer/gcode/script (Moonraker API; NOT used by Orca itself)","X-Api-Key optional (often none on LAN)",True,"Orca only does upload+print/start (Moonraker.cpp); full gcode passthrough exists in Moonraker and is how OpenPrintHQ drives it."),
 ("octoprint","OctoPrint (Marlin serial bridge)","http_rest",True,"POST /api/printer/command (OctoPrint API; NOT used by Orca)","X-Api-Key header (OctoPrint.cpp:497)",True,"Orca is upload-only; OctoPrint's own API supports gcode passthrough + temp control."),
 ("prusalink","PrusaLink / PrusaConnect","http_rest",False,None,"X-Api-Key or HTTP Digest (OctoPrint.cpp:569)",True,"Upload + print flag only (PUT api/v1/files). No gcode passthrough exposed."),
 ("duet_rrf","Duet / RepRapFirmware","http_rest",True,"GET rr_gcode?gcode=... | POST machine/code (Duet.cpp:247-251)","password via rr_connect (Duet.cpp:206)",True,"Full arbitrary G-code over HTTP."),
 ("repetier","Repetier-Server","http_rest",False,None,"X-Api-Key (Repetier.cpp:179)",True,"Orca upload+autostart only; Repetier-Server has its own gcode API."),
 ("mks_tcp","MKS WiFi (raw TCP)","tcp_console",True,"raw G/M-code over TCP :8080 (MKS.cpp)","none",True,"Raw serial console; any gcode."),
 ("flashforge_tcp","FlashForge (raw TCP ~M)","tcp_console",True,"~M-codes over TCP :8899 (Flashforge.hpp:56)","serial#/checkCode (HTTP variant)",True,"Legacy serial console; HTTP :8898 variant is JSON-only."),
 ("esp3d","ESP3D bridge","http_rest",True,"GET /command?plain=<gcode> (ESP3D.cpp)","none",True,"HTTP-to-serial bridge; arbitrary gcode."),
 ("creality_ws","CrealityPrint (K-series)","websocket",False,None,"Bearer token (CrealityPrint.cpp:68)",True,"WS :9999 fixed verbs (opGcodeFile); no raw gcode via Orca path. Underlying Klipper does."),
 ("elegoo_sdcp","ElegooLink SDCP","websocket",False,None,"X-Token (ElegooLink.cpp)",True,"WS :3030 SDCP command codes only (START_PRINT=128)."),
 ("marlin_serial","Marlin over USB serial","serial",True,"raw G-code over serial","none",False,"Generic serial (not an Orca network host); firmware accepts standard Marlin gcode."),
]
def _m(v): return "NULL" if v is None else sql_str(v)
w("\n-- comm mechanisms")
for key,name,tr,pt,ref,auth,ver,notes in MECHS:
    ref = None if ref is None else ref
    w(f"INSERT INTO ctl_comm_mechanism(key,name,transport,gcode_passthrough,passthrough_ref,auth,orca_verified,notes) VALUES ({sql_str(key)},{sql_str(name)},{sql_str(tr)},{sql_bool(pt)},{_m(ref)},{_m(auth)},{sql_bool(ver)},{_m(notes)});")

# ---------------------------------------------------------------- dialects (command syntax)
DIALECTS = [
 ("bambu_mqtt","Bambu MQTT (structured JSON + gcode_line)","extruder_index 0=main=RIGHT,1=deputy=LEFT (DevDefs.h:98, DeviceManager.hpp:335)"),
 ("marlin","Marlin (Legacy+Firmware) / Klipper-as-Marlin","M104/M140/M141/M106/M220/M221/G28 — GCodeWriter.cpp"),
 ("klipper","Klipper native (Moonraker gcode.script)","M-codes accepted; native SET_* extensions (GCodeWriter.cpp:509 etc.)"),
 ("reprapfirmware","RepRapFirmware (Duet)","G10 temps, M141 chamber, M572 PA (GCodeWriter.cpp:252,511)"),
 ("repetier","Repetier firmware","M104/M201/M233 (GCodeWriter.cpp:363,513)"),
 ("smoothie","Smoothieware","Marlin-compatible subset"),
]
w("\n-- dialects")
for key,name,notes in DIALECTS:
    w(f"INSERT INTO ctl_dialect(key,name,notes) VALUES ({sql_str(key)},{sql_str(name)},{sql_str(notes)});")

# ---------------------------------------------------------------- command catalog
# gate: capability required to be supported (else UNSUPPORTED)
CMDS = [
 # key, category, display, params, gate, desc
 ("set_nozzle_temp","temperature","Set nozzle temperature",[{"name":"temp","type":"int","unit":"C"},{"name":"nozzle_index","type":"int","default":0}],None,"Set the (primary) nozzle target temp."),
 ("set_left_nozzle_temp","temperature","Set left nozzle temperature",[{"name":"temp","type":"int","unit":"C"}],"multi_nozzle","Dual-nozzle machines only."),
 ("set_right_nozzle_temp","temperature","Set right nozzle temperature",[{"name":"temp","type":"int","unit":"C"}],"multi_nozzle","Dual-nozzle machines only."),
 ("set_bed_temp","temperature","Set bed temperature",[{"name":"temp","type":"int","unit":"C"}],None,"Set heated-bed target temp."),
 ("set_chamber_temp","temperature","Set chamber temperature",[{"name":"temp","type":"int","unit":"C"}],"chamber","Active chamber heater only."),
 ("set_part_fan","fan","Set part-cooling fan",[{"name":"pct","type":"int","unit":"%"}],None,"Part-cooling fan speed."),
 ("set_aux_fan","fan","Set auxiliary fan",[{"name":"pct","type":"int","unit":"%"}],"aux_fan","Auxiliary/remote cooling fan."),
 ("set_chamber_fan","fan","Set chamber/exhaust fan",[{"name":"pct","type":"int","unit":"%"}],"chamber_fan","Chamber exhaust fan."),
 ("fan_off","fan","Turn part fan off",[],None,"Stop part-cooling fan."),
 ("home_all","motion","Home all axes",[],None,"Full auto-home."),
 ("home_xy","motion","Home X/Y",[],None,"Home X and Y only."),
 ("move_axis","motion","Jog an axis",[{"name":"axis","type":"str"},{"name":"dist","type":"float","unit":"mm"},{"name":"feed","type":"int"}],None,"Relative jog of one axis."),
 ("extrude","motion","Extrude filament",[{"name":"len","type":"float","unit":"mm"},{"name":"feed","type":"int"}],None,"Relative extrude."),
 ("retract","motion","Retract filament",[{"name":"len","type":"float","unit":"mm"},{"name":"feed","type":"int"}],None,"Relative retract."),
 ("select_extruder","motion","Select active extruder",[{"name":"index","type":"int"}],"multi_nozzle","Multi-nozzle / IDEX only."),
 ("reset_extruder","motion","Reset extruder position",[],None,"G92 E0."),
 ("print_pause","print_control","Pause print",[],None,"Pause the running print."),
 ("print_resume","print_control","Resume print",[],None,"Resume a paused print."),
 ("print_stop","print_control","Stop print",[],None,"Cancel/abort the print."),
 ("print_speed_level","print_control","Set print speed mode",[{"name":"level","type":"int"}],"bambu_only","Bambu 1=silent..4=ludicrous."),
 ("set_speed_factor","print_control","Set speed factor",[{"name":"pct","type":"int","unit":"%"}],None,"M220 feedrate override."),
 ("set_flow_factor","print_control","Set flow factor",[{"name":"pct","type":"int","unit":"%"}],None,"M221 extrusion override."),
 ("set_pressure_advance","tuning","Set pressure advance",[{"name":"value","type":"float"}],None,"PA / linear advance."),
 ("babystep_z","tuning","Live Z-offset babystep",[{"name":"offset","type":"float","unit":"mm"}],None,"Adjust Z offset while printing."),
 ("set_accel","tuning","Set acceleration",[{"name":"accel","type":"int"}],None,"Print acceleration limit."),
 ("chamber_light","light","Chamber light on/off",[{"name":"on","type":"bool"}],"bambu_only","Bambu ledctrl."),
 ("ams_load","material","AMS load filament",[{"name":"tray","type":"int"}],"bambu_only","Bambu AMS."),
 ("ams_unload","material","AMS unload filament",[],"bambu_only","Bambu AMS."),
 ("ams_select_tray","material","AMS select tray",[{"name":"tray","type":"int"}],"bambu_only","Bambu AMS."),
 ("ams_filament_backup","material","AMS filament backup toggle",[{"name":"on","type":"bool"}],"bambu_only","Bambu AMS auto-refill."),
 ("calibrate_bed_level","calibration","Bed level / mesh",[],None,"Auto bed leveling / mesh."),
 ("calibrate_flow","calibration","Flow calibration",[],None,"Flow-rate calibration."),
 ("calibrate_pa","calibration","Pressure-advance calibration",[],None,"PA calibration."),
 ("refresh_status","status","Refresh / push status",[],None,"Request a status push."),
]
w("\n-- command catalog")
for key,cat,disp,params,gate,desc in CMDS:
    w(f"INSERT INTO ctl_command(key,category,display_name,params,gate,description) VALUES ({sql_str(key)},{sql_str(cat)},{sql_str(disp)},{sql_json(params)},{_m(gate)},{sql_str(desc)});")

# ---------------------------------------------------------------- templates (dialect x command)
# send_method: gcode | mqtt_json | mqtt_gcode_line | host_rpc | rr_gcode
# {} placeholders filled at execution.
T = []  # (dialect,command,method,template,wait,verified,source,notes)
def t(d,c,m,tpl,wait=None,ver=True,src=None,notes=None): T.append((d,c,m,tpl,wait,ver,src,notes))

# ---- Bambu MQTT (structured; from DeviceManager.cpp) ----
t("bambu_mqtt","set_nozzle_temp","mqtt_json",'{"print":{"command":"set_nozzle_temp","extruder_index":{nozzle_index},"target_temp":{temp}}}',src="DeviceManager.cpp:1634-1644",notes="extruder_index 0=RIGHT,1=LEFT; legacy fallback gcode_line M104 S{temp} (1628)")
t("bambu_mqtt","set_left_nozzle_temp","mqtt_json",'{"print":{"command":"set_nozzle_temp","extruder_index":1,"target_temp":{temp}}}',src="DeviceManager.cpp:1641; StatusPanel.cpp:4183")
t("bambu_mqtt","set_right_nozzle_temp","mqtt_json",'{"print":{"command":"set_nozzle_temp","extruder_index":0,"target_temp":{temp}}}',src="DeviceManager.cpp:1641; StatusPanel.cpp:4169")
t("bambu_mqtt","set_bed_temp","mqtt_json",'{"print":{"command":"set_bed_temp","temp":{temp}}}',src="DeviceManager.cpp:1613-1621",notes="cap bit 39; fallback gcode_line M140 S{temp}")
t("bambu_mqtt","set_chamber_temp","mqtt_json",'{"print":{"command":"set_ctt","ctt_val":{temp}}}',src="DeviceManager.cpp:1655-1660")
t("bambu_mqtt","set_part_fan","mqtt_json",'{"print":{"command":"set_fan","fan_index":1,"speed":{pct}}}',src="DevFan.cpp:58-68",notes="fan_index 1=part; legacy gcode_line M106 P1 S{0-255}")
t("bambu_mqtt","set_aux_fan","mqtt_json",'{"print":{"command":"set_fan","fan_index":2,"speed":{pct}}}',src="DevFan.cpp; DevFan.h:14")
t("bambu_mqtt","set_chamber_fan","mqtt_json",'{"print":{"command":"set_fan","fan_index":3,"speed":{pct}}}',src="DevFan.h:14 (FAN_CHAMBER)")
t("bambu_mqtt","fan_off","mqtt_json",'{"print":{"command":"set_fan","fan_index":1,"speed":0}}',src="DevFan.cpp")
t("bambu_mqtt","home_all","mqtt_json",'{"print":{"command":"back_to_center"}}',src="DeviceManager.cpp:1484-1489",notes="cap bit 32; fallback gcode_line G28")
t("bambu_mqtt","home_xy","mqtt_gcode_line",'{"print":{"command":"gcode_line","param":"G28 X\\n"}}',src="DeviceManager.cpp:1493")
t("bambu_mqtt","move_axis","mqtt_json",'{"print":{"command":"xyz_ctrl","axis":"{axis}","dir":{dir},"mode":0}}',src="DeviceManager.cpp:1953-1970",notes="cap bit 38; fallback gcode_line G91/G1 (1985)")
t("bambu_mqtt","extrude","mqtt_json",'{"print":{"command":"set_extrusion_length","extruder_index":{nozzle_index},"length":{len}}}',src="DeviceManager.cpp:1998-2004")
t("bambu_mqtt","retract","mqtt_json",'{"print":{"command":"set_extrusion_length","extruder_index":{nozzle_index},"length":-{len}}}',src="DeviceManager.cpp:1998-2004")
t("bambu_mqtt","select_extruder","mqtt_json",'{"print":{"command":"select_extruder","extruder_index":{index}}}',src="DevCtrl.cpp:59-64")
t("bambu_mqtt","print_pause","mqtt_json",'{"print":{"command":"pause","param":""}}',src="DeviceManager.cpp:1530")
t("bambu_mqtt","print_resume","mqtt_json",'{"print":{"command":"resume","param":""}}',src="DeviceManager.cpp:1540")
t("bambu_mqtt","print_stop","mqtt_json",'{"print":{"command":"stop","param":""}}',src="DeviceManager.cpp:1507")
t("bambu_mqtt","print_speed_level","mqtt_json",'{"print":{"command":"print_speed","param":"{level}"}}',src="DeviceManager.cpp:1874")
t("bambu_mqtt","set_pressure_advance","mqtt_gcode_line",'{"print":{"command":"gcode_line","param":"M900 K{value} L1000 M10\\n"}}',src="GCodeWriter.cpp:504")
t("bambu_mqtt","chamber_light","mqtt_json",'{"system":{"command":"ledctrl","led_node":"chamber_light","led_mode":"{on_off}"}}',src="DevLampCtrl.cpp:36-47")
t("bambu_mqtt","ams_load","mqtt_json",'{"print":{"command":"ams_change_filament","target":{tray},"curr_temp":220,"tar_temp":220}}',src="DeviceManager.cpp:1673")
t("bambu_mqtt","ams_unload","mqtt_json",'{"print":{"command":"ams_change_filament","target":255,"slot_id":255}}',src="DeviceManager.cpp:1673-1713")
t("bambu_mqtt","ams_select_tray","mqtt_gcode_line",'{"print":{"command":"gcode_line","param":"M620 P{tray}\\n"}}',src="DeviceManager.cpp:1792")
t("bambu_mqtt","ams_filament_backup","mqtt_json",'{"print":{"command":"print_option","auto_switch_filament":{on}}}',src="DeviceManager.cpp:1925")
t("bambu_mqtt","calibrate_bed_level","mqtt_json",'{"print":{"command":"calibration","option":2}}',src="DeviceManager.cpp:2020 (bed_leveling=1<<1)")
t("bambu_mqtt","calibrate_pa","mqtt_json",'{"print":{"command":"extrusion_cali"}}',src="DeviceManager.cpp:2044")
t("bambu_mqtt","calibrate_flow","mqtt_json",'{"print":{"command":"flowrate_cali"}}',src="DeviceManager.cpp:2194")
t("bambu_mqtt","refresh_status","mqtt_json",'{"pushing":{"command":"pushall","version":1,"push_target":1}}',src="DeviceManager.cpp:1362")
t("bambu_mqtt","reset_extruder","mqtt_gcode_line",'{"print":{"command":"gcode_line","param":"G92 E0\\n"}}',src="GCodeWriter.cpp:609")

# ---- Marlin (Legacy+Firmware); Klipper inherits M-code forms; Smoothie same core ----
def marlinish(dialect, pa_tpl, pa_src):
    t(dialect,"set_nozzle_temp","gcode","M104 S{temp} T{nozzle_index}",wait="M109 S{temp} T{nozzle_index}",src="GCodeWriter.cpp:248,255,269")
    t(dialect,"set_left_nozzle_temp","gcode","M104 T0 S{temp}",wait="M109 T0 S{temp}",src="GCodeWriter.cpp:269 (tool suffix)")
    t(dialect,"set_right_nozzle_temp","gcode","M104 T1 S{temp}",wait="M109 T1 S{temp}",src="GCodeWriter.cpp:269")
    t(dialect,"set_bed_temp","gcode","M140 S{temp}",wait="M190 S{temp}",src="GCodeWriter.cpp:305,309")
    t(dialect,"set_chamber_temp","gcode","M141 S{temp}",wait="M191 S{temp}",src="GCodeWriter.cpp:333,326")
    t(dialect,"set_part_fan","gcode","M106 S{pct255}",src="GCodeWriter.cpp:1287",notes="pct255 = round(255*pct/100)")
    t(dialect,"set_aux_fan","gcode","M106 P2 S{pct255}",src="GCodeWriter.cpp:1305 (set_additional_fan)")
    t(dialect,"set_chamber_fan","gcode","M106 P3 S{pct255}",src="GCodeWriter.cpp:1320 (set_exhaust_fan)")
    t(dialect,"fan_off","gcode","M107",src="GCodeWriter.cpp:1273 (M106 S0)")
    t(dialect,"home_all","gcode","G28",src="PrintConfig.cpp:6466 (machine_start default)")
    t(dialect,"home_xy","gcode","G28 X Y",src="G28 axis subset")
    t(dialect,"move_axis","gcode","G91\nG1 {axis}{dist} F{feed}\nG90",src="GCodeWriter.cpp travel (G1) + preamble G90/relative")
    t(dialect,"extrude","gcode","M83\nG1 E{len} F{feed}",src="GCodeWriter.cpp:225 (M83) + G1 E")
    t(dialect,"retract","gcode","M83\nG1 E-{len} F{feed}",src="GCodeWriter.cpp:1202")
    t(dialect,"select_extruder","gcode","T{index}",src="GCodeWriter.cpp:683,707")
    t(dialect,"reset_extruder","gcode","G92 E0",src="GCodeWriter.cpp:609")
    t(dialect,"set_speed_factor","gcode","M220 S{pct}",src="WipeTower.cpp:1001")
    t(dialect,"set_flow_factor","gcode","M221 S{pct}",ver=False,src="firmware standard (Orca emits E, not M221; GCodeProcessor:6379 parses)")
    t(dialect,"set_accel","gcode","M204 S{accel}",src="GCodeWriter.cpp:375")
    t(dialect,"set_pressure_advance","gcode",pa_tpl,src=pa_src)
    t(dialect,"calibrate_bed_level","gcode","G29",src="firmware standard (machine_bed_leveling gcode)")
    # print control (Orca uses host for non-Bambu; firmware gcode equivalents):
    t(dialect,"print_pause","gcode","M25",ver=False,src="Marlin firmware (Orca PrintHost has no pause; PrintHost.cpp)")
    t(dialect,"print_resume","gcode","M24",ver=False,src="Marlin firmware")
    t(dialect,"print_stop","gcode","M524",ver=False,src="Marlin firmware (abort)")

marlinish("marlin","M900 K{value}","GCodeWriter.cpp:517 (Marlin M900)")
marlinish("smoothie","M900 K{value}","GCodeWriter.cpp:517")

# ---- Klipper native: M-codes accepted, but native extensions preferred ----
marlinish("klipper","SET_PRESSURE_ADVANCE ADVANCE={value}","GCodeWriter.cpp:509")
# override klipper-native forms for a few commands (replace after marlinish inserted them):
KLIP_OVERRIDE = {
 "babystep_z":("gcode","SET_GCODE_OFFSET Z_ADJUST={offset} MOVE=1",True,"Klipper native"),
 "set_accel":("gcode","SET_VELOCITY_LIMIT ACCEL={accel}",True,"GCodeWriter.cpp:367"),
 "print_pause":("gcode","PAUSE",True,"Klipper macro"),
 "print_resume":("gcode","RESUME",True,"Klipper macro"),
 "print_stop":("gcode","CANCEL_PRINT",True,"Klipper macro"),
 "calibrate_bed_level":("gcode","BED_MESH_CALIBRATE",True,"Klipper"),
 "set_speed_factor":("gcode","M220 S{pct}",True,"Klipper accepts M220"),
 "set_left_nozzle_temp":("gcode","SET_HEATER_TEMPERATURE HEATER=extruder1 TARGET={temp}",True,"Klipper dual (extruder1)"),
 "set_right_nozzle_temp":("gcode","SET_HEATER_TEMPERATURE HEATER=extruder TARGET={temp}",True,"Klipper dual (extruder)"),
}
# babystep_z also for marlin (M290):
t("marlin","babystep_z","gcode","M290 Z{offset}",src="Marlin firmware (babystep)")
t("smoothie","babystep_z","gcode","M290 Z{offset}",src="firmware")

# ---- RepRapFirmware (Duet) ----
t("reprapfirmware","set_nozzle_temp","gcode","G10 P{nozzle_index} S{temp}",wait="G10 P{nozzle_index} S{temp}\nM116",src="GCodeWriter.cpp:252,278")
t("reprapfirmware","set_left_nozzle_temp","gcode","G10 P1 S{temp}",src="GCodeWriter.cpp:252")
t("reprapfirmware","set_right_nozzle_temp","gcode","G10 P0 S{temp}",src="GCodeWriter.cpp:252")
t("reprapfirmware","set_bed_temp","gcode","M140 S{temp}",wait="M190 S{temp}",src="GCodeWriter.cpp:305")
t("reprapfirmware","set_chamber_temp","gcode","M141 S{temp}",wait="M191 S{temp}",src="GCodeWriter.cpp:333")
t("reprapfirmware","set_part_fan","gcode","M106 S{pct255}",src="GCodeWriter.cpp:1287")
t("reprapfirmware","set_aux_fan","gcode","M106 P2 S{pct255}",src="GCodeWriter.cpp:1305")
t("reprapfirmware","set_chamber_fan","gcode","M106 P3 S{pct255}",src="GCodeWriter.cpp:1320")
t("reprapfirmware","fan_off","gcode","M106 S0",src="GCodeWriter.cpp:1273")
t("reprapfirmware","home_all","gcode","G28",src="RRF")
t("reprapfirmware","home_xy","gcode","G28 X Y",src="RRF")
t("reprapfirmware","move_axis","gcode","G91\nG1 {axis}{dist} F{feed}\nG90",src="RRF")
t("reprapfirmware","extrude","gcode","M83\nG1 E{len} F{feed}",src="RRF")
t("reprapfirmware","retract","gcode","M83\nG1 E-{len} F{feed}",src="RRF")
t("reprapfirmware","select_extruder","gcode","T{index}",src="GCodeWriter.cpp:683")
t("reprapfirmware","reset_extruder","gcode","G92 E0",src="GCodeWriter.cpp:609")
t("reprapfirmware","set_speed_factor","gcode","M220 S{pct}",src="RRF")
t("reprapfirmware","set_flow_factor","gcode","M221 S{pct}",src="RRF")
t("reprapfirmware","set_accel","gcode","M204 P{accel} T{accel}",src="GCodeWriter.cpp:365")
t("reprapfirmware","set_pressure_advance","gcode","M572 D0 S{value}",src="GCodeWriter.cpp:511")
t("reprapfirmware","babystep_z","gcode","M290 Z{offset}",src="RRF")
t("reprapfirmware","calibrate_bed_level","gcode","G29",src="RRF mesh")
t("reprapfirmware","print_pause","gcode","M25",ver=False,src="RRF")
t("reprapfirmware","print_resume","gcode","M24",ver=False,src="RRF")
t("reprapfirmware","print_stop","gcode","M0",ver=False,src="RRF")

# ---- Repetier firmware ----
t("repetier","set_nozzle_temp","gcode","M104 S{temp} T{nozzle_index}",wait="M109 S{temp} T{nozzle_index}",src="GCodeWriter.cpp:248")
t("repetier","set_bed_temp","gcode","M140 S{temp}",wait="M190 S{temp}",src="GCodeWriter.cpp:305")
t("repetier","set_chamber_temp","gcode","M141 S{temp}",wait="M191 S{temp}",src="GCodeWriter.cpp:333")
t("repetier","set_part_fan","gcode","M106 S{pct255}",src="GCodeWriter.cpp:1287")
t("repetier","fan_off","gcode","M107",src="GCodeWriter.cpp:1273")
t("repetier","home_all","gcode","G28",src="Repetier")
t("repetier","home_xy","gcode","G28 X Y",src="Repetier")
t("repetier","move_axis","gcode","G91\nG1 {axis}{dist} F{feed}\nG90",src="Repetier")
t("repetier","extrude","gcode","M83\nG1 E{len} F{feed}",src="Repetier")
t("repetier","retract","gcode","M83\nG1 E-{len} F{feed}",src="Repetier")
t("repetier","reset_extruder","gcode","G92 E0",src="GCodeWriter.cpp:609")
t("repetier","set_speed_factor","gcode","M220 S{pct}",src="Repetier")
t("repetier","set_accel","gcode","M201 X{accel} Y{accel}",src="GCodeWriter.cpp:363")
t("repetier","set_pressure_advance","gcode","M233 X{value} Y{value}",src="GCodeWriter.cpp:513")
t("repetier","print_pause","gcode","M25",ver=False,src="Repetier")
t("repetier","print_resume","gcode","M24",ver=False,src="Repetier")
t("repetier","print_stop","gcode","M0",ver=False,src="Repetier")

# apply klipper overrides
by_key = {}
for row in T:
    by_key[(row[0],row[1])] = row
for cmd,(m,tpl,ver,note) in KLIP_OVERRIDE.items():
    by_key[("klipper",cmd)] = ("klipper",cmd,m,tpl,None,ver,by_key.get(("klipper",cmd),(None,)*8)[6],note)
T = list(by_key.values())

w("\n-- command templates")
for d,c,m,tpl,wait,ver,src,notes in T:
    w("INSERT INTO ctl_command_template(dialect_key,command_key,send_method,template,wait_variant,orca_verified,source_ref,notes) VALUES "
      f"({sql_str(d)},{sql_str(c)},{sql_str(m)},{sql_str(tpl)},{_m(wait)},{sql_bool(ver)},{_m(src)},{_m(notes)});")

# ---------------------------------------------------------------- printer types (from CSV)
VENDOR_POP = {  # coarse popularity rank (lower=more popular)
 "BBL":1,"Prusa":2,"Creality":3,"Voron":4,"Anycubic":5,"Elegoo":6,"Qidi":7,"Sovol":8,
 "Ratrig":9,"Snapmaker":10,"FLSun":11,"Flashforge":12,"Artillery":13,"Kingroon":14,
 "Vzbot":15,"SeeMeCNC":16,"Raise3D":17,"UltiMaker":18,
}
def dialect_for(flavor):
    f = (flavor or "").lower()
    if f.startswith("klipper"): return "klipper"
    if f.startswith("marlin"): return "marlin"
    if f=="reprapfirmware": return "reprapfirmware"
    if f=="repetier": return "repetier"
    if f=="smoothie": return "smoothie"
    return "marlin"  # unknown default
def mech_for(vendor,flavor):
    if vendor=="BBL": return "bambu_mqtt"
    f=(flavor or "").lower()
    if f.startswith("klipper"): return "moonraker"
    if f=="reprapfirmware": return "duet_rrf"
    if f=="repetier": return "repetier"
    if vendor=="Prusa": return "prusalink"
    return "octoprint"  # Marlin over OctoPrint/serial (representative default)
def difficulty_for(mech):
    return {"bambu_mqtt":"high","moonraker":"medium","duet_rrf":"medium","octoprint":"medium",
            "prusalink":"medium","repetier":"medium"}.get(mech,"medium")

rows=[]
with open("/home/claude/orca_models.csv") as fh:
    for r in csv.DictReader(fh):
        vendor=r["vendor"].strip(); model=r["model_or_profile_name"].strip()
        flavor=r["gcode_flavor"].strip();
        try: nz=int(r["nozzle_count"])
        except: nz=1
        chamber = r["has_chamber_heater"].strip().lower()=="true"
        notes=r.get("notes","").strip()
        aux = "aux-fan" in notes.lower() or vendor=="BBL" and "aux" in notes.lower()
        # Bambu speaks MQTT structured commands, NOT its nominal marlin gcode_flavor.
        dk = "bambu_mqtt" if vendor=="BBL" else dialect_for(flavor)
        mk=mech_for(vendor,flavor)
        rows.append((vendor,model,dk,mk,flavor,nz,chamber,aux,nz>1,VENDOR_POP.get(vendor,50),difficulty_for(mk),notes))

w("\n-- printer types")
seen=set()
for vendor,model,dk,mk,flavor,nz,chamber,aux,multi,pop,diff,notes in rows:
    keyp=(vendor,model)
    if keyp in seen: continue
    seen.add(keyp)
    w("INSERT INTO ctl_printer_type(vendor,model,dialect_key,mechanism_key,gcode_flavor,nozzle_count,has_chamber_heater,has_aux_fan,is_multi_nozzle,popularity_rank,difficulty,notes) VALUES "
      f"({sql_str(vendor)},{sql_str(model)},{sql_str(dk)},{sql_str(mk)},{sql_str(flavor)},{nz},{sql_bool(chamber)},{sql_bool(aux)},{sql_bool(multi)},{pop},{sql_str(diff)},{_m(notes) if notes else 'NULL'});")

# ---------------------------------------------------------------- generative cross-ref
# supported = a template exists for the printer's dialect+command AND capability gate passes.
w("""
-- cross-reference: every printer_type x every command, resolved to supported/UNSUPPORTED
INSERT INTO ctl_printer_command_support (printer_type_id, command_key, status, supported, reason)
SELECT pt.id, c.key,
  CASE WHEN ok THEN 'supported' ELSE 'UNSUPPORTED' END,
  ok,
  CASE
    WHEN ok THEN NULL
    WHEN c.gate='chamber'      AND NOT pt.has_chamber_heater THEN 'no chamber heater'
    WHEN c.gate='aux_fan'      AND NOT pt.has_aux_fan         THEN 'no auxiliary fan'
    WHEN c.gate='chamber_fan'  AND NOT pt.has_chamber_heater  THEN 'no chamber/exhaust fan'
    WHEN c.gate='multi_nozzle' AND pt.nozzle_count<2          THEN 'single nozzle'
    WHEN c.gate='bambu_only'   AND pt.mechanism_key<>'bambu_mqtt' THEN 'Bambu-only command'
    WHEN tpl.template IS NULL THEN 'no command mapping for '||pt.dialect_key||' dialect'
    ELSE 'unsupported'
  END
FROM ctl_printer_type pt
CROSS JOIN ctl_command c
LEFT JOIN ctl_command_template tpl ON tpl.dialect_key=pt.dialect_key AND tpl.command_key=c.key
CROSS JOIN LATERAL (SELECT (
    tpl.template IS NOT NULL
    AND (c.gate IS NULL
      OR (c.gate='chamber'      AND pt.has_chamber_heater)
      OR (c.gate='aux_fan'      AND pt.has_aux_fan)
      OR (c.gate='chamber_fan'  AND pt.has_chamber_heater)
      OR (c.gate='multi_nozzle' AND pt.nozzle_count>=2)
      OR (c.gate='bambu_only'   AND pt.mechanism_key='bambu_mqtt')
    )
) AS ok) g;

COMMIT;
""")

with open("/home/claude/ctl_framework.sql","w") as fh:
    fh.write("\n".join(OUT))
print("wrote /home/claude/ctl_framework.sql")
print("mechanisms",len(MECHS),"dialects",len(DIALECTS),"commands",len(CMDS),"templates",len(T),"printer_types",len(seen))
