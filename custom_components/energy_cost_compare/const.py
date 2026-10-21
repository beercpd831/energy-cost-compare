"""Constants for Energy cost compare."""

DOMAIN = "energy_cost_compare"
NAME = "Energy cost compare"

CONF_GRID_IMPORT = "grid_import"
CONF_GRID_EXPORT = "grid_export"
CONF_PV = "pv"
CONF_INV_IN = "inv_in"
CONF_INV_OUT = "inv_out"
CONF_DC_CHARGE = "dc_charge"
CONF_DC_DISCHARGE = "dc_discharge"
CONF_START_DATE = "start_date"
CONF_HEAT = "heat"
CONF_HP_ELEC = "hp_elec"
CONF_BOILER_EFFICIENCY = "boiler_efficiency"
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_INCLUDES_VAT = "price_includes_vat"

# Config keys that hold a list of energy counters summed into one quantity.
QUANTITY_KEYS = (
    CONF_GRID_IMPORT,
    CONF_GRID_EXPORT,
    CONF_PV,
    CONF_INV_IN,
    CONF_INV_OUT,
    CONF_DC_CHARGE,
    CONF_DC_DISCHARGE,
    CONF_HEAT,
    CONF_HP_ELEC,
)
REQUIRED_KEYS = (CONF_GRID_IMPORT, CONF_GRID_EXPORT, CONF_PV)

# Short-term (5-minute) statistics are purged with the recorder history (10 days by
# default); older periods are read from hourly statistics.
SHORT_TERM_DAYS = 9
# Wait this long after an interval ends before reading it, so the recorder has
# compiled its 5-minute statistics.
PROCESS_LAG_MINUTES = 7
# Intervals older than this are stored even when statistics are missing.
GIVE_UP_AFTER_MINUTES = 60

LEDGER_DIR = "energy_cost_compare"
LEDGER_FILE = "ledger.db"

SERVICE_RECALCULATE = "recalculate"
SERVICE_RELOAD_TARIFFS = "reload_tariffs"
SIGNAL_LEDGER_UPDATED = f"{DOMAIN}_ledger_updated"

TARIFF_DIR = "tariffs"
BASELINE_AVERAGE = "average_fixed"
DEFAULT_ACTUAL = "zonneplan_dynamisch"
# Ask for a price check when a contract's prices are older than this.
STALE_PRICES_DAYS = 60
DEFAULT_SCENARIO = "2027_no_netting"
SERVICE_PROPOSE_PRICES = "propose_prices"
PROPOSAL_ISSUE_PREFIX = "proposal_"
