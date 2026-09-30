"""The routing runs' own distance and time for a cluster's van — read once, by every lane builder.

A delivery zone (chain 2) and a pickup cluster (chain 1) are each ONE van route in the routing
run that made them — 1,046 delivery routes, 931 pickup routes, one per cluster — and the run
writes what that van does in cluster_summary.csv:

    stem_km / stem_min                          depot -> cluster -> depot, ROAD, ROUND TRIP
    intra_cluster_km / intra_cluster_total_min  stop to stop inside the cluster, incl. service
    total_km   = stem_km + intra_cluster_km                         (to 0.1 km, every cluster)
    total_hours = (stem_min + intra_cluster_total_min + depot_min) / 60

WHY THE LANE NEEDS IT. A lane used to carry the one-way STRAIGHT LINE from the building to the
cluster's centroid. The stem is a round trip on roads — median 2.4x that line — and on the
delivery side the stem is only 43% of the km a van drives. The rest is the stops. So the old lane
held roughly a fifth of a delivery van's day and none of its hours.

WHAT A LANE GETS (basis `route`):

    transportdistance  stem_km + intra_cluster_km      the van's whole route, km
    transporttime      stem_min + intra_cluster_total_min, in HR — driving and stops. The run's
                       fixed 90-minute load at the building (`depot_min` / `facility_min`) is a
                       building activity, not the lane's, and is left out.

THE STEM DEPENDS ON THE BUILDING; THE STOPS DO NOT. When the lane starts at the building the run
routed from (within MATCH_KM), both halves are the run's own. When it starts ANYWHERE ELSE — a
zone served from another PDC, or a transport service's truck going straight to a hub —
the run never drove it, so the stem is estimated from the straight line to the centroid at the
run's own median road factor (km and minutes per straight-line km, calibrated per run). The
cluster's stops are its own either way.

KEYS. A cluster is (node, k): the model building that owns it and its number within that
building — CZ_PUD_Tullamarine_10 is (PUD_Tullamarine, 10), SUP_PKP_WEST_C27 is
(PUD_Sunshine_West, 27). The delivery run names its buildings by sites.csv `depot_id`
(`Depot_10`), the pickup run by `van_arm` (`Sunshine West Van Services`), exactly as s2a and s2b
already read them.
"""
import math

import pandas as pd

MATCH_KM = 0.5          # a lane from within this of the run's building IS the run's route
BASES = ("route", "haversine")

# (run folder kind) -> (building column in temp_clustered.csv, sites.csv column it matches,
#                       the run's building coordinate columns)
_KIND = {"delivery": ("facility_id", "depot_id", "depot_lat", "depot_lon"),
         "pickup": ("Facility Name", "van_arm", "facility_lat", "facility_lon")}


def haversine(a, b):
    """s2a's `_km`, verbatim, so a `haversine` lane is byte-identical to the old one."""
    (la1, lo1), (la2, lo2) = a, b
    dphi, dlmb = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dphi/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlmb/2)**2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class Routes:
    """One routing run's clusters. `lane(key, lat, lon)` -> (km, hours) from that point."""

    def __init__(self, folder, sites, kind):
        bcol, scol, latc, lonc = _KIND[kind]
        self.kind, self.folder = kind, folder
        cs = pd.read_csv(folder / "cluster_summary.csv")
        assert cs.cluster_id.is_unique, f"{folder}/cluster_summary.csv: duplicate cluster_id"
        tc = pd.read_csv(folder / "temp_clustered.csv", usecols=["cluster_id", bcol, latc, lonc],
                         low_memory=False).drop_duplicates()
        assert tc.cluster_id.is_unique, f"{folder}: a cluster's stops name two buildings"
        node_of = dict(zip(sites[scol].dropna(), sites.node[sites[scol].notna()]))
        tc["node"] = tc[bcol].map(node_of)
        assert tc.node.notna().all(), (
            f"{folder} names buildings sites.csv `{scol}` does not have: "
            f"{sorted(set(tc[bcol]) - set(node_of))}")
        cs = cs.merge(tc, on="cluster_id", how="left", validate="1:1")
        assert cs.node.notna().all(), f"{folder}: clusters temp_clustered.csv never places"
        cs["k"] = cs.cluster_id.str.split("_").str[1].astype(int)
        assert not cs.duplicated(["node", "k"]).any(), f"{folder}: (building, k) not unique"

        self.r = {}
        ratios = []
        for x in cs.itertuples():
            home, cen = (getattr(x, latc), getattr(x, lonc)), (x.centroid_lat, x.centroid_lon)
            self.r[(x.node, x.k)] = dict(home=home, centroid=cen, stem_km=x.stem_km,
                                         stem_min=x.stem_min, intra_km=x.intra_cluster_km,
                                         intra_min=x.intra_cluster_total_min)
            h = haversine(home, cen)
            if h > MATCH_KM:
                ratios.append((x.stem_km / h, x.stem_min / h))
        rt = pd.DataFrame(ratios, columns=["km", "min"])
        # the road factor for a stem the run never drove: its own median, round trip
        self.km_per_line_km = float(rt.km.median())
        self.min_per_line_km = float(rt["min"].median())
        self.used = {"run": 0, "estimated": 0}

    def __contains__(self, key):
        return key in self.r

    def lane(self, key, lat, lon):
        """(km, hours) for the van serving cluster `key` from the point (lat, lon)."""
        r = self.r[key]
        if haversine(r["home"], (lat, lon)) <= MATCH_KM:
            stem_km, stem_min = r["stem_km"], r["stem_min"]
            self.used["run"] += 1
        else:
            line = haversine((lat, lon), r["centroid"])
            stem_km, stem_min = line * self.km_per_line_km, line * self.min_per_line_km
            self.used["estimated"] += 1
        return round(stem_km + r["intra_km"], 2), round((stem_min + r["intra_min"]) / 60, 3)

    def describe(self):
        return (f"{len(self.r):,} {self.kind} routes from {self.folder.name}/ — a stem the run "
                f"never drove is {self.km_per_line_km:.2f} road km and "
                f"{self.min_per_line_km:.2f} min per straight-line km (the run's median)")
