"""Constants for the IR Remote HVAC integration."""

DOMAIN = "irremote_hvac"

# Dispatcher signal (suffixed with the config entry id) fired whenever the
# climate entity writes new state, so companion entities (e.g. the power
# switch) can refresh without polling.
SIGNAL_HVAC_STATE_UPDATED = f"{DOMAIN}_hvac_state_updated"

# Config entry keys
CONF_EMITTER_ENTITY_ID = "emitter_entity_id"
CONF_BRAND = "brand"
CONF_LEGACY_PROTOCOL = "protocol"
CONF_MODEL = "model"
CONF_MODULATION = "modulation"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TEMP_STEP = "temp_step"
CONF_DEBOUNCE_DELAY = "debounce_delay"
CONF_POWER_SENSOR_ENTITY_ID = "power_sensor_entity_id"
CONF_POWER_THRESHOLD = "power_threshold"
CONF_CURRENT_TEMPERATURE_SENSOR_ENTITY_ID = "current_temperature_sensor_entity_id"
CONF_CURRENT_HUMIDITY_SENSOR_ENTITY_ID = "current_humidity_sensor_entity_id"

# Defaults
DEFAULT_MIN_TEMP = 16.0
DEFAULT_MAX_TEMP = 30.0
DEFAULT_TEMP_STEP = 1.0
LEGACY_DEFAULT_MODEL = -1
DEFAULT_MODULATION = 38000
DEFAULT_DEBOUNCE_DELAY = 2.0
DEFAULT_POWER_THRESHOLD = 10.0

# Extra fan modes not in HA constants
FAN_MIN = "min"
FAN_MAX = "max"

# Preset mode names
PRESET_QUIET = "quiet"
PRESET_TURBO = "turbo"

# Protocol identifiers used by config entries created before pyhvac 0.2.
# They are retained only to migrate existing entries.
LEGACY_SUPPORTED_PROTOCOLS: dict[str, str] = {
    "Airton": "AIRTON",
    "Airwell": "AIRWELL",
    "Amcor": "AMCOR",
    "Argo": "ARGO",
    "Bosch (144-bit)": "BOSCH144",
    "Carrier (40-bit)": "CARRIER_AC40",
    "Carrier (64-bit)": "CARRIER_AC64",
    "Carrier (84-bit)": "CARRIER_AC84",
    "Carrier (128-bit)": "CARRIER_AC128",
    "Coolix": "COOLIX",
    "Coolix (48-bit)": "COOLIX48",
    "Corona AC": "CORONA_AC",
    "Daikin": "DAIKIN",
    "Daikin 2": "DAIKIN2",
    "Daikin (64-bit)": "DAIKIN64",
    "Daikin (128-bit)": "DAIKIN128",
    "Daikin (152-bit)": "DAIKIN152",
    "Daikin (160-bit)": "DAIKIN160",
    "Daikin (176-bit)": "DAIKIN176",
    "Daikin (216-bit)": "DAIKIN216",
    "Daikin (312-bit)": "DAIKIN312",
    "DeLonghi AC": "DELONGHI_AC",
    "Electra AC": "ELECTRA_AC",
    "Fujitsu AC": "FUJITSU_AC",
    "Goodweather": "GOODWEATHER",
    "Gree": "GREE",
    "Haier AC": "HAIER_AC",
    "Haier AC YRW02": "HAIER_AC_YRW02",
    "Haier AC (160-bit)": "HAIER_AC160",
    "Haier AC (176-bit)": "HAIER_AC176",
    "Hitachi AC": "HITACHI_AC",
    "Hitachi AC1": "HITACHI_AC1",
    "Hitachi AC2": "HITACHI_AC2",
    "Hitachi AC3": "HITACHI_AC3",
    "Hitachi AC (264-bit)": "HITACHI_AC264",
    "Hitachi AC (296-bit)": "HITACHI_AC296",
    "Hitachi AC (344-bit)": "HITACHI_AC344",
    "Hitachi AC (424-bit)": "HITACHI_AC424",
    "Kelon": "KELON",
    "Kelvinator": "KELVINATOR",
    "LG AC": "LG",
    "LG AC2": "LG2",
    "Midea AC": "MIDEA",
    "Midea AC (24-bit)": "MIDEA24",
    "Mitsubishi Electric AC": "MITSUBISHI_AC",
    "Mitsubishi AC (112-bit)": "MITSUBISHI112",
    "Mitsubishi AC (136-bit)": "MITSUBISHI136",
    "Mitsubishi Heavy Industries (88-bit)": "MITSUBISHI_HEAVY_88",
    "Mitsubishi Heavy Industries (152-bit)": "MITSUBISHI_HEAVY_152",
    "Neoclima": "NEOCLIMA",
    "Panasonic AC": "PANASONIC_AC",
    "Panasonic AC (32-bit)": "PANASONIC_AC32",
    "Samsung AC": "SAMSUNG_AC",
    "Sanyo AC": "SANYO_AC",
    "Sanyo AC (88-bit)": "SANYO_AC88",
    "Sharp AC": "SHARP_AC",
    "TCL AC (96-bit)": "TCL96AC",
    "TCL AC (112-bit)": "TCL112AC",
    "Teco AC": "TECO",
    "Toshiba AC": "TOSHIBA_AC",
    "Transcold": "TRANSCOLD",
    "Truma": "TRUMA",
    "Voltas": "VOLTAS",
    "Whirlpool AC": "WHIRLPOOL_AC",
}
