# How-to Guides

These guides answer practical questions after you understand the basic workflow in [Tutorial](tutorial.md).

## 1. Run the Pipeline on Your Own Data

Prepare two input tables:

- `stops.csv`: one row per physical individual stop.
- `route_stops.csv`: one row per route stop sequence entry.

Minimum schema:

```text
stops.csv
  stop_id, name, lat, lon

route_stops.csv
  route_id, seq, stop_id
```

Run:

```bash
python -m bustosubway run \
  --stops stops.csv \
  --routes route_stops.csv \
  --k-max 6 \
  --output outputs/
```

Quick sanity checks:

- `stop_id` must be unique in `stops.csv`.
- `(route_id, seq)` should be unique in `route_stops.csv`.
- `seq` should increase along a single route direction.
- If the graph has implausibly long edges, check whether outbound and inbound trips were merged.

## 2. Tune StopPlace Grouping

StopPlace grouping controls what counts as one logical station. Start conservatively.

```bash
python -m bustosubway group \
  --stops stops.csv \
  --eps 40 \
  --name-sim 0.85 \
  --output stopplace_map.csv
```

Useful review cases:

- Same-name stops across a normal road should often group.
- Same-name stops several hundred meters apart usually should not group.
- Different-name stops near the same intersection should be reviewed rather than automatically merged.
- Large terminals may need manual overrides because names and distance alone under-describe the facility.

Parameter guidance:

| parameter | effect |
|---|---|
| `eps` | spatial candidate radius in meters |
| `name_sim` | minimum name similarity for automatic grouping |
| manual override table | explicit corrections for cases the automatic rule should not decide |

## 3. Change the Dominance Metric

The lifetime calculation can use any node score, not only degree.

```python
from bustosubway import build_lspace, lifetime_sweep
import networkx as nx

G = build_lspace(stops, route_stops)
score = nx.betweenness_centrality(G)
lifetime = lifetime_sweep(G, score, k_max=6)
```

Interpret the result according to the score you choose. Degree-based lifetime identifies places that remain locally dominant by branching structure. Betweenness-based lifetime asks a different question and may promote corridor nodes because many shortest paths share the same road.

## 4. Tune `k_max` and Tie Handling

Use `k_max` as the largest neighborhood radius you want to inspect.

- If many important nodes have `lifetime == k_max`, increase `k_max`.
- If almost every node has lifetime 0, check whether strict tie handling is too harsh for a grid-like network.
- If you allow ties with `>=`, document it. Equal-degree neighboring stops can keep each other alive longer than strict comparison would.

Example:

```bash
python -m bustosubway run \
  --stops stops.csv \
  --routes route_stops.csv \
  --k-max 10 \
  --tie ge \
  --output outputs/
```

## 5. Export Results for Mapping

To inspect results in QGIS, Kepler.gl, or another map tool:

```bash
python -m bustosubway export \
  --hubs outputs/hubs.csv \
  --format geojson \
  --output outputs/hubs.geojson
```

Suggested visual encodings:

- color by `lifetime`,
- size by `degree`,
- filter to top N by `rank_lifetime`,
- compare with official transfer-center or terminal locations.

## 6. Compare Against Official Hubs

Use official transfer-center or terminal lists as face-validity checks, not as absolute truth.

1. Geocode or collect official hub coordinates.
2. Match them to extracted StopPlaces within a review radius such as 150m.
3. Compute precision@N or recall against top lifetime-ranked places.
4. Inspect disagreements manually.

Disagreement can be useful:

- official hub, low structural score: possibly policy-driven or demand-driven rather than topology-driven;
- high structural score, no official designation: possible candidate for further planning review;
- high degree, low lifetime: likely corridor stop rather than interchange.

## 7. Reproduce Publication Screenshots

For stable screenshots, save the view state:

- input data version,
- grouping parameters,
- `k_max`,
- tie rule,
- map center and zoom,
- selected metric and filters.

Then rerun the pipeline with the same parameters and open the explorer with that output directory.
