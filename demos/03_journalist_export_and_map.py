"""Scenario 3 - journalists & researchers: turn events into shareable artifacts.

A reporter or academic does not want a bespoke tool - they want the data in the
formats their existing tools already eat: a map layer and a citable structured
record. `conflictwatch.intel` exports either with zero dependencies:

  * GeoJSON  - every geolocated event as a point for Leaflet / Mapbox / QGIS / kepler.gl
  * STIX 2.1 - a valid bundle (location + observed-data + note per event, grouped
               in a report) that drops into OpenCTI and other TIPs

This demo ingests the ACLED-shaped export, summarizes the geocoding, and emits
both formats, showing exactly what lands on the map and in the record. Offline,
deterministic ids - re-running produces byte-identical STIX.
"""
import json

from _common import load_acled, rule

from conflictwatch import intel


def main() -> None:
    rule("JOURNALIST / RESEARCHER  -  export the picture to map + structured record")

    events = load_acled()
    geocoded = [e for e in events if e.lat is not None and e.lon is not None]
    print(f"\n{len(events)} events ingested; {len(geocoded)} are geolocated and mappable.")

    # --- GeoJSON: a map layer --------------------------------------------- #
    gj = json.loads(intel.to_geojson(events))
    print(f"\nGeoJSON FeatureCollection -> {len(gj['features'])} point feature(s):")
    for f in gj["features"][:4]:
        lon, lat = f["geometry"]["coordinates"]
        p = f["properties"]
        print(f"    ({lat:>6.2f},{lon:>6.2f})  {p['date']}  {p['event_type']:<18} "
              f"{p['location']}  fatalities={p['fatalities']}")
    print("    -> drop this straight onto Leaflet / Mapbox / QGIS / kepler.gl")

    # --- STIX 2.1: a citable structured record ---------------------------- #
    bundle = json.loads(intel.to_stix(events))
    kinds = {}
    for obj in bundle["objects"]:
        kinds[obj["type"]] = kinds.get(obj["type"], 0) + 1
    print(f"\nSTIX 2.1 bundle -> {len(bundle['objects'])} objects: {kinds}")
    note = next(o for o in bundle["objects"] if o["type"] == "note")
    print("    sample note content:")
    print(f"      {note['content']}")
    print("    -> ingestible by OpenCTI / MISP / any STIX 2.1 TIP; ids are deterministic")

    print("\nNo API keys, no dependencies - the story's data in the reader's own tools.")


if __name__ == "__main__":
    main()
