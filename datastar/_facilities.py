"""What each building runs, how fast, and at what cost — read once, for s2a, s2b and s2c.

A building is TWO files in inputs/factors_assumed/:

    sites.csv           one row per building: its name, role, coordinate, and `capacity_ea` (the
                        stated ops throughput, depots only). Was also pud_capacity.csv.
    site_machines.csv   one row per (building, machine). Was site_sorters.csv.

machine_rates.csv stays the CATALOGUE: every machine's default rate_hr, unit_cost and fixed_yr.
A site_machines.csv row does two things:

  1. A SORT_AUTO_* row INSTALLS that sorter at the site — it is the only way a building gets an
     automatic sorter, so it must carry a rate_hr (the catalogue leaves those blank on purpose).
  2. ANY row LOCALISES the machine at that site: a non-blank rate_hr, unit_cost or fixed_yr
     replaces the catalogue value there and nowhere else. A blank means "use the catalogue".
     So a small sorter at Avalon can run slower and cost more per parcel than the same model
     at a hub, and a row for UNLOAD_HAND at Bayswater changes Bayswater's hand unload only.

What a building RUNS is still decided by its role (hubs get the docks and a manual sort, a
depot with a sorter gets round-0, and so on). A localising row for a machine the site does not
run changes nothing, and `unused()` says so rather than failing — the role may change later.

The three build steps used to read these files three different ways. Every lookup now goes
through `rate` / `unit_cost` / `fixed_yr`, so a new column cannot reach one entity and not
the other.
"""
import pandas as pd

AUTO_SORTER = "SORT_AUTO_"          # a row naming one of these installs it
FIELDS = ("rate_hr", "unit_cost", "fixed_yr")


class Machines:
    def __init__(self, fass):
        cat = pd.read_csv(fass / "machine_rates.csv")
        self.catalogue = {r.machine: {f: getattr(r, f) for f in FIELDS} for r in cat.itertuples()}
        rows = pd.read_csv(fass / "site_machines.csv")
        nodes = set(pd.read_csv(fass / "sites.csv").node)
        assert not rows.duplicated(["site", "machine"]).any(), (
            "site_machines.csv: the same (site, machine) twice — "
            f"{rows[rows.duplicated(['site', 'machine'], keep=False)][['site', 'machine']].values.tolist()}")
        assert set(rows.site) <= nodes, (
            f"site_machines.csv names {sorted(set(rows.site) - nodes)}, which sites.csv does not have")
        assert set(rows.machine) <= set(self.catalogue), (
            f"site_machines.csv: unknown machine {sorted(set(rows.machine) - set(self.catalogue))} "
            f"— add it to machine_rates.csv first")
        # (site, machine) -> {field: local value}, blanks left out so they fall through
        self.local = {(r.site, r.machine): {f: getattr(r, f) for f in FIELDS
                                            if pd.notna(getattr(r, f))}
                      for r in rows.itertuples()}
        # site -> {sorter: rate}, in file order: s2b's round-0 sort takes a depot's FIRST sorter
        self.sorters = {}
        for r in rows.itertuples():
            if r.machine.startswith(AUTO_SORTER):
                assert "rate_hr" in self.local[(r.site, r.machine)], (
                    f"site_machines.csv: {r.site} installs {r.machine} with no rate_hr — an "
                    f"automatic sorter's rate is always the site's own")
                self.sorters.setdefault(r.site, {})[r.machine] = self.rate(r.site, r.machine)

    def _get(self, site, machine, field):
        v = self.local.get((site, machine), {}).get(field)
        return self.catalogue[machine][field] if v is None else v

    def rate(self, site, machine):
        """EA per hour at this site, or None where neither the site nor the catalogue gives one."""
        v = self._get(site, machine, "rate_hr")
        return None if pd.isna(v) else int(v)

    def unit_cost(self, site, machine):
        return float(self._get(site, machine, "unit_cost"))

    def fixed_yr(self, site, machine):
        return int(self._get(site, machine, "fixed_yr"))

    def localised(self):
        """(site, machine, field, catalogue value, local value) for every override that differs."""
        return [(s, m, f, self.catalogue[m][f], v)
                for (s, m), d in sorted(self.local.items()) for f, v in d.items()
                if not (m.startswith(AUTO_SORTER) and f == "rate_hr")
                and not (pd.notna(self.catalogue[m][f]) and float(self.catalogue[m][f]) == float(v))]

    def unused(self, runs):
        """Rows for a machine the site does not run. `runs` is {site: [machine, ...]}."""
        return sorted((s, m) for (s, m) in self.local if m not in runs.get(s, ()))


def ops_capacity(fass):
    """{depot: stated ops throughput, EA/day} — sites.csv `capacity_ea`, blank on non-depots."""
    s = pd.read_csv(fass / "sites.csv")
    return {r.node: int(r.capacity_ea) for r in s.itertuples() if pd.notna(r.capacity_ea)}
