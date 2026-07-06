# Tutorial: From Raw Stops to First Hub Candidates

This tutorial shows the intended public workflow for BusToSubway. It is written for a small sample dataset with two tables:

- `stops.csv`: individual stop poles, one row per Quay.
- `route_stops.csv`: route-by-route stop order.

The exact command names may change while the package is being stabilized, but the workflow and outputs are the contract this public interface should satisfy.

## 1. Prepare the Input

Your stop table should contain at least:

| column | meaning |
|---|---|
| `stop_id` | unique stop-pole identifier |
| `name` | stop name |
| `lat`, `lon` | WGS84 coordinates |

Your route-stop table should contain at least:

| column | meaning |
|---|---|
| `route_id` | route-direction identifier |
| `seq` | stop order within that route direction |
| `stop_id` | foreign key into `stops.csv` |

Important: `route_id` should represent a single direction or pattern. If both directions are merged into one sequence, the graph will create false edges at the turnaround.

## 2. Build StopPlaces

The first layer groups Quay-level stops into logical places.

```bash
python -m bustosubway group \
  --stops data/sample/stops.csv \
  --eps 40 \
  --name-sim 0.85 \
  --output outputs/stopplace_map.csv
```

The output maps every `stop_id` to a `stopplace_id`. Review this file before trusting downstream hub results. In particular, check:

- same-name stops on opposite sides of a road,
- large intersections where four corners may or may not be one place,
- nearby stops with different names that may need manual review.

## 3. Build the L-space Graph

Using the StopPlace map, build a graph where:

- nodes are StopPlaces,
- edges connect consecutive stops on at least one route.

```bash
python -m bustosubway run \
  --stops data/sample/stops.csv \
  --routes data/sample/route_stops.csv \
  --stopplaces outputs/stopplace_map.csv \
  --k-max 6 \
  --output outputs/
```

The run should produce:

- `outputs/hubs.csv`
- `outputs/lspace_edges.csv`
- `outputs/stopplace_map.csv` if grouping is run as part of the pipeline

## 4. Read the Hub Table

The main table should contain columns like:

| column | meaning |
|---|---|
| `node_id` | StopPlace or stop identifier |
| `degree` | number of L-space neighbors |
| `dominance` | score used for local comparison |
| `lifetime` | largest ego radius where the node remains dominant |
| `rank_degree`, `rank_lifetime` | rankings by the two views |

A typical interpretation:

| place | degree | lifetime | reading |
|---|---:|---:|---|
| Major terminal | 8 | 6 | strong hub across a broad neighborhood |
| Corridor stop | 7 | 1 | busy locally, but dominated nearby |
| Local branch point | 4 | 3 | meaningful neighborhood-level junction |

Degree asks "how many adjacent directions are visible here?" Lifetime asks "how far does this dominance persist?"

## 5. Open the Explorer

```bash
streamlit run app.py -- --data outputs/
```

Use the explorer to compare views:

1. Sort or color by degree.
2. Switch to lifetime.
3. Increase the ego radius `k`.
4. Watch which high-degree corridor stops lose rank and which places remain dominant.

That visual transition is the point of the method. It makes the corridor/interchange distinction inspectable instead of hiding it inside a single score.

## Next Steps

- To run your own city data, see [How-to §1](how-to.md#1-run-the-pipeline-on-your-own-data).
- To tune grouping and lifetime parameters, see [How-to §2-4](how-to.md#2-tune-stopplace-grouping).
- For formal definitions, see [Reference](reference.md).
