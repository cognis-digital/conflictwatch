# Cognis interop map

How **conflictwatch** fits the wider Cognis suite — open-source situational awareness that
composes with the maritime, drone, and threat-intel tools, all on your own hardware.

```mermaid
graph LR
  CW[conflictwatch]:::hub
  MI[maritimeint]; UL[uaslog]; DW[awesome-drone-warfare-osint]; GL[geolens]
  CC[cognis-connect]; EM[edgemesh]; HM[humind]; AL[agentlex]
  CW -- events --> CC --> P[STIX/MISP/Sigma/SIEM/Slack]
  CW -- imagery refs --> GL
  MI -- maritime events --> CW
  UL -- counter-UAS detections --> CW
  DW -- platform context --> CW
  CW -- "/v1 brief" --> EM
  CW -- findings narrated by --> HM --> AL
  classDef hub fill:#6b46c1,color:#fff;
```

## Key edges

| from | relation | to |
|---|---|---|
| conflictwatch events | normalized + forwarded by | [`cognis-connect`](https://github.com/cognis-digital/cognis-connect) |
| [`maritimeint`](https://github.com/cognis-digital/maritimeint) / [`uaslog`](https://github.com/cognis-digital/uaslog) | domain events feed | conflictwatch situational picture |
| [`awesome-drone-warfare-osint`](https://github.com/cognis-digital/awesome-drone-warfare-osint) | platform/component context for | conflictwatch drone/uas events |
| [`geolens`](https://github.com/cognis-digital/geolens) | geolocates imagery referenced in | conflictwatch OSINT |
| conflictwatch | analyst brief via `/v1` | [`edgemesh`](https://github.com/cognis-digital/edgemesh) |

## Composition patterns

```bash
# conflict events -> STIX bundle for your TIP
conflictwatch ingest --source acled --from-file acled.csv | python -m conflictwatch.connect --to stix
# maritime + land in one picture: merge maritimeint + conflictwatch JSON, then analyze
# OSINT feeds -> Slack situational channel
conflictwatch scrape | python -m conflictwatch.connect --to slack --url $SLACK --dry-run
```

> Part of the cross-repo interop pass. **300+ tools →** [github.com/cognis-digital](https://github.com/cognis-digital)
