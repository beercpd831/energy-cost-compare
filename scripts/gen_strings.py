"""Generate strings.json, en.json and nl.json for the integration."""
import json, sys
from pathlib import Path

root = Path(sys.argv[1])
L = {
 "en": dict(
  fields={
   "grid_import": ("Grid import", "Energy taken from the grid (kWh counters)"),
   "grid_export": ("Grid export", "Energy fed into the grid"),
   "pv": ("PV production", "Solar inverter yield"),
   "inv_in": ("Into battery inverters", "AC energy into the battery inverters, from both AC-in and AC-out"),
   "inv_out": ("Out of battery inverters", "AC energy out of the battery inverters, to both AC-out and AC-in"),
   "dc_charge": ("Battery DC charge", "Optional, used to split battery losses from stored energy"),
   "dc_discharge": ("Battery DC discharge", "Optional, used with DC charge"),
   "heat": ("Heat from the heat pump", "Heat delivered for heating and hot water (kWh counters), for the gas comparison"),
   "hp_elec": ("Heat pump electricity", "Electricity used by the heat pump"),
   "boiler_efficiency": ("Gas boiler efficiency", "Efficiency of the gas boiler you compare with (0.90 = 90%, on upper heating value)"),
   "price_sensor": ("Market price sensor", "Day-ahead (EPEX) price in EUR/kWh, for example from ENTSO-e"),
   "price_includes_vat": ("Price sensor includes VAT", "On when the sensor shows the market price plus 21% VAT"),
   "start_date": ("Start date", "Compare from this date"),
  },
  title="Energy cost compare", user_desc="Pick the energy counters to record. Several counters per field are added up.",
  counters_title="Energy counters", counters_desc="Changing these rebuilds the history from the start date.",
  menu={"counters": "Energy counters and price sensor", "contract": "Check and update contract prices"},
  contract_title="Which contract?", contract_field="Contract",
  prices_title="Prices of {name}",
  prices_desc="All amounts in euro excluding VAT; electricity supply excludes energy tax. Current values are from {latest_from}. A new start date adds a price period and keeps the old one.\n\nNote: {note}\n\nSources: {sources}",
  price_fields={"supply":"Electricity supply price (EUR/kWh)","markup":"Electricity markup on market price (EUR/kWh)",
   "feed_in":"Feed-in compensation (EUR/kWh)","feed_in_markup":"Feed-in markup on market price (EUR/kWh, may be negative)",
   "feed_in_bonus":"Daytime feed-in bonus (share of the market price, 0.10 = 10%)",
   "feed_in_bonus_min_price":"No feed-in bonus below this market price (EUR/kWh)",
   "feed_in_cost":"Feed-in costs (EUR per exported kWh)","fixed_per_month":"Fixed supply costs electricity (EUR/month)",
   "gas_supply":"Gas supply price (EUR/m³)","gas_markup":"Gas markup (EUR/m³)","gas_fixed_per_month":"Fixed supply costs gas (EUR/month)",
   "valid_from":"Prices valid from","checked_on":"Checked on"},
  errors={"required":"This field is required","inverter_pair":"Fill in both inverter fields, or neither","invalid_prices":"These prices can't be saved; check the values"},
  abort="Energy cost compare is already set up",
  energy={"house_load":"House load","pv_only_import":"PV only: grid import","pv_only_export":"PV only: grid export","battery_net_ac":"Battery system net AC","battery_losses":"Battery losses"},
  today="today", total="since start",
  diag={"last_interval":"Last recorded interval","incomplete_intervals":"Incomplete intervals","missing_prices":"Intervals without market price"},
  costs={"cost_baseline":"Cost without PV and battery (baseline)","cost_pv":"Cost with PV only","cost_pv_battery":"Cost with PV and battery",
   "saving_pv":"Saving PV","saving_pv_battery":"Saving PV and battery","saving_battery":"Saving battery"},
  heat={"gas_boiler_cost":"Heating cost with a gas boiler","heat_pump_cost":"Heating cost with the heat pump","saving_heat_pump":"Saving heat pump"},
  periods={"today":"today","month":"this month","year":"this year","total":"since start"},
  contract="{name}: cost of actual usage",
  selects={"baseline":"Baseline contract","actual":"Actual supplier","scenario":"Future scenario"},
  scenario_prefix="Scenario:",
  services={"recalculate":("Recalculate","Read the energy history again from a date (default: the start date).",{"from_date":("From date","First day to recalculate")}),
            "reload_tariffs":("Reload tariffs","Read the contract, tax and grid files again after editing them.",{}),
            "propose_prices":("Propose contract prices","Offer new prices for a contract. They are only used after you accept the repair notice that appears.",
              {"contract_id":("Contract","Id of the contract, for example vattenfall_vast_1jaar"),
               "valid_from":("Valid from","First day of the new prices (default: today)"),
               "electricity":("Electricity prices","EUR excl. VAT: supply, markup, feed_in, feed_in_markup, feed_in_cost, fixed_per_month; feed_in_bonus as a share"),
               "gas":("Gas prices","EUR excl. VAT: supply, markup, fixed_per_month"),
               "sources":("Sources","Where the prices were found"),
               "note":("Note","Shown with the proposal and saved with the contract"),
               "confidence":("Confidence","For example estimate or offer")})},
  issues={"tariff_errors":("Tariff files have errors","These tariff files could not be used: {errors}. They are in {folder}."),
          "stale_prices":("Contract prices need a check","The prices of {contracts} were last checked more than {days} days ago. Check them under Settings > Devices & services > Energy cost compare > Configure.")},
  proposal=dict(title="New prices for {contract}",
    description="Proposed on {proposed_on}, valid from {valid_from}. All amounts excl. VAT.\n\n{changes}\n\nSources:\n{sources}\n\nNote: {note}",
    step_title="New prices for {contract}",
    step_desc="Valid from {valid_from}. All amounts excl. VAT.\n\n{changes}\n\nSources:\n{sources}\n\nAccepting adds a price period from {valid_from}; earlier days keep their old prices. A later period gets a new value only where it had the same value as now.",
    accept="Accept these prices", reject="Reject",
    gone="This proposal no longer exists.", invalid="These prices can't be saved; check the contract file."),
 ),
 "nl": dict(
  fields={
   "grid_import": ("Netafname", "Energie van het net (kWh-tellers)"),
   "grid_export": ("Teruglevering", "Energie terug naar het net"),
   "pv": ("PV-opbrengst", "Opbrengst van de zonne-omvormer"),
   "inv_in": ("Naar de batterij-omvormers", "AC-energie de omvormers in, vanaf AC-in en AC-out"),
   "inv_out": ("Uit de batterij-omvormers", "AC-energie de omvormers uit, naar AC-out en AC-in"),
   "dc_charge": ("Batterij DC laden", "Optioneel, om verliezen te scheiden van opgeslagen energie"),
   "dc_discharge": ("Batterij DC ontladen", "Optioneel, samen met DC laden"),
   "heat": ("Warmte van de warmtepomp", "Geleverde warmte voor verwarming en warm water (kWh-tellers), voor de gasvergelijking"),
   "hp_elec": ("Stroom van de warmtepomp", "Elektriciteitsverbruik van de warmtepomp"),
   "boiler_efficiency": ("Rendement cv-ketel", "Rendement van de gasketel waarmee je vergelijkt (0,90 = 90%, op bovenwaarde)"),
   "price_sensor": ("Marktprijssensor", "Day-ahead (EPEX) prijs in EUR/kWh, bijvoorbeeld van ENTSO-e"),
   "price_includes_vat": ("Prijssensor is inclusief btw", "Aan als de sensor de marktprijs plus 21% btw toont"),
   "start_date": ("Startdatum", "Vergelijken vanaf deze datum"),
  },
  title="Energiekosten vergelijken", user_desc="Kies de energietellers die worden vastgelegd. Meerdere tellers per veld worden opgeteld.",
  counters_title="Energietellers", counters_desc="Wijzigen bouwt de geschiedenis opnieuw op vanaf de startdatum.",
  menu={"counters": "Energietellers en prijssensor", "contract": "Contractprijzen controleren en bijwerken"},
  contract_title="Welk contract?", contract_field="Contract",
  prices_title="Prijzen van {name}",
  prices_desc="Alle bedragen in euro exclusief btw; de stroomprijs is exclusief energiebelasting. De huidige waarden gelden vanaf {latest_from}. Een nieuwe ingangsdatum voegt een prijsperiode toe en bewaart de oude.\n\nOpmerking: {note}\n\nBronnen: {sources}",
  price_fields={"supply":"Leveringsprijs stroom (EUR/kWh)","markup":"Opslag stroom op marktprijs (EUR/kWh)",
   "feed_in":"Terugleververgoeding (EUR/kWh)","feed_in_markup":"Opslag teruglevering op marktprijs (EUR/kWh, mag negatief)",
   "feed_in_bonus":"Zonnebonus overdag (deel van de marktprijs, 0,10 = 10%)",
   "feed_in_bonus_min_price":"Geen zonnebonus onder deze marktprijs (EUR/kWh)",
   "feed_in_cost":"Terugleverkosten (EUR per teruggeleverde kWh)","fixed_per_month":"Vaste leveringskosten stroom (EUR/maand)",
   "gas_supply":"Leveringsprijs gas (EUR/m³)","gas_markup":"Opslag gas (EUR/m³)","gas_fixed_per_month":"Vaste leveringskosten gas (EUR/maand)",
   "valid_from":"Prijzen geldig vanaf","checked_on":"Gecontroleerd op"},
  errors={"required":"Dit veld is verplicht","inverter_pair":"Vul beide omvormervelden in, of geen van beide","invalid_prices":"Deze prijzen kunnen niet worden opgeslagen; controleer de waarden"},
  abort="Energiekosten vergelijken is al ingesteld",
  energy={"house_load":"Huisverbruik","pv_only_import":"Alleen PV: netafname","pv_only_export":"Alleen PV: teruglevering","battery_net_ac":"Batterijsysteem netto AC","battery_losses":"Batterijverliezen"},
  today="vandaag", total="sinds start",
  diag={"last_interval":"Laatst vastgelegd kwartier","incomplete_intervals":"Onvolledige kwartieren","missing_prices":"Kwartieren zonder marktprijs"},
  costs={"cost_baseline":"Kosten zonder PV en batterij (basis)","cost_pv":"Kosten met alleen PV","cost_pv_battery":"Kosten met PV en batterij",
   "saving_pv":"Besparing PV","saving_pv_battery":"Besparing PV en batterij","saving_battery":"Besparing batterij"},
  heat={"gas_boiler_cost":"Verwarmingskosten met cv-ketel","heat_pump_cost":"Verwarmingskosten met warmtepomp","saving_heat_pump":"Besparing warmtepomp"},
  periods={"today":"vandaag","month":"deze maand","year":"dit jaar","total":"sinds start"},
  contract="{name}: kosten van werkelijk verbruik",
  selects={"baseline":"Basiscontract","actual":"Huidige leverancier","scenario":"Toekomstscenario"},
  scenario_prefix="Scenario:",
  services={"recalculate":("Herberekenen","Lees de energiegeschiedenis opnieuw vanaf een datum (standaard de startdatum).",{"from_date":("Vanaf datum","Eerste dag om te herberekenen")}),
            "reload_tariffs":("Tarieven herladen","Lees de contract-, belasting- en netbeheerbestanden opnieuw na een wijziging.",{}),
            "propose_prices":("Contractprijzen voorstellen","Stel nieuwe prijzen voor een contract voor. Ze worden pas gebruikt als je de reparatiemelding accepteert.",
              {"contract_id":("Contract","Id van het contract, bijvoorbeeld vattenfall_vast_1jaar"),
               "valid_from":("Geldig vanaf","Eerste dag van de nieuwe prijzen (standaard vandaag)"),
               "electricity":("Stroomprijzen","EUR excl. btw: supply, markup, feed_in, feed_in_markup, feed_in_cost, fixed_per_month; feed_in_bonus als deel"),
               "gas":("Gasprijzen","EUR excl. btw: supply, markup, fixed_per_month"),
               "sources":("Bronnen","Waar de prijzen gevonden zijn"),
               "note":("Opmerking","Getoond bij het voorstel en bewaard bij het contract"),
               "confidence":("Betrouwbaarheid","Bijvoorbeeld estimate of offer")})},
  issues={"tariff_errors":("Fouten in tariefbestanden","Deze tariefbestanden konden niet worden gebruikt: {errors}. Ze staan in {folder}."),
          "stale_prices":("Contractprijzen controleren","De prijzen van {contracts} zijn meer dan {days} dagen geleden gecontroleerd. Controleer ze via Instellingen > Apparaten en diensten > Energiekosten vergelijken > Configureren.")},
  proposal=dict(title="Nieuwe prijzen voor {contract}",
    description="Voorgesteld op {proposed_on}, geldig vanaf {valid_from}. Alle bedragen excl. btw.\n\n{changes}\n\nBronnen:\n{sources}\n\nOpmerking: {note}",
    step_title="Nieuwe prijzen voor {contract}",
    step_desc="Geldig vanaf {valid_from}. Alle bedragen excl. btw.\n\n{changes}\n\nBronnen:\n{sources}\n\nAccepteren voegt een prijsperiode toe vanaf {valid_from}; eerdere dagen houden hun oude prijzen. Een latere periode krijgt alleen een nieuwe waarde waar die gelijk was aan nu.",
    accept="Prijzen accepteren", reject="Afwijzen",
    gone="Dit voorstel bestaat niet meer.", invalid="Deze prijzen kunnen niet worden opgeslagen; controleer het contractbestand."),
 ),
}

def build(t):
    data = {k: v[0] for k, v in t["fields"].items()}
    desc = {k: v[1] for k, v in t["fields"].items()}
    sensors = {}
    for k, n in t["energy"].items():
        sensors[f"{k}_today"] = {"name": f"{n} {t['today']}"}
        sensors[f"{k}_total"] = {"name": f"{n} {t['total']}"}
    for k, n in t["diag"].items():
        sensors[k] = {"name": n}
    for k, n in t["costs"].items():
        for p, pn in t["periods"].items():
            sensors[f"{k}_{p}"] = {"name": f"{n} {pn}"}
    sensors["contract"] = {"name": t["contract"]}
    for k, n in t["heat"].items():
        for p, pn in t["periods"].items():
            sensors[f"{k}_{p}"] = {"name": f"{n} {pn}"}
    for k, n in t["costs"].items():
        sensors[f"scenario_{k}"] = {"name": f"{t['scenario_prefix']} {n[0].lower() + n[1:]}"}
    return {
     "config": {"step": {"user": {"title": t["title"], "description": t["user_desc"], "data": data, "data_description": desc}},
                "error": t["errors"], "abort": {"already_configured": t["abort"]}},
     "options": {"step": {
        "init": {"title": t["title"], "menu_options": t["menu"]},
        "counters": {"title": t["counters_title"], "description": t["counters_desc"], "data": data, "data_description": desc},
        "contract": {"title": t["contract_title"], "data": {"contract": t["contract_field"]}},
        "prices": {"title": t["prices_title"], "description": t["prices_desc"], "data": t["price_fields"]}},
        "error": t["errors"]},
     "entity": {"sensor": sensors, "select": {k: {"name": v} for k, v in t["selects"].items()}},
     "issues": {**{k: {"title": v[0], "description": v[1]} for k, v in t["issues"].items()},
                "price_proposal": {"title": t["proposal"]["title"], "description": t["proposal"]["description"],
                  "fix_flow": {"step": {"init": {"title": t["proposal"]["step_title"], "description": t["proposal"]["step_desc"],
                                                 "menu_options": {"accept": t["proposal"]["accept"], "reject": t["proposal"]["reject"]}}},
                               "abort": {"proposal_gone": t["proposal"]["gone"], "proposal_invalid": t["proposal"]["invalid"]}}}},
     "services": {k: {"name": v[0], "description": v[1], "fields": {f: {"name": a, "description": b} for f, (a, b) in v[2].items()}}
                  for k, v in t["services"].items()},
    }

for lang, t in L.items():
    out = json.dumps(build(t), indent=2, ensure_ascii=False) + "\n"
    (root / "translations" / f"{lang}.json").write_text(out, encoding="utf-8")
    if lang == "en":
        (root / "strings.json").write_text(out, encoding="utf-8")
print("ok")
